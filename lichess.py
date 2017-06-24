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

__all__ = [
    "Lichess",
    "LoginError",
    "StudyConnectionError"
    "move_to_path_id",
]

import aiohttp
import asyncio
import json
import random
import pprint
import string
import sys
import time

from collections import defaultdict


from chess import PIECE_SYMBOLS, SQUARES, square_file, square_rank


#-------------------------------------------------------------------------------
# Lichess errors
#-------------------------------------------------------------------------------
class LoginError(RuntimeError):
    pass

class StudyConnectionError(RuntimeError):
    pass

class StudyNotAContributor(RuntimeError):
    pass

#-------------------------------------------------------------------------------
# Some useful constants
#-------------------------------------------------------------------------------
STAGING_DOMAIN = "listage.ovh"
STAGING_URL = "https://{}/".format(STAGING_DOMAIN)
STAGING_WS_URL = "wss://socket.{}".format(STAGING_DOMAIN)

LIVE_DOMAIN = "lichess.org"
LIVE_URL = "https://{}/".format(LIVE_DOMAIN)
LIVE_WS_URL = "wss://socket.{}".format(LIVE_DOMAIN)

#-------------------------------------------------------------------------------
# Converting raw data into dictionaries suitable for the lichess protocol
#-------------------------------------------------------------------------------
def clock_from_comment(comment):
    if not "[%clk" in comment:
        return None
    comment = comment.replace("[%clk ", "")
    comment = comment.replace("]", "")
    return comment.strip()


#-------------------------------------------------------------------------------
# Hashing uci moves into 2 character path names in the lichess study style.
#-------------------------------------------------------------------------------
def hash_code(pos):
    return 8 * square_rank(pos) + square_file(pos)

char_shift = 35;
void_char = chr(33);

pos_to_char_map = {pos: chr(hash_code(pos) + char_shift) for pos in SQUARES}
pos_to_char_map_size = len(pos_to_char_map)
def to_char(pos):
    return pos_to_char_map.get(pos, void_char)

all_promotable = ["q", "r", "b", "n","k"]
promotion_to_char_map = {}
for index, role in enumerate(all_promotable):
    for file in range(0,8):
        promotion_to_char_map[(file, role)] = chr(char_shift + pos_to_char_map_size  + index * 8 + file)
promotion_to_char_map_size = len(promotion_to_char_map)
def to_char_with_promotion(file, role):
    return promotion_to_char_map.get((file, role), void_char)

drop_role_to_char_map = {}
droppable = [s for s in PIECE_SYMBOLS if s not in ("", "k")]
droppable = ["q", "r", "b", "n","p"]
for index, role in enumerate(droppable):
    drop_role_to_char_map[role] = chr(char_shift + pos_to_char_map_size + promotion_to_char_map_size + index)


def move_to_path_id(move):
    """Turn a move into a unique 2 character symbol, based on:
    https://github.com/ornicar/scalachess/blob/ba0a2a56378e268d78e00f3f1457730552c6ce01/src/main/scala/format/UciCharPair.scala

    >>> from chess import E2, E4, Move, QUEEN
    >>> move_to_path_id(Move(E2, E4))
    '/?'

    >>> from chess import A7, A8, Move, QUEEN
    >>> move_to_path_id(Move(A7, A8, QUEEN))
    'Sc'

    >>> from chess import H7, H8, Move, KNIGHT
    >>> move_to_path_id(Move(H7, H8, KNIGHT))
    'Z\x82'

    >>> from chess import E8, G8, Move
    >>> move_to_path_id(Move(E8, G8))
    '_a'

    >>> from chess import E8, H8, Move
    >>> move_to_path_id(Move(E8, H8))
    '_b'

    >>> from chess import E1, H1, Move
    >>> move_to_path_id(Move(E1, H1))
    "'*"

    >>> from chess import E1, G1, Move
    >>> move_to_path_id(Move(E1, G1))
    "')"

    """
    if move.drop:
        return "{}{}".format(
			to_char(move.from_square),
            drop_role_to_char_map.get(PIECE_SYMBOLS[move.drop], void_char)
        )
    elif move.promotion:
        return "{}{}".format(
            to_char(move.from_square),
            to_char_with_promotion(square_file(move.to_square), PIECE_SYMBOLS[move.promotion])
        )
    elif move:
        return "{}{}".format(
            to_char(move.from_square),
            to_char(move.to_square)
        )
    else:
        raise NotImplementedError("We don't have an implementation for null moves")

headers = {
    'Accept': 'application/vnd.lichess.v2+json',
}
#-------------------------------------------------------------------------------
# The objects used to interact with the study.
#-------------------------------------------------------------------------------
class Study:
    #---------------------------------------------------------------------------
    def __init__(self, lichess, study_id):
        self.lichess = lichess
        self.study_id = study_id
        self.study_path = "study/{}".format(study_id)
        self.study_url = self.lichess.url(self.study_path)
        self.study_data = {}
        self._chapters = {}
        self._chapter_versions = defaultdict(int)
        self.domain = None
        self.sri = "".join([random.choice(string.ascii_letters) for x in range(10)])
        self.websocket_url = "wss://socket.{}/{}/socket/v2?sri={}".format(
            self.lichess.domain,
            self.study_path,
            self.sri
        )
        self.websocket = None
        self.websocket_connected = asyncio.Future()
        self.should_stop = False

    #---------------------------------------------------------------------------
    async def connect(self):
        await self.sync()
        asyncio.ensure_future(self.connect_to_websocket())
        await self.websocket_connected

    #---------------------------------------------------------------------------
    async def process_chat_message(self, data):
        contributors = [u for u, v in self.study_data['study']['members'].items() if v['role'] == 'w']
        if data['u'] in contributors:
            if data.get('t', '').startswith('sync '):
                print("<- [RECEIVE]: {} is a contributor. Syncing".format(data['u']))
                await self.sync(full=True)

    #---------------------------------------------------------------------------
    async def connect_to_websocket(self):
        async with self.lichess.session.ws_connect(self.websocket_url, headers=headers) as websocket:
            self.websocket = websocket
            self.websocket_connected.set_result(self.websocket)
            ping_future = asyncio.ensure_future(self._ping())
            async for msg in websocket:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if not data:
                        continue
                    if self.lichess.log_ws:
                        print("<- [RECEIVE]: {}".format(msg.data))
                    if data['t'] == 'addChapter':
                        # We got a new chapter, we should sync it
                        await self.sync_chapter(data['d']['p']['chapterId'])
                    elif data['t'] == 'reload':
                        chapter_id = data.get('d', {}).get('chapterId')
                        if chapter_id:
                            await self.sync_chapter(chapter_id)
                        else:
                            await self.sync()
                    elif data['t'] == 'message':
                        d = data.get("d")
                        if d:
                            await self.process_chat_message(d)
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    print("Lost connection, disconnecting")
                    self.should_stop = True
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print("Error, disconnecting")
                    self.should_stop = True
                    break
            await ping_future

    #---------------------------------------------------------------------------
    async def _ping(self):
        while True:
            await self.send({"t": "p"})
            await asyncio.sleep(1)
            if self.should_stop:
                break

    # TODO: There has to be a better way to accomplish this O_o
    #---------------------------------------------------------------------------
    async def send(self, data):
        if not self.websocket:
            raise RuntimeError("Sending without a websocket!!")
        msg_str = json.dumps(data)
        if self.lichess.log_ws:
            print("-> [SENDING]: {}".format(msg_str))
        await self.websocket.send_str(msg_str)

    #---------------------------------------------------------------------------
    def ensure_contributor(self):
        members = self.study_data['study']['members']
        if self.lichess.username not in members or members[self.lichess.username]['role'] != 'w':
            raise StudyNotAContributor("The user must be a contributor to the study")

    #---------------------------------------------------------------------------
    async def sync(self, full=False):
        print("++ [SYNCING] getting full study")
        response = await self.lichess.session.get(self.study_url, headers=headers)
        if response.status != 200:
            raise StudyConnectionError("Unable to connect to the study. {} returned {}".format(
                self.study_url,
                response.status
            ))
        self.study_data = await response.json()
        for chapter in self.study_data['study'].get('chapters', []):
            if full or chapter['id'] not in self._chapters:
                await self.sync_chapter(chapter['id'])

    #---------------------------------------------------------------------------
    async def sync_chapter(self, chapter_id):
        chapter_url = "{}/{}?_={}".format(self.study_url, chapter_id, time.time())
        print("++ [SYNCING] getting new chapter#{}".format(chapter_id))
        response = await self.lichess.session.get(chapter_url, headers=headers)
        if response.status != 200:
            raise StudyConnectionError("Unable to connect to the chapter. {} returned {}".format(
                chapter_url,
                response.status
            ))
        chapter_data = await response.json()

        # Convert the tags into a dict
        tags = {}
        for tag_name, tag_value in chapter_data['study']['chapter']['tags']:
            tags[tag_name] = tag_value

        chapter_data['tags'] = tags
        chapter_data['id'] = chapter_id

        self._chapter_versions[chapter_id] = self._chapter_versions[chapter_id] + 1
        chapter_data['version'] = self._chapter_versions[chapter_id]
        self._chapters[chapter_id] = chapter_data

    #---------------------------------------------------------------------------
    def get_chapters(self):
        return self._chapters.values()

    #---------------------------------------------------------------------------
    def get_chapter(self, id):
        return self._chapters[id]

    #---------------------------------------------------------------------------
    async def create_chapter_from_pgn(self, pgn):
        await self.send({
            "t":"addChapter",
            "d":{
                "name": "Chapter 1", # NOTE: this + pgn causes the chapter to be autonamed.
                "game":None,
                "variant":"Automatic",
                "fen":None,
                "pgn": pgn.strip(),
                "orientation":"white",
                "mode":"normal",
                "initial":False,
                "sticky": False
            }
        })

    #---------------------------------------------------------------------------
    async def add_move(self, chapter_id, path,new_node, old_node):
        promotion_lookup = {
            "q": "queen",
            "r": "rook",
            "b": "bishop",
            "n": "knight",
            "k": "king",
        }
        uci = old_node.board().uci(new_node.move, chess960=True)
        move = {
            "t":"anaMove",
            "d":{
                "orig": uci[:2],
                "dest": uci[2:4],
                "fen": old_node.board().fen(),
                "path": path,
                "ch": chapter_id,
                "sticky": False,
                "promote": True,
            }
        }
        if len(uci) == 5:
            move["d"]["promotion"] = promotion_lookup[uci[4]]
        clock = clock_from_comment(new_node.comment)
        if clock:
            move["d"]["clock"] = "{}".format(clock)
        await self.send(move)

    #---------------------------------------------------------------------------
    async def set_tag(self, chapter_id, tag_name, tag_value):
        await self.send({
            "t": "setTag",
            "d": {
                "chapterId": chapter_id,
                "name": tag_name,
                "value": tag_value,
            }
        })

    #---------------------------------------------------------------------------
    async def set_move_comment(self, chapter_id, path, comment):
        await self.send({
            "t": "setComment",
            "d": { 
                "ch": chapter_id,
                "path": path,
                "text": comment
            }
        })

    #---------------------------------------------------------------------------
    async def talk(self, message):
        await self.send({
            "t": "talk",
            "d": message,
        })




#-------------------------------------------------------------------------------
class Lichess:
    #---------------------------------------------------------------------------
    def __init__(self, loop, session, url, log_ws=False):
        if url not in [STAGING_URL, LIVE_URL]:
            raise RuntimeError("{} is not one of {} or {}".format(
                url,
                LIVE_URL,
                STAGING_URL,
            ))

        self.loop = loop
        self.base_url = url
        self.domain = "lichess.org" if url == LIVE_URL else "listage.ovh"
        self.session = session
        self.log_ws = False

    #---------------------------------------------------------------------------
    def url(self, path, scheme="https"):
        """Generate a lichess url from the given path component. 

        This is a convenience function that makes for slightly shorter code.
        """
        return "{}://{}/{}".format(scheme, self.domain, path)

    #---------------------------------------------------------------------------
    async def login(self, username, password):
        """Login to lichess using the given credentials.

        Raises LoginError if the login is unsuccessful
        """
        self.username = username
        response = await self.session.post(
            self.url('login'),
            headers=headers,
            data={"username": username, "password": password}
        )
        if response.status != 200:
            raise LoginError("Unable to login")

    #---------------------------------------------------------------------------
    async def study(self, study_id):
        study = Study(self, study_id)
        await study.connect()
        return study

if __name__ == "__main__":
    import doctest
    doctest.testmod()
