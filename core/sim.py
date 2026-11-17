"""Headless balance simulator: a bot plays whole games without a window and prints how they went.

    python main.py --sim emberfall_road normal
    python main.py --sim all                    # every map on every difficulty, one summary line each
    python main.py --sim all powers             # the same, with the bot also using Meteor / Frost Nova / Overcharge
"""
from __future__ import annotations

import time as _time
from typing import Any

from core import data
from core.bot import Bot
from core.match import Match, MatchConfig

STEP = 1.0 / 20.0


def run(map_id: str, difficulty: str = "normal", endless: bool = False, seed: int = 1, skill: float = 1.0,
        verbose: bool = True, max_wave: int = 0, max_time: float = 6000.0, powers: bool = False) -> dict[str, Any]:
    """Play one game with the bot. Returns a summary dict."""
    m = Match(None, MatchConfig(map_id=map_id, difficulty=difficulty, endless=endless, visual=False, seed=seed))
    bot = Bot(m, skill, use_powers=powers)
    w = m.waves
    rows = []
    cleared = 0
    leaks_before = 0
    t0 = _time.perf_counter()
    if verbose:
        print(f"== {m.level.name} / {difficulty}{' / endless' if endless else ''}  (road {m.grid.route_length(0):.0f} units)")
        print(" wave  creeps                          hp/each  lives  leaks   gold  towers  invested   time")
    while not m.over and m.time < max_time:
        if not w.in_progress and w.has_next and w.countdown > 1.0 and (w.number > 0 or m.time > 4.0):
            w.countdown = 1.0                      # skip the idle countdown (no early-call bonus is paid)
        m.update(STEP)
        bot.update(STEP)
        if len(w.cleared) > cleared:
            cleared = len(w.cleared)
            info = w.info(cleared)
            invested = sum(t.invested for t in m.towers)
            leaked = m.stats["leaks"] - leaks_before
            leaks_before = m.stats["leaks"]
            rows.append((cleared, m.lives, leaked, m.gold, len(m.towers), invested))
            if verbose and info is not None:
                hp = max(info["hp"].values())
                print(f" {cleared:4d}  {info['detail'][:30]:30s} {hp:8.0f}  {m.lives:5d}  {leaked:5d}  {m.gold:5d}  {len(m.towers):6d}"
                      f"  {invested:8d}  {m.time:5.0f}s")
            if max_wave and cleared >= max_wave:
                break
    best = max(m.towers, key=lambda t: t.damage_dealt, default=None)
    by_type: dict[str, float] = {}
    for t in m.towers:
        by_type[t.type_id] = by_type.get(t.type_id, 0.0) + t.damage_dealt
    out = {"map": map_id, "difficulty": difficulty, "won": m.won, "wave": len(w.cleared), "lives": m.lives,
           "leaks": m.stats["leaks"], "kills": m.stats["kills"], "score": m.score(), "time": m.time,
           "towers": len(m.towers), "real": _time.perf_counter() - t0, "by_type": by_type, "rows": rows}
    if verbose:
        result = "VICTORY" if m.won else ("DEFEAT" if m.over else "stopped")
        print(f"-> {result} at wave {out['wave']}, {m.lives}/{m.max_lives} lives, {out['leaks']} leaks, {out['kills']} kills, "
              f"score {out['score']}, {m.time:.0f}s game time ({out['real']:.1f}s real)")
        total = sum(by_type.values()) or 1.0
        print("   damage share: " + ", ".join(f"{k} {v / total * 100:.0f}%" for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])))
        if best is not None:
            print(f"   best tower: {best.name} with {best.damage_dealt:,.0f} damage and {best.kills} kills")
        roster: dict[str, list[int]] = {}
        for t in m.towers:
            roster.setdefault(t.type_id, []).append(t.level)
        print("   towers: " + ",  ".join(f"{k} {'/'.join(str(v) for v in sorted(lv, reverse=True))}" for k, lv in sorted(roster.items())))
    return out


def main(args: list[str]) -> None:
    maps = list(data.maps())
    diffs = list(data.combat()["difficulty"])
    powers = "powers" in args
    args = [a for a in args if a != "powers"]
    if not args or args[0] == "all":
        print(f"{'map':16s} {'difficulty':8s} result   wave  lives  leaks  score" + ("   (bot uses powers)" if powers else ""))
        for mid in maps:
            for d in diffs:
                r = run(mid, d, verbose=False, powers=powers)
                print(f"{mid:16s} {d:8s} {'WIN ' if r['won'] else 'LOSS'}    {r['wave']:3d}  {r['lives']:5d}  {r['leaks']:5d}  {r['score']:6d}"
                      f"   ({r['real']:.1f}s)")
        return
    map_id = args[0] if args[0] in maps else maps[0]
    difficulty = args[1] if len(args) > 1 and args[1] in diffs else "normal"
    run(map_id, difficulty, endless="endless" in args, powers=powers)
