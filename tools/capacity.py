"""Balance helper: how much creep health can a fixed budget of one tower type stop in a single wave?

For every tower type and level it spends BUDGET gold on that tower alone (placed where it covers the most
road), then searches for the toughest wave of 12 creeps it kills without a leak. The result is printed as
"health killed per gold spent", which makes over- and under-priced towers easy to spot.

    python tools/capacity.py                 # all towers against a few reference creeps
    python tools/capacity.py emberfall_road arrow cannon
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import data  # noqa: E402
from core.bot import Bot  # noqa: E402
from core.match import Match, MatchConfig  # noqa: E402

BUDGET = 600
COUNT = 12
INTERVAL = 1.0
STEP = 1.0 / 20.0
CREEPS = ("gloomling", "bristleboar", "marauder", "wraith", "hexer", "gloomwing")


def leaks(map_id: str, tower: str, level: int, creep: str, unit_hp: float) -> tuple[int, int]:
    """(leaks, gold spent) for one trial."""
    m = Match(None, MatchConfig(map_id=map_id, visual=False))
    m.waves.countdown = 1e9
    m.lives = m.max_lives = 10 ** 6
    bot = Bot(m)
    cost = data.tower_total_cost(tower, level)
    spent = 0
    while spent + cost <= BUDGET:
        cell = bot.best_cell(tower)
        if cell < 0:
            break
        t = m.build_tower(tower, cell, free=True, instant=True)
        if t is None:
            break
        for _ in range(level - 1):
            t.begin_upgrade()
            t.busy_t = 0.0
            t.update(0.0)
        spent += cost
    if spent == 0:
        return COUNT, 0
    sent = 0
    clock = 0.0
    while True:
        while sent < COUNT and clock >= sent * INTERVAL:
            m.spawn_creep(creep, unit_hp, 0, 0, 0)
            sent += 1
        m.update(STEP)
        clock += STEP
        if sent >= COUNT and not m.creeps and not m._born:
            break
        if clock > 600:
            break
    return m.stats["leaks"], spent


def capacity(map_id: str, tower: str, level: int, creep: str) -> tuple[float, int]:
    """Total wave health (COUNT creeps) this tower set can stop, found by bisection on the creeps' health."""
    lo, hi = 1.0, 200000.0
    spent = 0
    for _ in range(18):
        mid = (lo * hi) ** 0.5
        n, spent = leaks(map_id, tower, level, creep, mid)
        if n == 0:
            lo = mid
        else:
            hi = mid
    factor = float(data.creeps()[creep].get("hp", 1.0))
    return lo * factor * COUNT, spent


def main(argv: list[str]) -> None:
    maps = data.maps()
    map_id = argv[0] if argv and argv[0] in maps else "emberfall_road"
    towers = [a for a in argv if a in data.towers()] or [t for t in data.towers() if t != "banner"]
    print(f"map {map_id}, budget {BUDGET} gold, wave of {COUNT} creeps: health killed per gold spent")
    print(f"{'tower':10s} lv  n  " + "  ".join(f"{c[:10]:>10s}" for c in CREEPS))
    for tid in towers:
        for level in (1, 2, 3):
            row = []
            spent = 0
            for creep in CREEPS:
                cap, spent = capacity(map_id, tid, level, creep)
                row.append(cap / spent if spent else 0.0)
            n = spent // data.tower_total_cost(tid, level) if spent else 0
            print(f"{tid:10s} {level:2d} {n:2d}  " + "  ".join(f"{v:10.2f}" for v in row), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
