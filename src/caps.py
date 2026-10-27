"""
Board caps: how many valid plays make the board, and which ones.

The edge model grades every player with a line, so a busy slate can produce
far more A-grade edges than anyone should bet. The caps keep the board to the
strongest edges: at most MAX_PER_PLAYER single-stat plays per player (plus one
combo per player, counted separately), at most MAX_PER_GAME plays per game and
at most MAX_PICKS on the whole board. Plays are taken best edge first (ties:
more books hanging the line, then the higher modelled probability), so a
dropped play is always weaker than every play kept ahead of it on its cap.

A dropped play keeps its grade (it was still an A) but is no longer valid; its
`cap_note` says which cap dropped it ("cap: 3 per game", "cap: 15 on the
board", "cap: 1 per player") so the board can list it below the picks.
"""

import logging

from src.config import MAX_PER_GAME, MAX_PER_PLAYER, MAX_PICKS
from src.underdog import norm_name

logger = logging.getLogger(__name__)

COMBO_STATS = {"pts+ast", "pts+reb", "reb+ast", "pra"}

REASON_PLAYER = "per_player"
REASON_GAME = "per_game"
REASON_BOARD = "board"


def _edge_order_key(p) -> tuple:
    """Strongest edge first; a play without an edge value sorts last."""
    edge = getattr(p, "edge_value", None)
    edge = float(edge) if isinstance(edge, (int, float)) else float("-inf")
    books = (getattr(p, "line_info", None) or {}).get("books") or 0
    p_model = getattr(p, "p_model", None)
    p_model = float(p_model) if isinstance(p_model, (int, float)) else float("-inf")
    return (-edge, -float(books), -p_model)


def _player_key(p) -> str:
    try:
        return norm_name(p.player_name)
    except Exception:
        return str(getattr(p, "player_name", "")).strip().lower()


def _game_key(p) -> frozenset:
    return frozenset({str(getattr(p, "team", "") or ""), str(getattr(p, "opponent", "") or "")})


def apply_caps(props: list, max_picks: int = MAX_PICKS, max_per_game: int = MAX_PER_GAME,
               max_per_player: int = MAX_PER_PLAYER, pinned: list | None = None) -> dict:
    """
    Apply the board caps to the valid props in `props`, in place.

    Only props with `is_valid` are considered. They are taken in order of
    `edge_value` (desc; ties: more books, then higher p_model). Each is kept
    unless it breaches, in this order: the per-player cap (singles count
    against `max_per_player`; a combo counts against a separate cap of one
    combo per player), the per-game cap (`max_per_game` plays per game,
    singles and combos together; game = frozenset({team, opponent})) or the
    board cap (`max_picks` plays overall). A dropped prop gets
    `is_valid = False` and a `cap_note` naming the cap; its grade is kept.

    `pinned` (fill mode): picks already published. They are always kept and
    count against every cap first, so the remaining room is filled in one
    pass with the real limits (a strong play in a game the pinned picks
    already fill is dropped per game; it never takes a board slot first).

    Returns {"kept": n, "dropped": n, "by_reason": {"per_player": n,
    "per_game": n, "board": n}, "limits": {"max_picks", "max_per_game",
    "max_per_player"}, "pinned": n}.
    """
    pinned = list(pinned or [])
    pinned_ids = {id(p) for p in pinned}
    valid = sorted((p for p in props if getattr(p, "is_valid", False) and id(p) not in pinned_ids), key=_edge_order_key)
    singles_by_player: dict[str, int] = {}
    combos_by_player: dict[str, int] = {}
    by_game: dict[frozenset, int] = {}
    kept = 0
    by_reason = {REASON_PLAYER: 0, REASON_GAME: 0, REASON_BOARD: 0}

    for p in pinned:   # published earlier today: kept, and they use up room first
        p.is_valid = True
        p.cap_note = ""
        kept += 1
        game, player = _game_key(p), _player_key(p)
        by_game[game] = by_game.get(game, 0) + 1
        if getattr(p, "stat", "") in COMBO_STATS:
            combos_by_player[player] = combos_by_player.get(player, 0) + 1
        else:
            singles_by_player[player] = singles_by_player.get(player, 0) + 1

    for p in valid:
        player = _player_key(p)
        game = _game_key(p)
        combo = getattr(p, "stat", "") in COMBO_STATS
        note = ""
        reason = None
        if combo:
            if combos_by_player.get(player, 0) >= 1:
                note, reason = "cap: 1 combo per player", REASON_PLAYER
        elif singles_by_player.get(player, 0) >= max_per_player:
            note, reason = f"cap: {max_per_player} per player", REASON_PLAYER
        if not note and by_game.get(game, 0) >= max_per_game:
            note, reason = f"cap: {max_per_game} per game", REASON_GAME
        if not note and kept >= max_picks:
            note, reason = f"cap: {max_picks} on the board", REASON_BOARD

        if note:
            p.is_valid = False
            p.cap_note = note
            by_reason[reason] += 1
            logger.info(f"  Capped: {p.player_name} {p.stat} {p.edge} ({note})")
            continue

        p.cap_note = ""
        kept += 1
        by_game[game] = by_game.get(game, 0) + 1
        if combo:
            combos_by_player[player] = combos_by_player.get(player, 0) + 1
        else:
            singles_by_player[player] = singles_by_player.get(player, 0) + 1

    dropped = sum(by_reason.values())
    if dropped:
        logger.info(f"Board caps: kept {kept}, dropped {dropped} "
                    f"({by_reason[REASON_PLAYER]} per player, {by_reason[REASON_GAME]} per game, {by_reason[REASON_BOARD]} board)")
    return {
        "kept": kept,
        "dropped": dropped,
        "by_reason": by_reason,
        "limits": {"max_picks": max_picks, "max_per_game": max_per_game, "max_per_player": max_per_player},
        "pinned": len(pinned),
    }
