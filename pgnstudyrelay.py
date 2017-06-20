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

import pprint
import chess
import chess.pgn
import glob
from io import StringIO
import json
import random
import string
from tornado import gen
from tornado import websocket, web, ioloop, httpclient
from http import cookies
from urllib.parse import urlencode
import time
import lichess


# everyone loves global mutable state, right?  Right?  
# ...
# ...
# No one?
# ...
# ...
# damn
game_lookup = {}
chapter_lookup = {}
tree_parts_lookup = {}
headers = {
    'Accept': 'application/vnd.lichess.v2+json',
}
cookie = None
study_socket = None
pgn_source_url = None
username = None
password = None
study = None
url = None
http_url = "https://listage.ovh"
ws_url = "wss://socket.listage.ovh"

def game_key_from_tags(tags):
    white = "-".join(tags['White'].replace(",", "").split())
    black = "-".join(tags['Black'].replace(",", "").split())
    key = "{}-vs-{}".format(white.lower(), black.lower())
    return key

def game_key_from_game(game):
    return game_key_from_tags(game.headers)

def game_key_from_chapter(chapter):
    tags = dict(chapter['study']['chapter']['tags'])
    return game_key_from_tags(tags)

@gen.coroutine
def process_pgn(contents):
    try:
        handle = StringIO(contents)
        while True:
            new_game = chess.pgn.read_game(handle)
            if new_game is None:
                break
            key = game_key_from_game(new_game)
            new_game.key = key
            if key not in game_lookup:
                game_lookup[key] = new_game
            else:
                old_game = game_lookup[key]
                if str(old_game) == str(new_game):
                    continue
                game_lookup[key] = new_game

            if key not in chapter_lookup:
                print("inserting {}".format(key))
                yield send_to_study_socket(lichess.add_study_chapter_message(name="Chapter 1", pgn=str(new_game)))
                yield sync_with_study() # TODO: ideally this would be sync_with_chapter.
                continue

            chapter = chapter_lookup[key]
            # Make sure we're up to date
            yield sync_chapter(chapter_id=chapter['id'])
            chapter = chapter_lookup[key]
            tree_parts = chapter['analysis']['treeParts']
            total_ply = len(tree_parts)
            tree_index = 1
            tree_node = tree_parts[tree_index]
            path = ""
            cur_node = new_game.variations[0]
            prev_node = None

            more_data_incoming = False

            while True:
                if tree_node['san'] != cur_node.san():
                    print("Difference in move sans!")
                    more_data_incoming = True
                    break
                if cur_node.is_end():
                    path += tree_node['id']
                    print("End of incoming data")
                    break

                if tree_index+1 == total_ply:
                    print("End of chapter")
                    more_data_incoming = True
                    break

                path += tree_node['id']
                tree_index += 1
                tree_node = tree_parts[tree_index]
                prev_node = cur_node
                cur_node = cur_node.variations[0]

            while more_data_incoming:
                print("New move in {}: {}".format(key, cur_node.move.uci()))
                message = lichess.add_move_to_study(cur_node, prev_node, chapter['id'], path)
                yield send_to_study_socket(message)
                yield sync_chapter(chapter_id=chapter['id'])
                chapter = chapter_lookup[key]
                tree_parts = chapter['analysis']['treeParts']
                new_tree_node = tree_parts[tree_index]
                tree_index += 1
                if new_tree_node['uci'] != cur_node.move.uci():
                    print("Adding new node failed")
                    return
                path += new_tree_node['id']

                if cur_node.is_end():
                    break

                prev_node = cur_node
                cur_node = cur_node.variations[0]

            incoming_result = new_game.headers['Result']
            if incoming_result != "*":
                if chapter['tags']['Result'] != incoming_result and cur_node.is_end():
                    yield send_to_study_socket(lichess.set_tag(chapter['id'], 'Result', new_game.headers['Result']))
                    yield send_to_study_socket(lichess.set_comment(chapter['id'], path, "Game ended in: {}".format(incoming_result)))
    except:
        import traceback
        print(traceback.format_exc())

@gen.coroutine
def login():
    global cookie
    global headers
    client = httpclient.AsyncHTTPClient()
    url = "{}/login".format(http_url)
    params = {"username": username, "password": password}
    login_request = httpclient.HTTPRequest(url, method="POST", headers=headers, body=urlencode(params))
    response = yield client.fetch(login_request)
    print("Logged in")
    cookie = cookies.SimpleCookie()
    cookie.load(response.headers['Set-Cookie'])
    headers['Cookie'] = cookie['lila2'].OutputString()

    url = "{}/account/info".format(http_url)
    account_info_request = httpclient.HTTPRequest(url, method="GET", headers=headers)
    response = yield client.fetch(account_info_request)
    print("Got account info")

@gen.coroutine
def send_to_study_socket(message):
    global study_socket
    msg = json.dumps(message)
    print(msg)
    yield study_socket.write_message(msg)


{"t":"changeChapter","d":{"p":{"chapterId":"dsVeR14T","path":""},"w":{"u":"tournament-relay","s":"JQMIkvjLKS"}},"v":59}

@gen.coroutine
def connect_to_study():
    global study_socket
    sri = "".join([random.choice(string.ascii_letters) for x in range(10)])
    study_url = "{}/study/{}/socket/v2?sri={}".format(
        ws_url,
        study,
        sri
    )
    socket_request = httpclient.HTTPRequest(study_url, headers=headers)
    study_socket = yield websocket.websocket_connect(socket_request)
    print("Connected to study!")

@gen.coroutine
def listen_to_study():
    global study_socket
    while True:
        msg = yield study_socket.read_message()
        if msg is None: break
        print("Recieved message: ")
        print(msg)

@gen.coroutine
def get_json(url):
    client = httpclient.AsyncHTTPClient()
    study_info_request = httpclient.HTTPRequest(url, method="GET", headers=headers)
    response = yield client.fetch(study_info_request)
    return json.loads(response.body.decode('utf-8'))

@gen.coroutine
def get_study_data():
    url = "{}/study/{}?_={}".format(http_url, study, time.time())
    json = yield get_json(url)
    return json

@gen.coroutine
def get_chapter_data(chapter_id):
    url = "{}/study/{}/{}?_={}".format(http_url, study, chapter_id, time.time())
    json = yield get_json(url)
    return json

@gen.coroutine
def sync_chapter(chapter_id=None):
    chapter_data = yield get_chapter_data(chapter_id)
    tags = {}
    for tag_name, tag_value in chapter_data['study']['chapter']['tags']:
        tags[tag_name] = tag_value

    chapter_data['tags'] = tags
    if not any([
        tags.get('White'), tags.get('Black'),
        tags.get('WhiteElo'), tags.get('BlackElo')
    ]):
        print("skipping chapter: {}".format(chapter_id))
        return

    pgn = ""
    for tag_name, tag_value in chapter_data['study']['chapter']['tags']:
        tags[tag_name] = tag_value
        pgn += '[{} "{}"]\n'.format(tag_name, tag_value)
    pgn += "\n"
    moves = ""
    for move in chapter_data['analysis']['treeParts'][1:]:
        ply = int(move['ply'])
        turn = "" if ply % 2 == 0 else "{}. ".format((ply+1) // 2)
        moves += '{}{} '.format(turn, move['san'])
        if len(moves) > 70:
            moves += "\n"
            pgn += moves
            moves = ""
    pgn += moves
    pgn += " {}".format(tags['Result'])
    key = game_key_from_chapter(chapter_data)
    chapter_data['id'] = chapter_id
    chapter_lookup[key] = chapter_data
    tree_parts_lookup[key] = chapter_data['analysis']['treeParts']
    print("Updating game {} from study info".format(key))

@gen.coroutine
def sync_with_study(chapter_id=None):
    print("Syncing with study")
    study_data = yield get_study_data()
    for chapter in study_data['study'].get('chapters', []):
        yield sync_chapter(chapter['id'])

@gen.coroutine
def update_pgns():
    while True:
        client = httpclient.AsyncHTTPClient()
        url = "{}?v={}".format(pgn_source_url, time.time())
        print(".", end="", flush=True)
        response = yield client.fetch(url)
        yield process_pgn(response.body.decode("ISO-8859-1")) # TODO: pull this from the content-type
        yield gen.sleep(1)

already_processed = []
@gen.coroutine
def poll_files():
    files = sorted(glob.glob("./local-files/*.pgn"))
    for file in files:
        if file in already_processed:
            continue
        print("Processing {}!".format(file))
        already_processed.append(file)
        contents = open(file, "r").read()
        yield process_pgn(contents)
        yield gen.sleep(1)

@gen.coroutine
def main():
    global username
    global password
    global study
    global url
    import sys
    poll = None
    if len(sys.argv) < len(['username', 'password', 'study']):
        sys.exit("Usage: pgnstudyrelay.py <username> <password> <study> [<url>]")
    if len(sys.argv) == len(['_', 'username', 'password', 'study', 'url']):
        _, username, password, study, url = sys.argv
        pgn_source_url = url
        poll = update_pgns
    else:
        _, username, password, study = sys.argv
        poll = poll_files
    yield login()
    yield connect_to_study()
    yield sync_with_study()
    yield [poll(), listen_to_study()]

if __name__ == '__main__':
    main()
ioloop.IOLoop.instance().start()
