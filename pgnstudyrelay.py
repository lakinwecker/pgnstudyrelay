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


games = {}
subscriptions = []


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

def start_game_message(game):
    game_json = game_message(game)['d']
    return {
        "t": "fen",
        "d": {
            "id": game_json['game']['id'],
            "fen": game_json['game']['fen'],
            "lm": game_json['game']['lastMove'],
        }
    }
def game_message(game):
    last_node = game
    last_ply = 0
    moves = []
    while not last_node.is_end():
        last_node = last_node.variations[0]
        last_ply += 1
        moves.append(last_node)
    if last_node.board().turn == chess.WHITE:
        white_last_move = last_node
        black_last_move = last_node.parent
    else:
        black_last_move = last_node
        white_last_move = last_node.parent

    return {
        "t": "game",
        "d": {
            "game": {
                "id": game_key(game),
                "variant": {"key": "standard", "name": "standard", "short": "Std" },
                "speed": "classical",
                "perf": "classical",
                "rated": True,
                "initialFen": game.board().fen(),
                "fen": last_node.board().fen(),
                "turns": last_ply,
                "source": "norway-2017-arbiter",
                "lastMove": last_node.move.uci(),
                "opening": {
                    "eco": game.headers["ECO"],
                }
            },
            "clock": {
                "running": True,
                "initial": 6000,
                "increment": 0, # lying - but I don't think lichess implements the style of increment
                "white": hacky_python_parsing_of_times(white_last_move.comment),
                "black": hacky_python_parsing_of_times(black_last_move.comment),
            },
            "player": {
                "color": "white",
                "rating": int(game.headers["WhiteElo"]),
                "user": {
                    "id": game.headers['White'],
                    "username": game.headers['White'],
                }
            },
            "opponent": {
                "color": "black",
                "rating": int(game.headers["BlackElo"]),
                "user": {
                    "id": game.headers['Black'],
                    "username": game.headers['Black'],
                }
            },
            "orientation": "white",
            "steps": [move_message(n) for n in moves],
        }
    }

# {"t":"fen","d":{"id":"CdDDXCJd","fen":"2r1rbk1/3b1ppp/q2p1n2/1pnPp3/p1N1P3/2N1B1PP/PP2QPB1/2R1R1K1","lm":"b7b5"}}
# {"t": "fen", "d": {"ply": 0, "id": "caruana-fabiano-vs-kramnik-vladimir", "fen": "7r/8/1br3p1/p4p2/R2PpNkP/2P1KpP1/1P3P2/R7 w - - 0 45", "san": "Rxc6", "uci": "d6c6", "clock": {"white": 1494}, "lm": "d6c6"}}
def move_message(node, ply=None, type="move"):
    return {
        "t": type,
        "d": {
            "id": game_key(node.root()),
            "ply": 0 if ply is None else ply,
            "uci": node.move.uci(),
            "lm": node.move.uci(),
            "san": node.san(),
            "fen": node.board().fen(),
            "clock": {
                "white" if node.board().turn == chess.WHITE else "black": hacky_python_parsing_of_times(node.comment),
            }
        }
    }

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
            # TODO: This needs to be updated to create/find the chapter and then ensure it's up to date.
            send(game_message(new_game))
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
        new_node = new_node.variations[0]
        while True:
            print("New move in {}: {}".format(key, new_node.move.uci()))
            # TODO: This message needs to be updated
            send(move_message(new_node, type="fen"))
            if new_node.is_end():
                break
            new_node = new_node.variations[0]
        games[key] = new_game

@gen.coroutine
def update_pgns():
    client = httpclient.AsyncHTTPClient()
    url = "{}?v={}".format(pgn_source_url, time.time())
    print(".", end="", flush=True)
    response = yield client.fetch(url)
    process_pgn(response.body.decode("ISO-8859-1")) # TODO: pull this from the content-type

already_processed = []
def poll_files():
    files = sorted(glob.glob("./local-files/*.pgn"))
    for file in files:
        if file in already_processed:
            continue
        print("Processing {}!".format(file))
        already_processed.append(file)
        contents = open(file, "r").read()
        process_pgn(contents)
        return # don't process anymore.

def send(message):
    pass

headers = {
    'Accept': 'application/vnd.lichess.v1+json',
}
cookie = None
@gen.coroutine
def login(study):
    global cookie
    global headers
    client = httpclient.AsyncHTTPClient()
    url = "https://lichess.org/login"
    params = {"username": username, "password": password}
    login_request = httpclient.HTTPRequest(url, method="POST", headers=headers, body=urlencode(params))
    response = yield client.fetch(login_request)
    cookie = cookies.SimpleCookie()
    cookie.load(response.headers['Set-Cookie'])
    headers['Cookie'] = cookie['lila2'].OutputString()

    url = "https://lichess.org/account/info"
    account_info_request = httpclient.HTTPRequest(url, method="GET", headers=headers)
    response = yield client.fetch(account_info_request)
    connect_to_study(study)
    # TODO: At this point we can start polling for the new data. Not before

socket = None
@gen.coroutine
def connect_to_study(study):
    sri = "".join([random.choice(string.ascii_letters) for x in range(10)])
    study_url = "wss://socket.lichess.org:9028/study/{}/socket/v2?sri={}".format(
        study,
        sri
    )
    socket_request = httpclient.HTTPRequest(study_url, headers=headers)
    socket = yield websocket.websocket_connect(socket_request)

    while True:
        msg = yield socket.read_message()
        if msg is None: break
        print(msg)


pgn_source_url = None
username = password = study = url = None
if __name__ == '__main__':
    import sys
    if len(sys.argv) < len(['username', 'password', 'study']):
        sys.exit("Usage: pgnstudyrelay.py <username> <password> <study> [<url>]")
    if len(sys.argv) == len(['_', 'username', 'password', 'study', 'url']):
        _, username, password, study, url = sys.argv
        pgn_source_url = url
        # TODO: this should be moved to after we have connected to the study
        pollpgn = ioloop.PeriodicCallback(update_pgns, 1000)
    else:
        print("Polling files!")
        _, username, password, study = sys.argv
        pollpgn = ioloop.PeriodicCallback(poll_files, 500)
        # TODO: this should be moved to after we have connected to the study
    pollpgn.start()
    login(study)
ioloop.IOLoop.instance().start()
