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
        "sticky": False}
    }


#{"t":"anaMove","d":{"orig":"h4","dest":"f6","fen":"r5k1/p5pp/B2p1nb1/3p4/7B/7P/P1P3P1/1R4K1 w - - 2 26","path":"/?WG)8\\M(DaP'*P?('?N8G`WD(MG'G_b.>WPG'`_%@_'&'N_$5P>5FVN@IXPIB>,#$,G0@UM@GMFGP_P(6]V2:TD6D^_'_V_DK_Q","ch":"rZ5fgilU"}}
#{"t":"anaMove","d":{"orig":"h4","dest":"f6","fen":"r5k1/p5pp/B2p1nb1/3p4/7B/7P/P1P3P1/1R4K1 w - - 2 26","path":"/?WG)8\\M(DaP'*P?('?N8G`WD(MG'G_b.>WPG'`_%@_'&'N_$5P>5FVN@IXPIB>,#$,G0@UM@GMFGP_P(6]V2:TD6D^_'_V_DK_Q","ch":"rZ5fgilU"}}
#

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
        move["clock"] = "{}".format(clock)
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
