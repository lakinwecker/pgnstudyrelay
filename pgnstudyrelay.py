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
games = {}
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


def hacky_python_parsing_of_times(comment):
    if not "[%clk" in comment:
        return None
    comment = comment.replace("[%clk ", "")
    comment = comment.replace("]", "")
    parts = comment.split(":")
    assert len(parts) == 3
    h,m,s = [int(x) for x in parts]
    return (((h*60) + m)*60)+s

def game_key(game):
    white = "-".join(game.headers['White'].replace(",", "").split())
    black = "-".join(game.headers['Black'].replace(",", "").split())
    key = "{}-vs-{}".format(white.lower(), black.lower())
    return key

@gen.coroutine
def process_pgn(contents):
    handle = StringIO(contents)
    while True:
        new_game = chess.pgn.read_game(handle)
        if new_game is None:
            break
        key = game_key(new_game)
        new_game.key = key
        if key not in games:
            games[key] = new_game
            print("inserting {}".format(key))
            yield send_to_study_socket(lichess.add_study_chapter_message(name="Chapter 1", pgn=str(new_game)))
            # TODO: This needs to be updated to create/find the chapter and then ensure it's up to date.
            yield sync_with_study()
            # BROADCAST TO CLIENTS OF NEW GAME
            continue

        old_game = games[key]
        old_node = old_game.variations[0]
        new_node = new_game.variations[0]
        while True:
            if old_node.move.uci() != new_node.move.uci():
                print("Corruption! Restart game: {}".format(key))
                continue
            if old_node.is_end() or new_node.is_end():
                break
            old_node = old_node.variations[0]
            new_node = new_node.variations[0]
        if old_node.is_end() and new_node.is_end():
            # print("No new moves for {}".format(key))
            continue
        if not old_node.is_end() and new_node.is_end():
            print(old_game, new_game)
            print(old_node, new_node)
            print("Corruption! old game is longer than new game!? {}".format(key))
            # TODO: What do we do here!?
            continue
        old_node = new_node
        new_node = new_node.variations[0]
        chapter = chapter_lookup[key]
        while True:
            print("New move in {}: {}".format(key, new_node.move.uci()))
            # TODO: This message needs to be updated
            #send(lichess.move_message(new_node, type="fen"))
            message = lichess.add_move_to_study(new_node, old_node, chapter, tree_parts_lookup[key])
            yield send_to_study_socket(message)
            yield sync_with_study(chapter_id=chapter['id'])
            if new_node.is_end():
                break
            old_node = new_node
            new_node = new_node.variations[0]
        games[key] = new_game


def send(message):
    pass

@gen.coroutine
def login():
    global cookie
    global headers
    client = httpclient.AsyncHTTPClient()
    url = "https://lichess.org/login"
    params = {"username": username, "password": password}
    login_request = httpclient.HTTPRequest(url, method="POST", headers=headers, body=urlencode(params))
    response = yield client.fetch(login_request)
    print("Logged in")
    cookie = cookies.SimpleCookie()
    cookie.load(response.headers['Set-Cookie'])
    headers['Cookie'] = cookie['lila2'].OutputString()

    url = "https://lichess.org/account/info"
    account_info_request = httpclient.HTTPRequest(url, method="GET", headers=headers)
    response = yield client.fetch(account_info_request)
    yield connect_to_study()

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
    study_url = "wss://socket.lichess.org:9028/study/{}/socket/v2?sri={}".format(
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
        print(msg)

@gen.coroutine
def get_json(url):
    client = httpclient.AsyncHTTPClient()
    study_info_request = httpclient.HTTPRequest(url, method="GET", headers=headers)
    response = yield client.fetch(study_info_request)
    return json.loads(response.body.decode('utf-8'))

@gen.coroutine
def get_study_data():
    url = "https://lichess.org/study/{}?_={}".format(study, time.time())
    json = yield get_json(url)
    return json

@gen.coroutine
def get_chapter_data(chapter_id):
    url = "https://lichess.org/study/{}/{}?_={}".format(study, chapter_id, time.time())
    json = yield get_json(url)
    return json

@gen.coroutine
def sync_with_study(chapter_id=None):
    study_data = yield get_study_data()
    for chapter in study_data['study'].get('chapters', []):
        if chapter_id is not None and chapter['id'] != chapter_id:
            continue
        chapter_data = yield get_chapter_data(chapter['id'])
        tags = {}
        for tag_name, tag_value in chapter_data['study']['chapter']['tags']:
            tags[tag_name] = tag_value
        print(tags)
        if not any([
            tags.get('White'), tags.get('Black'),
            tags.get('WhiteElo'), tags.get('BlackElo')
        ]):
            print("skipping chapter: {}".format(chapter['name']))
            continue

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
        new_game = chess.pgn.read_game(StringIO(pgn))
        key = game_key(new_game)
        new_game.key = key
        games[key] = new_game
        chapter_lookup[key] = chapter
        tree_parts_lookup[key] = chapter_data['analysis']['treeParts']
        print("Updating game {} from study info".format(key))

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
        yield gen.sleep(0.5)

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
    yield sync_with_study()
    yield [poll(), listen_to_study()]

if __name__ == '__main__':
    main()
ioloop.IOLoop.instance().start()
