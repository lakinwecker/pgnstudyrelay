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

import argparse
import asyncio
import aiohttp
import chess
import chess.pgn
import glob
from io import StringIO
import time
from urllib.parse import urlparse
import os
import codecs

from collections import defaultdict

from lichess import (
    clock_from_comment,
    clock_from_seconds,
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
        self.chapter_versions_by_key = defaultdict(str)

    async def sync_with_pgn(self, contents):
        handle = StringIO(contents)
        chapters_created = False
        while True:
            game = chess.pgn.read_game(handle)
            if game is None: break

            game.key = game_key_from_game(game)
            game.title = game_title_from_game(game)

            chapter_lookup = {game_key_from_chapter(c): c for c in self.study.get_chapters()}
            chapter = chapter_lookup.get(game.key)

            if not chapter:
                print("++ [SYNCING] inserting new chapter for: {}".format(game.title))
                await self.study.create_chapter_from_pgn(str(game))
                chapters_created = True
                continue

            # Do some checks before we other syncing.
            should_sync = False
            old_version = self.chapter_versions_by_key[game.key]
            if old_version != chapter['version']:
                should_sync = True
            old_game = self.pgns_by_key[game.key]
            if str(old_game) != str(game):
                should_sync = True
            self.pgns_by_key[game.key] = game

            if not should_sync:
                continue

            # This could happen above, but then that delays the creation of the 
            # games when it first starts.
            if len(game.variations) == 0: continue

            has_new_moves = False

            tree_parts = chapter['analysis']['treeParts']
            tree_len = len(tree_parts)
            tree_index = 1
            path = ""
            prev_node = game
            cur_node = game.variations[0]
            if tree_len > 1:
                tree_node = tree_parts[tree_index]

                while True:
                    if tree_node['san'] != cur_node.san():
                        has_new_moves = True
                        break

                    #clock = clock_from_comment(cur_node.comment)
                    #tree_clock = clock_from_seconds(tree_node.get('clock', 0))
                    #if clock != tree_clock:
                        #print("{} vs {}->{}".format(clock, tree_node.get('clock'), tree_clock))
                        #has_new_moves = True
                        #break

                    # if we're at the end of the incoming moves we're done.
                    if cur_node.is_end(): break

                    # The moves were the same, update iterator for incoming moves
                    # and the path
                    path += tree_node['id']
                    prev_node = cur_node
                    cur_node = cur_node.variations[0]

                    # We're done with the chapter moves, but not the incoming moves
                    if tree_index+1 == tree_len:
                        has_new_moves = True
                        break

                    tree_index += 1
                    tree_node = tree_parts[tree_index]
            else:
                has_new_moves = True

            if has_new_moves:
                while True:
                    # Ensure we are in sync with the latest data.  If not, stop sending moves.
                    # We will get to these moves when processing the next pgn
                    new_chapter = self.study.get_chapter(chapter['id'])
                    if new_chapter['version'] != chapter['version']:
                        print("-- [SYNCING] Chapter {} updated while processing moves.".format(chapter['id']))
                        break

                    print("++ [SYNCING] New move in {}: {}".format(game.title, cur_node.move.uci()))
                    await self.study.add_move(chapter['id'], path, cur_node, prev_node)
                    path += move_to_path_id(prev_node.board()._to_chess960(cur_node.move))
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
                    await self.study.sync_chapter(chapter['id'])

        if chapters_created:
            # TODO: there has to be a better way to do this. But at the moment
            #       we are too fast. Wait a full 2seconds before continuing
            print("-- [SYNCING] Sleeping for 2 seconds to allow lila the opportunity to finish")
            await asyncio.sleep(2.0)
            print("-- [SYNCING] Syncing study because we created chapters")
            await self.study.sync()

async def poll_directory_of_files(relay, directory, delay):
    files = sorted(glob.glob("{}/*.pgn".format(directory)))
    for file in files:
        print("~~ [POLLING] {}".format(file))
        contents = open(file, "r").read()
        contents = contents[3:] if contents[0:3] == codecs.BOM_UTF8 else contents
        await relay.sync_with_pgn(contents)
        await asyncio.sleep(delay)

async def poll_local_file(relay, file, delay):
    while True:
        print("~~ [POLLING] {}".format(file))
        contents = open(file, "r").read()
        contents = contents[3:] if contents[0:3] == codecs.BOM_UTF8 else contents
        await relay.sync_with_pgn(contents)
        await asyncio.sleep(delay)

async def poll_url(relay, url, delay):
    async with aiohttp.ClientSession(loop=loop) as session:
        while True:
            url_with_buster = "{}?v={}".format(url, time.time())
            print("~~ [POLLING] {}".format(url_with_buster))
            response = await session.get(url_with_buster)
            body = await response.read()
            body = body[3:] if body[0:3] == codecs.BOM_UTF8 else body
            await relay.sync_with_pgn(body.decode("ISO-8859-1"))
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
            print("Polling URL: {}".format(url))
            await poll_url(relay, url, args.poll_delay)
        elif os.path.isdir(url):
            print("~~ [POLLING] processing {}".format(url))
            await poll_directory_of_files(relay, url, args.poll_delay)
        else:
            print("~~ [POLLING] processing {}".format(url))
            await poll_local_file(relay, url, args.poll_delay)

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main(loop))
