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
        "initial":False}
    }
#            {"t":"addChapter","d":{"name":"Chapter 2","game":null,"variant":"Automatic","fen":null,"pgn":"[Event \"Norway Chess 2017\"]\n[Site \"Stavanger\"]\n[Date \"2017.06.08\"]\n[Round \"3.4\"]\n[White \"Kramnik, Vladimir\"]\n[Black \"So, Wesley\"]\n[Result \"*\"]\n[BlackElo \"2812\"]\n[WhiteElo \"2808\"]\n[LiveChessVersion \"1.4.8\"]\n[ECO \"C50\"]\n\n1. e4 {[%clk 1:39:32]} e5 {[%clk 1:39:42]} 2. Nf3 {[%clk 1:39:24]} Nc6\n{[%clk 1:39:38]} 3. Bc4 {[%clk 1:38:47]} Bc5 {[%clk 1:39:08]} 4. O-O\n{[%clk 1:38:17]} Nf6 {[%clk 1:39:02]} 5. d3 {[%clk 1:38:13]} O-O {[%clk 1:38:33]}\n6. h3 {[%clk 1:38:06]} d6 {[%clk 1:37:37]} 7. c3 {[%clk 1:37:58]} a6\n{[%clk 1:32:29]} 8. Re1 {[%clk 1:36:44]} Ba7 {[%clk 1:30:52]} 9. Bb3\n{[%clk 1:36:12]} h6 {[%clk 1:29:45]} 10. Nbd2 {[%clk 1:33:47]} Re8\n{[%clk 1:28:48]} 11. Nf1 {[%clk 1:33:40]} Be6 {[%clk 1:28:15]} 12. Be3\n{[%clk 1:33:33]} Bxe3 {[%clk 1:18:35]} 13. Nxe3 {[%clk 1:33:23]} Qd7\n{[%clk 1:18:30]} 14. Bxe6 {[%clk 1:32:26]} Qxe6 {[%clk 1:14:19]} 15. c4\n{[%clk 1:32:04]} Ne7 {[%clk 1:05:01]} 16. Nd5 {[%clk 1:30:55]} Nexd5\n{[%clk 1:00:35]} 17. cxd5 {[%clk 1:30:48]} Qd7 {[%clk 1:00:02]} 18. d4\n{[%clk 1:21:31]} c6 {[%clk 0:55:42]} 19. dxc6 {[%clk 1:15:21]} Qxc6\n{[%clk 0:54:27]} 20. Rc1 {[%clk 1:07:24]} Qb6 {[%clk 0:53:49]} *\n","orientation":"white","mode":"normal","initial":false}}


#{"t":"anaMove","d":{"orig":"h4","dest":"f6","fen":"r5k1/p5pp/B2p1nb1/3p4/7B/7P/P1P3P1/1R4K1 w - - 2 26","path":"/?WG)8\\M(DaP'*P?('?N8G`WD(MG'G_b.>WPG'`_%@_'&'N_$5P>5FVN@IXPIB>,#$,G0@UM@GMFGP_P(6]V2:TD6D^_'_V_DK_Q","ch":"vkE9sJtF"}}
#{"t":"anaMove","d":{"orig":"h4","dest":"f6","fen":"r5k1/p5pp/B2p1Bb1/3p4/8/7P/P1P3P1/1R4K1 b - - 0 26","path":"/?WG)8\\M(DaP'*P?('?N8G`WD(MG'G_b.>WPG'`_%@_'&'N_$5P>5FVN@IXPIB>,#$,G0@UM@GMFGP_P(6]V2:TD6D^_'_V_DK_Q","ch":"vkE9sJtF"}}


def add_move_to_study(new_node, old_node, chapter, tree_parts):
    uci = new_node.move.uci()
    return {
        "t":"anaMove",
        "d":{
            "orig": uci[:2],
            "dest": uci[2:],
            "fen": old_node.board().fen(),
            "path": "".join([tp['id'] for tp in tree_parts[1:]]),
            "ch": chapter['id'],
        }
    }
