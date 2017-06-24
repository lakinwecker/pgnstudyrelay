#!/usr/bin/python3

# pgnstudyrelay - Relay moves from a PGN feed into a lichess study
#
# Copyright (C) 2017 Lakin Wecker <lakin@wecker.ca>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.  
'''
@gen.coroutine
def update_pgns(time_to_delay):
    while True:
        client = httpclient.AsyncHTTPClient()
        url = "{}?v={}".format(pgn_source_url, time.time())
        print(".", end="", flush=True)
        response = yield client.fetch(url)
        yield process_pgn(response.body.decode("ISO-8859-1")) # TODO: pull this from the content-type
        yield gen.sleep(time_to_delay)

@gen.coroutine
def main():

    url = args.url
    poll = None
    if url.startswith('http://') or url.startswith('https://'):
        pgn_source_url = url
        print("Polling URL: {}".format(pgn_source_url))
        poll = update_pgns
    else:
        pgn_source_directory = url
        print("Polling directory: {}".format(pgn_source_directory))
        poll = poll_files
    yield login()
    yield connect_to_study()
    yield sync_with_study()
    yield [poll(args.poll_delay), listen_to_study()]

if __name__ == '__main__':
    main()
ioloop.IOLoop.instance().start()
'''

import argparse
import asyncio
import aiohttp
import chess
import chess.pgn
import glob
from io import StringIO
from urllib.parse import urlparse

from collections import defaultdict

from lichess import (
    move_to_path_id,
    Lichess,
    LoginError,
    StudyConnectionError,
    StudyNotAContributor,
)

def game_key_from_tags(tags):
    white = "-".join(tags.get('White', '').replace(",", "").split())
    black = "-".join(tags.get('Black', '').replace(",", "").split())
    if not white or not black:
        return ''
    key = "{}-vs-{}".format(white.lower(), black.lower())
    return key

def game_key_from_game(game):
    return game_key_from_tags(game.headers)

def game_key_from_chapter(chapter):
    tags = dict(chapter['study']['chapter']['tags'])
    return game_key_from_tags(tags)

def game_title_from_tags(tags):
    white = tags.get('White', '').split(", ")[0]
    black = tags.get('Black', '').split(", ")[0]
    if not white or not black:
        return ''
    key = "{} vs {}".format(white, black)
    return key

def game_title_from_game(game):
    return game_title_from_tags(game.headers)

class PGNStudyRelay:
    def __init__(self, study):
        self.study = study
        self.pgns_by_key = defaultdict(str)

    async def sync_with_pgn(self, contents):
        try:
            handle = StringIO(contents)
            while True:
                game = chess.pgn.read_game(handle)
                if game is None: break
                if len(game.variations) == 0: continue

                game.key = game_key_from_game(game)
                game.title = game_title_from_game(game)

                # Only process the PGN if it's different from last time
                old_game = self.pgns_by_key[game.key]
                if str(old_game) == str(game): continue
                self.pgns_by_key[game.key] = game

                chapter_lookup = {game_key_from_chapter(c): c for c in self.study.get_chapters()}
                chapter = chapter_lookup.get(game.key)

                if not chapter:
                    print("++ [SYNCING] inserting new chapter for: {}".format(game.title))
                    await self.study.create_chapter_from_pgn(str(game))
                    continue

                tree_parts = chapter['analysis']['treeParts']
                total_ply = len(tree_parts)

                more_data_incoming = False
                path = ""
                if not len(game.variations) > 0:
                    print("++ [SYNCING] No game data yet")
                    continue
                cur_node = game.variations[0]
                prev_node = game
                if total_ply > 1:
                    tree_index = 1
                    tree_node = tree_parts[tree_index]

                    while True:
                        #print(game.title, total_ply, tree_index)
                        #print(game.title, tree_node['san'], cur_node.san())
                        if tree_node['san'] != cur_node.san():
                            print("++ [SYNCING] Difference in move sans!")
                            more_data_incoming = True
                            break
                        if cur_node.is_end():
                            path += tree_node['id']
                            print("++ [SYNCING] End of incoming data")
                            break

                        if tree_index+1 == total_ply:
                            print("++ [SYNCING] End of chapter")
                            more_data_incoming = True
                            # TODO: figure this out
                            break

                        path += tree_node['id']
                        tree_index += 1
                        tree_node = tree_parts[tree_index]
                        prev_node = cur_node
                        cur_node = cur_node.variations[0]
                else:
                    more_data_incoming = True

                while more_data_incoming:
                    #print(game.title, cur_node.san())

                    # Ensure we're not out of sync with the latest data. If we are, bail out. We'll get 
                    # these moves when processing the next pgn
                    new_chapter = self.study.get_chapter(chapter['id'])
                    if new_chapter['version'] != chapter['version']:
                        print("++ [SYNCING] Chapter {} was updated while we were processing moves. Bailing out")
                        break

                    print("++ [SYNCING] New move in {}: {}".format(game.title, cur_node.move.uci()))
                    await self.study.add_move(chapter['id'], path, cur_node, prev_node)
                    move = prev_node.board()._to_chess960(cur_node.move)
                    path_part = move_to_path_id(move)
                    path += path_part
                    if cur_node.is_end():
                        break

                    prev_node = cur_node
                    cur_node = cur_node.variations[0]
                    await asyncio.sleep(0.5) # TODO:  This could be smarter.
                await self.study.sync_chapter(chapter['id'])

                incoming_result = game.headers['Result']
                if incoming_result != "*":
                    if chapter['tags']['Result'] != incoming_result and cur_node.is_end():
                        await self.study.set_tag(chapter['id'], 'Result', game.headers['Result'])
                        await self.study.set_move_comment(chapter['id'], path, "Game ended in: {}".format(incoming_result))
                        await self.study.talk("{} ended in: {}".format(game.title, incoming_result))
        except:
            import traceback
            print(traceback.format_exc())

async def poll_files(relay, directory, delay):
    files = sorted(glob.glob("{}/*.pgn".format(directory)))
    for file in files:
        print("++ [POLLING] {}".format(file))
        contents = open(file, "r").read()
        await relay.sync_with_pgn(contents)
        await asyncio.sleep(delay)

async def main(loop):
    async with aiohttp.ClientSession(loop=loop) as session:
        parser = argparse.ArgumentParser()
        parser.add_argument("username", help="A lichess username")
        parser.add_argument("password", help="The password for that username")
        parser.add_argument("study_url", help="The study URL where the moves should be relayed. NOTE: the user must have contributor access")
        parser.add_argument("url", help="A PGN url that will be polled, or a directory containing already polled PGN files.")
        parser.add_argument("--poll_delay", type=float, default=1, help="The time to wait (in seconds) between polling. Accepts floats")
        parser.add_argument("--log_ws", type=bool, default=False, help="Log websocket messages")
        args = parser.parse_args()

        username = args.username
        password = args.password

        study_url = args.study_url
        components = urlparse(study_url)
        base_url = "{}://{}/".format(components.scheme, components.netloc)
        lichess = Lichess(loop, session, base_url, log_ws=args.log_ws)
        try:
            await lichess.login(username, password)
        except LoginError:
            print("Unable to login to lichess successfully. Please check your credentials")
            return

        study_id = study_url.split("/")[-1]
        try:
            study = await lichess.study(study_id)
            study.ensure_contributor()
        except StudyConnectionError:
            print("Unable to connect to the study url that was provided. Are you sure this user can access it?")
            return
        except StudyNotAContributor:
            print("The provided user is not a contributor to the study.")
            return

        relay = PGNStudyRelay(study)

        url = args.url
        poll = None
        if url.startswith('http://') or url.startswith('https://'):
            print("Polling URL: {}".format(pgn_source_url))
            await update_pgns(url)
        else:
            print("++ [POLLING] processing {}".format(url))
            await poll_files(relay, url, args.poll_delay)

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main(loop))
