"""Spirewatch - a fantasy tower defense built with Ursina.

Usage:
    python main.py                                   # main menu
    python main.py --quick emberfall_road normal     # skip the menus: map, difficulty
    python main.py --quick hollow_moor hard --endless
    python main.py --sim all                         # headless balance run (no window)
    python main.py --autotest t1                     # scripted smoke test (screenshots + checks, then quits)
"""
from __future__ import annotations

import argparse
import sys

from settings import GAME_TITLE, user_settings

MENU_TESTS = ("menu",)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=GAME_TITLE)
    ap.add_argument("--autotest", default=None, help="run a scripted smoke test and quit")
    ap.add_argument("--quick", nargs="*", default=None, help="map difficulty - skip the menus")
    ap.add_argument("--endless", action="store_true", help="with --quick: play endless mode")
    ap.add_argument("--sim", nargs="*", default=None, help="headless bot game(s): [map|all] [difficulty] [endless]")
    ap.add_argument("--speed", type=float, default=1.0, help="simulation speed multiplier")
    ap.add_argument("--novsync", action="store_true", help="uncapped frame rate")
    ap.add_argument("--size", default=None, help="window size WxH")
    ap.add_argument("--seed", type=int, default=1)
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.sim is not None:
        from core import sim
        sim.main(args.sim)
        return

    if args.autotest:
        import settings
        settings.PERSIST = False          # tests must not overwrite the player's settings or best scores

    from panda3d.core import loadPrcFileData
    loadPrcFileData("", "framebuffer-multisample 1\nmultisamples 4\n")
    loadPrcFileData("", "texture-anisotropic-degree 4\n")
    loadPrcFileData("", "window-title " + GAME_TITLE + "\n")
    if args.novsync or args.autotest:
        loadPrcFileData("", "sync-video 0\n")

    from ursina import Ursina

    size = tuple(user_settings.resolution)
    if args.size:
        w, h = args.size.lower().split("x")
        size = (int(w), int(h))
    fullscreen = bool(user_settings.fullscreen) and not args.autotest
    app = Ursina(title=GAME_TITLE, development_mode=False, fullscreen=fullscreen, borderless=False,
                 size=size, vsync=not (args.novsync or args.autotest), show_ursina_splash=False)
    from ursina import window
    if not fullscreen:
        window.center_on_screen()
    window.exit_button.enabled = False
    window.fps_counter.enabled = bool(user_settings.show_fps) and not args.autotest
    window.color = (0.02, 0.02, 0.025, 1)

    import core.game as game_module
    from core import data
    from core.game import Game, GameLoop
    from core.match import MatchConfig

    game = Game(app)
    game_module.game = game
    try:
        from core.audio import AudioSystem
        game.audio = AudioSystem(listener=lambda: (game.match.cam.fx, game.match.cam.fz) if game.match else None)
    except Exception as exc:  # pragma: no cover - audio is optional
        print("[audio] disabled:", exc)
    from ui.hud import HUD
    from ui.menus import MenuController
    game.hud = HUD(game)
    game.menus = MenuController(game)
    GameLoop(game)
    game.fast_forward = args.speed

    maps = data.maps()
    diffs = data.combat()["difficulty"]
    if args.autotest and not args.autotest.startswith(MENU_TESTS):
        from core.autotest import config_for
        game.start_match(config_for(args.autotest))
    elif args.quick is not None:
        q = args.quick
        map_id = q[0] if len(q) > 0 and q[0] in maps else user_settings.last_map
        diff = q[1] if len(q) > 1 and q[1] in diffs else "normal"
        game.start_match(MatchConfig(map_id=map_id if map_id in maps else next(iter(maps)), difficulty=diff,
                                     endless=args.endless, seed=args.seed))
    else:
        game.menus.show_main()

    if args.autotest:
        from core.autotest import AutoTest
        tester = AutoTest(game, args.autotest)
        game.on_update.append(tester.update)
    app.run(info=False)


if __name__ == "__main__":
    main()
