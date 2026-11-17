"""Best results per map / difficulty / mode, stored in scores.json next to the game."""
from __future__ import annotations

import json
from typing import Any

import settings
from settings import SCORES_FILE


def _load() -> dict[str, Any]:
    try:
        raw = json.loads(SCORES_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _key(map_id: str, difficulty: str, endless: bool) -> str:
    return f"{map_id}|{difficulty}|{'endless' if endless else 'campaign'}"


def stars(won: bool, lives: int, max_lives: int) -> int:
    """0-3 stars: win at all, win with half your lives, win without losing any."""
    if not won:
        return 0
    if lives >= max_lives:
        return 3
    return 2 if lives * 2 >= max_lives else 1


def best(map_id: str, difficulty: str, endless: bool = False) -> dict[str, Any] | None:
    return _load().get(_key(map_id, difficulty, endless))


def record(match) -> tuple[dict[str, Any], bool]:
    """Store the result of a finished match if it beats the previous best. Returns (entry, is new best)."""
    cfg = match.config
    entry = {"score": match.score(), "wave": len(match.waves.cleared), "won": bool(match.won), "lives": max(0, match.lives),
             "stars": stars(match.won, match.lives, match.max_lives), "kills": match.stats["kills"]}
    allscores = _load()
    key = _key(cfg.map_id, cfg.difficulty, cfg.endless)
    old = allscores.get(key)
    is_best = old is None or entry["score"] > old.get("score", 0)
    if not settings.PERSIST:
        return entry, is_best
    if is_best:
        entry["stars"] = max(entry["stars"], (old or {}).get("stars", 0))
        allscores[key] = entry
        try:
            SCORES_FILE.write_text(json.dumps(allscores, indent=2), encoding="utf-8")
        except OSError:
            pass
    elif entry["stars"] > old.get("stars", 0):
        old["stars"] = entry["stars"]
        try:
            SCORES_FILE.write_text(json.dumps(allscores, indent=2), encoding="utf-8")
        except OSError:
            pass
    return entry, is_best
