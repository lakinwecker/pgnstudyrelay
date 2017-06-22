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

from http import cookies
from io import StringIO
from tornado import gen
from tornado import websocket, web, ioloop, httpclient
from urllib.parse import urlencode
import chess
import chess.pgn
import glob
import json
import lichess
import pprint
import random
import string
import sys
import time


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
pgn_source_directory = None
username = None
password = None
study_id = None
study_url = None
url = None
http_url = None
ws_url = None

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

            more_data_incoming = False
            path = ""
            if not len(new_game.variations) > 0:
                print("No game data yet")
                continue
            cur_node = new_game.variations[0]
            prev_node = new_game
            if total_ply > 1:
                tree_index = 1
                tree_node = tree_parts[tree_index]

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
            else:
                more_data_incoming = True

            while more_data_incoming:
                print("New move in {}: {}".format(key, cur_node.move.uci()))
                message = lichess.add_move_to_study(cur_node, prev_node, chapter['id'], path)
                yield send_to_study_socket(message)
                path += lichess.move_to_path_id(cur_node.board()._to_chess960(cur_node.move))
                if cur_node.is_end():
                    break

                prev_node = cur_node
                cur_node = cur_node.variations[0]
                yield gen.sleep(0.5)

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


@gen.coroutine
def connect_to_study():
    global study_socket
    sri = "".join([random.choice(string.ascii_letters) for x in range(10)])
    study_url = "{}/study/{}/socket/v2?sri={}".format(
        ws_url,
        study_id,
        sri
    )
    socket_request = httpclient.HTTPRequest(study_url, headers=headers)
    study_socket = yield websocket.websocket_connect(socket_request)
    print("Connected to study!")

@gen.coroutine
def ping_study():
    global study_socket
    i = 0
    while True:
        i += 1
        try:
            yield send_to_study_socket({"t": "p"})
            yield gen.sleep(1)
        except:
            import traceback
            print(traceback.format_exc())

@gen.coroutine
def listen_to_study():
    global study_socket
    while True:
        try:
            msg = yield study_socket.read_message()
            if msg is None: continue
            print("------------------> Recieved message: ", msg)
        except:
            import traceback
            print(traceback.format_exc())

@gen.coroutine
def get_json(url):
    client = httpclient.AsyncHTTPClient()
    study_info_request = httpclient.HTTPRequest(url, method="GET", headers=headers)
    response = yield client.fetch(study_info_request)
    return json.loads(response.body.decode('utf-8'))

@gen.coroutine
def get_study_data():
    url = "{}/study/{}?_={}".format(http_url, study_id, time.time())
    json = yield get_json(url)
    return json

@gen.coroutine
def get_chapter_data(chapter_id):
    url = "{}/study/{}/{}?_={}".format(http_url, study_id, chapter_id, time.time())
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

    key = game_key_from_chapter(chapter_data)
    chapter_data['id'] = chapter_id
    chapter_lookup[key] = chapter_data
    tree_parts_lookup[key] = chapter_data['analysis']['treeParts']
    print("Updating game {} from study info".format(key))

@gen.coroutine
def sync_with_study(chapter_id=None):
    print("Syncing with study")
    study_data = yield get_study_data()
    members = study_data['study']['members']
    if username not in members or members[username]['role'] != 'w':
        sys.exit("{} is not a contributor of {}".format(username, study_url))
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
    files = sorted(glob.glob("{}/*.pgn".format(pgn_source_directory)))
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
    global study_id
    global study_url
    global url
    global pgn_source_url
    global http_url
    global ws_url

    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("username", help="A lichess username")
    parser.add_argument("password", help="The password for that username")
    parser.add_argument("study_url", help="The study URL where the moves should be relayed. NOTE: the user must have contributor access")
    parser.add_argument("url", help="A PGN url that will be polled, or a directory containing already polled PGN files.")
    args = parser.parse_args()


    username = args.username
    password = args.password


    study_url = args.study_url
    if study_url.startswith("https://lichess.org/study/"):
        http_url = "https://lichess.org"
        ws_url = "wss://socket.lichess.org"
        study_id = study_url.replace("https://lichess.org/study/", "")
    if study_url.startswith("https://listage.ovh/study/"):
        http_url = "https://listage.ovh"
        ws_url = "wss://socket.listage.ovh"
        study_id = study_url.replace("https://listage.ovh/study/", "")
    else:
        sys.exit("study_url must start with https://lichess.org/study/<studyId> or https://listage.ovh/study/<studyId>")

    url = args.url
    poll = None
    if url.startswith('http://') or url.startswith('https://'):
        pgn_source_url = url
        print("Polling URL!")
        poll = update_pgns
    else:
        pgn_source_directory = url
        poll = poll_files
    yield login()
    yield connect_to_study()
    yield sync_with_study()
    yield [poll(), listen_to_study(), ping_study()]

if __name__ == '__main__':
    main()
ioloop.IOLoop.instance().start()
