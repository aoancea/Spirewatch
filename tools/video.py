"""Records the gameplay video in docs/ straight from the running game.

    pip install imageio-ffmpeg          # bundles an ffmpeg binary (or have ffmpeg on the PATH)
    python tools/video.py

The game runs on a fixed 1/30 s clock, so every rendered frame is one video frame and the result is smooth
however long a frame takes to capture. A scripted storyboard plays a few scenes on the three maps; a bot
fast-forwards between them off camera. The soundtrack is not recorded from the speakers: every sound the game
asks for while filming is logged with its time, volume and pitch, and the same procedural sounds and music
loops are mixed offline. The game itself is muted while recording.

Output: docs/gameplay.mp4 (with sound) and docs/gameplay.gif (a silent excerpt for the README).
"""
from __future__ import annotations

import math
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any, Callable

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FPS = 30
SIZE = (1280, 720)
DOCS = ROOT / "docs"
WORK = ROOT / "screenshots" / "video"
GIF_LIMIT_MB = 9.5


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        exe = shutil.which("ffmpeg")
        if exe is None:
            raise SystemExit("ffmpeg not found: pip install imageio-ffmpeg")
        return exe


class Recorder:
    """Grabs frames into an ffmpeg pipe, logs sound events, and animates the camera and a fade overlay."""

    def __init__(self, game) -> None:
        from panda3d.core import ClockObject
        from ursina import Entity, camera, color
        self.game = game
        self.base = game.base
        clock = ClockObject.getGlobalClock()
        clock.setMode(ClockObject.MNonRealTime)       # one rendered frame == exactly 1/FPS of game time
        clock.setDt(1.0 / FPS)
        WORK.mkdir(parents=True, exist_ok=True)
        self.master = WORK / "master.mp4"
        self.proc: subprocess.Popen | None = None
        self.on = False
        self.frames = 0
        self.sfx: list[tuple[float, str, float, float]] = []
        self.loops: list[dict[str, float]] = []
        self.marks: dict[str, float] = {}
        self.tweens: list[list[Any]] = []
        self.follow: Callable[[], tuple[float, float] | None] | None = None
        self.black = Entity(parent=camera.ui, model="quad", scale=(4, 2), color=color.rgba(0, 0, 0, 1), z=-90)
        self.alpha = 1.0
        self._color = color
        self._mute()
        game.on_update.append(self.update)

    # ------------------------------------------------------------------ sound log
    def _mute(self) -> None:
        """Silence the speakers and log what the game wants to play instead."""
        from settings import user_settings
        from ui.widgets import Tooltip
        audio = self.game.audio
        user_settings.master_volume = 0.0
        Tooltip.show = lambda *a, **k: None           # the real mouse pointer must not pop tooltips into the film
        if audio is None:
            return

        def play(name: str, volume: float = 1.0, pitch: float = 1.0) -> None:
            if self.on:
                self.sfx.append((self.frames / FPS, name, float(volume), float(pitch)))

        audio.play = play

    # ------------------------------------------------------------------ controls
    def resume(self, _g=None) -> None:
        self.on = True

    def pause(self, _g=None) -> None:
        self.on = False

    def mark(self, name: str) -> None:
        self.marks[name] = self.frames / FPS

    def tween(self, obj: Any, attr: str, end: float, dur: float) -> None:
        self.tweens.append([obj, attr, float(getattr(obj, attr)), float(end), 0.0, max(dur, 1e-3)])

    def fade(self, alpha: float, dur: float) -> None:
        self.tween(self, "alpha", alpha, dur)

    def cam(self, x: float, z: float, dist: float, dur: float = 0.0) -> None:
        c = self.game.match.cam
        self.follow = None
        if dur <= 0:
            c.fx, c.fz, c.distance, c.target_distance = x, z, dist, dist
            c.apply()
        else:
            self.tween(c, "fx", x, dur)
            self.tween(c, "fz", z, dur)
            self.tween(c, "target_distance", dist, dur)

    # ------------------------------------------------------------------ per frame
    def update(self, dt: float) -> None:
        for tw in list(self.tweens):
            obj, attr, a, b, t, dur = tw
            tw[4] = t = t + dt
            k = min(1.0, t / dur)
            k = k * k * (3 - 2 * k)
            setattr(obj, attr, a + (b - a) * k)
            if t >= dur:
                self.tweens.remove(tw)
        m = self.game.match
        if self.follow is not None and m is not None:
            p = self.follow()
            if p is not None:
                m.cam.fx += (p[0] - m.cam.fx) * min(1.0, dt * 2.5)
                m.cam.fz += (p[1] - m.cam.fz) * min(1.0, dt * 2.5)
        self.black.color = self._color.rgba(0, 0, 0, max(0.0, min(1.0, self.alpha)))
        if not self.on:
            return
        tex = self.base.win.getScreenshot()
        if tex is None:
            return
        w, h = tex.getXSize(), tex.getYSize()
        if self.proc is None:
            self.proc = subprocess.Popen(
                [ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(FPS),
                 "-i", "-", "-vf", "vflip", "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p",
                 str(self.master)], stdin=subprocess.PIPE)
        self.proc.stdin.write(np.frombuffer(tex.getRamImageAs("RGB"), np.uint8).tobytes())
        audio = self.game.audio
        self.loops.append(dict(audio.loop_vol) if audio is not None else {})
        self.frames += 1

    # ------------------------------------------------------------------ output
    def soundtrack(self) -> np.ndarray:
        """Mix the logged sounds and music loops at the game's default volume settings."""
        from core import audio as A
        sr = A.SR
        total = int(self.frames / FPS * sr) + sr // 2
        out = np.zeros(total, np.float32)
        cache: dict[str, np.ndarray] = {}

        def clip(name: str) -> np.ndarray:
            x = cache.get(name)
            if x is None:
                x = A.RECIPES[name]().astype(np.float32)
                peak = float(np.max(np.abs(x))) if len(x) else 1.0
                if peak > 0.98:                        # the game's WAV writer normalises loud clips the same way
                    x = x / peak * 0.98
                cache[name] = x
            return x

        master, sfx_vol, music, ambient = 0.8, 0.9, 0.6, 0.5
        for t, name, vol, pitch in self.sfx:
            if name not in A.RECIPES:
                continue
            x = clip(name)
            if abs(pitch - 1.0) > 1e-3:
                x = np.interp(np.arange(0, len(x) - 1, pitch), np.arange(len(x)), x).astype(np.float32)
            i = int(t * sr)
            n = min(len(x), total - i)
            if n > 0:
                out[i:i + n] += x[:n] * (min(1.0, vol) * master * sfx_vol)
        if self.loops:
            frame_t = np.arange(len(self.loops)) / FPS
            sample_t = np.arange(total) / sr
            for name in self.loops[0]:
                env = np.interp(sample_t, frame_t, [fr.get(name, 0.0) for fr in self.loops]).astype(np.float32)
                if float(env.max()) <= 0.002:
                    continue
                x = clip(name)
                track = np.tile(x, total // len(x) + 1)[:total]
                out += track * env * (master * (music if name in A.MUSIC else ambient))
        raw_peak = float(np.max(np.abs(out)))
        out = np.tanh(out * 1.15)                      # gentle limiter for the busy moments
        fade_in, fade_out = int(0.4 * sr), int(1.0 * sr)
        out[:fade_in] *= np.linspace(0, 1, fade_in, dtype=np.float32)
        out[-fade_out:] *= np.linspace(1, 0, fade_out, dtype=np.float32)
        peak = float(np.max(np.abs(out)))
        if peak > 0:
            out *= 0.89 / peak
        rms = float(np.sqrt(np.mean(out ** 2)))
        print(f"[video] soundtrack: {len(self.sfx)} sound events, raw peak {raw_peak:.2f}, final peak 0.89, rms {rms:.3f}", flush=True)
        return out

    def finish(self, _g=None) -> None:
        """Close the video, mix the sound, and write docs/gameplay.mp4 and docs/gameplay.gif."""
        from core import audio as A
        self.on = False
        if self.proc is None:
            print("[video] nothing was recorded", flush=True)
            return
        self.proc.stdin.close()
        self.proc.wait()
        seconds = self.frames / FPS
        wav_path = WORK / "soundtrack.wav"
        pcm = (np.clip(self.soundtrack(), -1, 1) * 32767).astype(np.int16)
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(A.SR)
            w.writeframes(pcm.tobytes())
        exe = ffmpeg_exe()
        DOCS.mkdir(exist_ok=True)
        mp4 = DOCS / "gameplay.mp4"
        subprocess.run([exe, "-y", "-loglevel", "error", "-i", str(self.master), "-i", str(wav_path), "-c:v", "libx264",
                        "-preset", "slow", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                        "-movflags", "+faststart", "-t", f"{seconds:.3f}", str(mp4)], check=True)
        # the README preview: a silent excerpt, shrunk until it fits comfortably in the repository
        start = self.marks.get("gif_start", 0.0)
        length = max(4.0, self.marks.get("gif_end", seconds) - start)
        gif = DOCS / "gameplay.gif"
        for width, fps, colors in ((720, 12, 160), (640, 12, 128), (640, 10, 112), (560, 10, 96), (480, 10, 80)):
            vf = (f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors={colors}:stats_mode=diff[p];"
                  f"[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
            subprocess.run([exe, "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(self.master),
                            "-vf", vf, str(gif)], check=True)
            if gif.stat().st_size <= GIF_LIMIT_MB * 1024 * 1024:
                break
        print(f"[video] {seconds:.1f}s, {self.frames} frames -> {mp4.name} {mp4.stat().st_size / 1e6:.1f} MB, "
              f"{gif.name} {gif.stat().st_size / 1e6:.1f} MB ({width}px, {fps} fps, {length:.1f}s from {start:.1f}s)", flush=True)
        print(f"[video] marks: { {k: round(v, 1) for k, v in self.marks.items()} }", flush=True)


# ====================================================================== storyboard
def storyboard(g) -> list[tuple]:
    from core import autotest as T
    from core.bot import Bot
    from core.match import MatchConfig
    rec = Recorder(g)
    st: dict[str, Any] = {}

    def player(g) -> None:
        """Route the 'mouse' through the script: the film must not depend on where the real pointer is."""
        ctl = g.controls
        ctl.over_ui = lambda: False
        if "hud_input" not in st:
            st["hud_input"] = g.hud.input
            g.hud.input = lambda key: False if "mouse" in key else st["hud_input"](key)
        g.match.cam.edge_scroll = False

    def aim(col: int, row: int):
        def f(g):
            cell = T._cell(g, col, row)
            g.controls.cell_under_mouse = lambda: cell
        return f

    def aim_point(fn: Callable[[Any], tuple[float, float]]):
        def f(g):
            p = fn(g)
            g.controls.ground_point = lambda: p
        return f

    def start(map_id: str):
        def f(g):
            g.menus._launch(MatchConfig(map_id=map_id, difficulty="normal"))
            player(g)
        return f

    def bot_on(g) -> None:
        g.bot = Bot(g.match)
        g.match.auto_send = True
        g.match.waves.countdown = min(g.match.waves.countdown, 2.0)

    def hub(g) -> tuple[float, float]:
        towers = [t for t in g.match.towers if t.kind != "aura"]
        best = max(towers, key=lambda t: sum(1 for o in towers if (o.x - t.x) ** 2 + (o.z - t.z) ** 2 < 64.0))
        st["hub"] = (best.x, best.z)
        return st["hub"]

    def near_hub(g) -> list:
        hx, hz = st["hub"]
        return [c for c in g.match.creeps if c.alive and (c.x - hx) ** 2 + (c.z - hz) ** 2 < 170.0]

    def pack(g, lead: float = 0.0) -> tuple[float, float]:
        """Centre of the densest knot of creeps near the towers, led by ``lead`` seconds of their walking."""
        creeps = near_hub(g) or [c for c in g.match.creeps if c.alive]
        if not creeps:
            return st["hub"]
        c = max(creeps, key=lambda c: sum(1 for o in creeps if (o.x - c.x) ** 2 + (o.z - c.z) ** 2 < 9.0))
        a = math.radians(c.face)
        return c.x + math.sin(a) * c.speed_now * lead, c.z + math.cos(a) * c.speed_now * lead

    def wave(n: int):
        return lambda g: g.match.waves.number >= n or g.match.over

    def chase(g) -> tuple[float, float]:
        """Camera target that sticks with one creep in the thick of the swarm (and picks another when it dies)."""
        c = st.get("chase")
        if c is None or not c.alive:
            creeps = [o for o in g.match.creeps if o.alive and not o.flying]
            if not creeps:
                return g.match.cam.fx, g.match.cam.fz
            c = st["chase"] = max(creeps, key=lambda c: sum(1 for o in creeps if (o.x - c.x) ** 2 + (o.z - c.z) ** 2 < 16.0))
        return c.x, c.z - 2.5

    def boss(g):
        return next((c for c in g.match.creeps if c.alive and c.boss), None)

    def cut(*middle: tuple) -> list[tuple]:
        """Dip to black, do something off camera, come back."""
        return [("call", lambda g: rec.fade(1.0, 0.3)), ("wait", 0.34), ("call", rec.pause), ("speed", 1), *middle,
                ("speed", 1), ("wait", 0.1), ("call", rec.resume), ("call", lambda g: rec.fade(0.0, 0.35)), ("wait", 0.35)]

    steps: list[tuple] = [
        # ---- title: the main menu over its self-playing backdrop
        ("speed", 3), ("wait", 3.0), ("speed", 1), ("wait", 0.2),
        ("call", rec.resume), ("call", lambda g: rec.fade(0.0, 0.6)), ("wait", 3.6),

        # ---- Emberfall Road: place two towers, call the first wave
        *cut(("call", start("emberfall_road")), ("wait", 0.3), ("call", lambda g: rec.cam(-21.0, 9.0, 35.0))),
        ("wait", 0.7),
        ("press", "q"), ("call", aim(2, 4)), ("wait", 0.25), ("call", aim(3, 3)), ("wait", 0.25), ("call", aim(4, 3)), ("wait", 0.4),
        ("press", "left mouse down"), ("wait", 0.5),
        ("press", "q"), ("call", aim(5, 3)), ("wait", 0.22), ("call", aim(6, 3)), ("wait", 0.22), ("call", aim(7, 3)), ("wait", 0.4),
        ("press", "left mouse down"), ("wait", 0.9),
        ("press", "space"), ("call", lambda g: rec.cam(-20.0, 9.5, 30.0, 4.0)), ("wait", 5.6),

        # ---- same game, a dozen waves later: a swarm meets Meteor and Frost Nova
        *cut(("call", bot_on), ("speed", 8), ("until", wave(13), 600), ("call", lambda g: hub(g) and None),
             ("until", lambda g: len(near_hub(g)) >= 12 or g.match.waves.number > 13, 120), ("speed", 1),
             ("call", lambda g: (g.match.env.__setattr__("hour", 14.0), rec.cam(st["hub"][0], st["hub"][1] - 2.5, 31.0),
                                 rec.mark("gif_start"))[0])),
        ("wait", 1.3),
        ("call", aim_point(lambda g: pack(g, 1.0))), ("press", "1"), ("wait", 0.55), ("press", "left mouse down"), ("wait", 2.6),
        ("call", aim_point(lambda g: pack(g, 0.0))), ("press", "2"), ("wait", 0.45), ("press", "left mouse down"), ("wait", 2.8),

        # ---- Twin Rifts at dusk: the Behemoth walks into the prisms
        *cut(("call", start("twin_rifts")), ("call", bot_on), ("speed", 8),
             ("until", lambda g: (g.match.waves.number >= 20 and boss(g) is not None and boss(g).hp_frac < 0.93)
              or g.match.waves.number > 20 or g.match.over, 900),
             ("speed", 1),
             ("call", lambda g: (g.match.env.__setattr__("hour", 19.0),
                                 rec.cam(*(((boss(g).x, boss(g).z - 2.0) if boss(g) else hub(g)) + (30.0,))),
                                 g.controls.select(boss(g), sound=False),
                                 setattr(rec, "follow", lambda: (boss(g).x, boss(g).z - 2.0) if boss(g) else None))[0])),
        ("wait", 2.4),
        ("call", aim_point(lambda g: (boss(g).x, boss(g).z) if boss(g) else hub(g))), ("press", "3"), ("wait", 0.45),
        ("press", "left mouse down"), ("wait", 4.8),
        ("call", lambda g: rec.mark("gif_end")),

        # ---- The Hollow Moor: a swarm threads the maze
        *cut(("call", start("hollow_moor")), ("call", bot_on), ("speed", 8), ("until", wave(26), 900),
             ("until", lambda g: sum(1 for c in g.match.creeps if c.alive) >= 30 or g.match.waves.number > 26, 120), ("speed", 1),
             ("call", lambda g: (g.match.env.__setattr__("hour", 15.8), rec.cam(0.0, -6.0, 66.0), setattr(g.match, "show_paths", True))[0])),
        ("wait", 3.6),
        ("call", lambda g: (rec.cam(*chase(g), 31.0, 1.6), setattr(g.match, "show_paths", False))[0]),
        ("wait", 1.6), ("call", lambda g: setattr(rec, "follow", lambda: chase(g))), ("wait", 4.2),
        ("call", lambda g: rec.fade(1.0, 0.7)), ("wait", 0.8),
        ("call", rec.finish), ("quit",),
    ]
    return steps


def main() -> None:
    from core import autotest
    autotest.SCRIPTS["video"] = storyboard
    autotest.MENU_SCRIPTS.add("video")
    import main as game_main
    game_main.main(["--autotest", "video", "--size", f"{SIZE[0]}x{SIZE[1]}"])


if __name__ == "__main__":
    main()
