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


# Helpers for making lichess style messages
from chess import PIECE_SYMBOLS, SQUARES, square_file, square_rank

def add_study_chapter_message(name, pgn=None):
    return {
        "t":"addChapter",
        "d":{
            "name": name,
            "game":None,
            "variant":"Automatic",
            "fen":None,
            "pgn": pgn.strip(),
            "orientation":"white",
            "mode":"normal",
            "initial":False,
            "sticky": False
        }
    }

def clock_from_comment(comment):
    if not "[%clk" in comment:
        return None
    comment = comment.replace("[%clk ", "")
    comment = comment.replace("]", "")
    return comment.strip()

def add_move_to_study(new_node, old_node, chapter_id, path):
    uci = new_node.move.uci()
    move = {
        "t":"anaMove",
        "d":{
            "orig": uci[:2],
            "dest": uci[2:],
            "fen": old_node.board().fen(),
            "path": path,
            "ch": chapter_id,
            "sticky": False,
            "promote": True,
        }
    }
    clock = clock_from_comment(new_node.comment)
    if clock:
        move["d"]["clock"] = "{}".format(clock)
    return move


#{"t":"setTag","d":{"chapterId":"NNgEcycT","name":"Result","value":"1/2-1/2"}}
def set_tag(chapter_id, tag_name, tag_value):
    return {
        "t": "setTag",
        "d": {
            "chapterId": chapter_id,
            "name": tag_name,
            "value": tag_value,
        }
    }

def set_comment(chapter_id, path, comment):
    return {
        "t": "setComment",
        "d": { 
            "ch": chapter_id,
            "path": path,
            "text": comment
        }
    }

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

    >>> from chess import E1, H1, Move
    >>> move_to_path_id(Move(E1, H1))
    "'*"

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
        return "" # TODO: what to do for null moves?

if __name__ == "__main__":
    import doctest
    doctest.testmod()
