"""Procedurally synthesised sound effects, ambience and music (numpy -> WAV cache -> Panda3D audio)."""
from __future__ import annotations

import math
import wave
from pathlib import Path
from typing import Callable

import numpy as np
from panda3d.core import AudioSound, Filename

from core.events import events
from settings import CACHE_DIR, user_settings

SR = 22050
AUDIO_VERSION = 1


def _t(dur: float) -> np.ndarray:
    return np.arange(int(SR * dur), dtype=np.float32) / SR


def _env(n: int, attack: float = 0.005, decay: float = 0.2, curve: float = 3.0) -> np.ndarray:
    t = np.arange(n, dtype=np.float32) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    d = np.exp(-np.maximum(t - attack, 0) / max(decay, 1e-4) * curve / 3.0)
    return a * d


def _noise(n: int, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).uniform(-1, 1, n).astype(np.float32)


def _lp(x: np.ndarray, k: int) -> np.ndarray:
    """Cheap low-pass: moving average of width k."""
    if k <= 1:
        return x
    c = np.cumsum(np.concatenate([[0.0], x.astype(np.float64)]))
    y = (c[k:] - c[:-k]) / k
    return np.concatenate([y, np.full(k - 1, y[-1] if len(y) else 0.0)]).astype(np.float32)


def _hp(x: np.ndarray, k: int) -> np.ndarray:
    return (x - _lp(x, k)).astype(np.float32)


def _sine(freq: np.ndarray | float, dur: float) -> np.ndarray:
    t = _t(dur)
    if np.isscalar(freq):
        return np.sin(2 * np.pi * float(freq) * t).astype(np.float32)
    phase = np.cumsum(np.asarray(freq, np.float32)) / SR
    return np.sin(2 * np.pi * phase).astype(np.float32)


_pluck_cache: dict[tuple, np.ndarray] = {}


def _pluck(freq: float, dur: float, decay: float = 0.996, seed: int = 3) -> np.ndarray:
    """Karplus-Strong plucked string."""
    key = (round(freq, 2), round(dur, 3), decay, seed)
    hit = _pluck_cache.get(key)
    if hit is not None:
        return hit
    n = int(SR * dur)
    period = max(2, int(SR / freq))
    buf = np.random.default_rng(seed).uniform(-1, 1, period).astype(np.float32).tolist()
    out = [0.0] * n
    k = decay * 0.5
    j = 0
    for i in range(n):
        v = buf[j]
        out[i] = v
        nj = j + 1 if j + 1 < period else 0
        buf[j] = k * (v + buf[nj])
        j = nj
    arr = np.asarray(out, np.float32)
    _pluck_cache[key] = arr
    return arr


def _note(freq: float, dur: float, harmonics: tuple = (1.0, 0.5, 0.25), decay: float = 0.5, attack: float = 0.01) -> np.ndarray:
    t = _t(dur)
    s = np.zeros_like(t)
    for i, a in enumerate(harmonics):
        s += a * np.sin(2 * np.pi * freq * (i + 1) * t)
    return (s * _env(len(t), attack, decay)).astype(np.float32)


def _mix(*parts: np.ndarray) -> np.ndarray:
    n = max(len(p) for p in parts)
    out = np.zeros(n, np.float32)
    for p in parts:
        out[: len(p)] += p
    return out


def _put(out: np.ndarray, start: float, x: np.ndarray, gain: float = 1.0) -> None:
    i = int(start * SR)
    if i >= len(out):
        return
    m = min(len(x), len(out) - i)
    out[i:i + m] += x[:m] * gain


def _seq(notes: list[tuple[float, float, float]], total: float, **kw) -> np.ndarray:
    out = np.zeros(int(SR * total), np.float32)
    for start, freq, dur in notes:
        _put(out, start, _note(freq, dur, **kw))
    return out


def _horn(freq: float, dur: float, vib: float = 4.0) -> np.ndarray:
    """Brassy horn tone (harmonics, slow attack, vibrato)."""
    t = _t(dur)
    f = freq * (1.0 + 0.006 * np.sin(2 * np.pi * vib * t))
    ph = np.cumsum(f) / SR
    s = np.zeros_like(t)
    for k, a in enumerate((1.0, 0.7, 0.45, 0.3, 0.18, 0.1)):
        s += a * np.sin(2 * np.pi * ph * (k + 1))
    env = np.clip(t / 0.08, 0, 1) * np.clip((dur - t) / 0.25, 0, 1)
    return (np.tanh(s * 0.8) * env * 0.35).astype(np.float32)


def _swell(n: int) -> np.ndarray:
    return np.clip(np.sin(np.linspace(0, np.pi, n, dtype=np.float32)), 0.0, 1.0)


# --------------------------------------------------------------------------- tower sounds
def s_bow() -> np.ndarray:
    tw = _pluck(240, 0.3, 0.99) * _env(int(SR * 0.3), 0.001, 0.16)
    n = int(SR * 0.16)
    whoosh = _lp(_noise(n, 2), 5) * _swell(n) ** 2
    return _mix(tw * 0.55, whoosh * 0.35)


def s_ballista() -> np.ndarray:
    n = int(SR * 0.4)
    thunk = _sine(np.linspace(190, 70, n), 0.4) * _env(n, 0.002, 0.1) * 0.8
    twang = _pluck(110, 0.4, 0.992, seed=5) * _env(n, 0.001, 0.25) * 0.6
    whoosh = _lp(_noise(n, 4), 4) * _swell(n) ** 3 * 0.4
    return _mix(thunk, twang, whoosh)


def s_cannon() -> np.ndarray:
    n = int(SR * 0.55)
    boom = _sine(np.linspace(140, 40, n), 0.55) * _env(n, 0.002, 0.22) * 1.0
    crack = _lp(_noise(n, 6), 5) * _env(n, 0.001, 0.07) * 0.9
    return _mix(boom, crack)


def s_boom() -> np.ndarray:
    n = int(SR * 0.7)
    boom = _sine(np.linspace(110, 34, n), 0.7) * _env(n, 0.003, 0.3) * 0.8
    roar = _lp(_noise(n, 34), 10) * _env(n, 0.004, 0.3) * 1.6
    return _mix(boom, roar)


def s_explosion() -> np.ndarray:
    n = int(SR * 1.4)
    boom = _sine(np.linspace(90, 26, n), 1.4) * _env(n, 0.003, 0.55) * 1.0
    roar = _lp(_noise(n, 35), 14) * _env(n, 0.005, 0.6) * 2.4
    crackle = _noise(n, 36) * (np.random.default_rng(37).random(n) > 0.985) * _env(n, 0.05, 0.8) * 0.5
    return _mix(boom, roar, crackle)


def s_frost() -> np.ndarray:
    notes = [(0.0, 1760, 0.3), (0.03, 2349, 0.3), (0.07, 2637, 0.35)]
    chime = _seq(notes, 0.45, harmonics=(1.0, 0.25), decay=0.12) * 0.22
    n = int(SR * 0.3)
    hiss = _hp(_noise(n, 9), 4) * _env(n, 0.002, 0.08) * 0.25
    return _mix(chime, hiss)


def s_flame() -> np.ndarray:
    n = int(SR * 0.3)
    return _lp(_noise(n, 6), 9) * _swell(n) * 1.1


def s_zap() -> np.ndarray:
    n = int(SR * 0.28)
    t = _t(0.28)
    rng = np.random.default_rng(12)
    gate = (rng.random(n) > 0.55).astype(np.float32)
    buzz = np.sign(np.sin(2 * np.pi * (900 - 1800 * t) * t)) * 0.3
    crack = _hp(_noise(n, 13), 3) * gate * 0.7
    return ((buzz + crack) * _env(n, 0.001, 0.12)).astype(np.float32) * 0.6


def s_spit() -> np.ndarray:
    n = int(SR * 0.2)
    pop = _sine(np.linspace(700, 180, n), 0.2) * _env(n, 0.002, 0.06) * 0.5
    wet = _lp(_noise(n, 14), 6) * _env(n, 0.004, 0.06) * 0.6
    return _mix(pop, wet)


def s_beam() -> np.ndarray:
    n = int(SR * 0.45)
    f = np.linspace(420, 880, n)
    hum = (_sine(f, 0.45) * 0.5 + _sine(f * 2.01, 0.45) * 0.25 + _sine(f * 3.0, 0.45) * 0.1) * _swell(n)
    return hum.astype(np.float32) * 0.3


# --------------------------------------------------------------------------- building & economy
def s_build() -> np.ndarray:
    out = np.zeros(int(SR * 0.55), np.float32)
    for k in range(3):
        n = int(SR * 0.08)
        knock = (_sine(np.linspace(900, 500, n), 0.08) * 0.4 + _lp(_noise(n, 30 + k), 3) * 0.5) * _env(n, 0.001, 0.03)
        _put(out, k * 0.15, knock)
    n = int(SR * 0.3)
    _put(out, 0.0, _sine(np.linspace(120, 60, n), 0.3) * _env(n, 0.002, 0.12) * 0.6)
    return out


def s_hammer() -> np.ndarray:
    out = np.zeros(int(SR * 0.4), np.float32)
    for k in range(2):
        n = int(SR * 0.1)
        hit = (_sine(1400, 0.1) * 0.3 + _sine(2100, 0.1) * 0.15 + _lp(_noise(n, 40 + k), 2) * 0.4) * _env(n, 0.001, 0.035)
        _put(out, k * 0.17, hit)
    return out


def s_upgrade() -> np.ndarray:
    notes = [(0.0, 523.3, 0.4), (0.09, 659.3, 0.4), (0.18, 784.0, 0.4), (0.27, 1046.5, 0.7), (0.27, 1318.5, 0.7)]
    return _seq(notes, 1.0, harmonics=(1.0, 0.35, 0.15), decay=0.3) * 0.3


def s_coin() -> np.ndarray:
    second = np.concatenate([np.zeros(int(SR * 0.05), np.float32), _note(2093, 0.15, (1.0, 0.3), 0.06) * 0.2])
    return _mix(_note(1568, 0.12, (1.0, 0.3), 0.05) * 0.2, second)


def s_click() -> np.ndarray:
    n = int(SR * 0.05)
    return _sine(1200, 0.05) * _env(n, 0.001, 0.015) * 0.25


def s_select() -> np.ndarray:
    return _note(660, 0.09, (1.0, 0.3), 0.05) * 0.25


def s_error() -> np.ndarray:
    n = int(SR * 0.18)
    return (np.sign(_sine(130, 0.18)) * 0.15 + _sine(97, 0.18) * 0.2) * _env(n, 0.005, 0.12)


# --------------------------------------------------------------------------- creeps & waves
def s_squish() -> np.ndarray:
    n = int(SR * 0.2)
    thud = _sine(np.linspace(240, 90, n), 0.2) * _env(n, 0.002, 0.06) * 0.6
    wet = _lp(_noise(n, 50), 7) * _env(n, 0.003, 0.07) * 0.7
    return _mix(thud, wet)


def s_boss_death() -> np.ndarray:
    n = int(SR * 1.6)
    t = _t(1.6)
    saw = ((t * (95 - 30 * t) + 0.3 * np.sin(t * 30)) % 1.0 - 0.5) * 2
    growl = _lp(saw.astype(np.float32) + _noise(n, 51) * 0.5, 6) * _swell(n) ** 0.5 * 0.5
    return _mix(growl, s_explosion()[:n] * 0.6)


def s_leak() -> np.ndarray:
    n = int(SR * 0.9)
    stab = _horn(98.0, 0.9, 5.0) * 1.2
    thud = _sine(np.linspace(80, 36, n), 0.9) * _env(n, 0.002, 0.25) * 0.9
    hiss = _hp(_noise(n, 52), 3) * _env(n, 0.01, 0.2) * 0.15
    return _mix(stab, thud, hiss)


def s_wave_start() -> np.ndarray:
    out = np.zeros(int(SR * 1.5), np.float32)
    _put(out, 0.0, _horn(196.0, 0.5))
    _put(out, 0.42, _horn(261.6, 1.0))
    _put(out, 0.42, _horn(392.0, 1.0), 0.5)
    return out


def s_boss_warning() -> np.ndarray:
    out = np.zeros(int(SR * 2.6), np.float32)
    _put(out, 0.0, _horn(87.3, 1.2, 3.0), 1.3)
    _put(out, 1.0, _horn(82.4, 1.5, 3.0), 1.3)
    _put(out, 1.0, _horn(123.5, 1.5, 3.0), 0.7)
    for k in range(4):
        n = int(SR * 0.4)
        _put(out, k * 0.6, _sine(np.linspace(70, 36, n), 0.4) * _env(n, 0.002, 0.2), 0.9)
    return out


def s_wave_clear() -> np.ndarray:
    notes = [(0.0, 392.0, 0.3), (0.12, 523.3, 0.3), (0.24, 659.3, 0.6)]
    return _seq(notes, 0.9, harmonics=(1.0, 0.4, 0.2), decay=0.3) * 0.28


def s_victory() -> np.ndarray:
    notes = [(0.0, 261.6, 0.5), (0.25, 329.6, 0.5), (0.5, 392.0, 0.5), (0.8, 523.3, 1.6), (0.8, 392.0, 1.6), (0.8, 659.3, 1.6)]
    return _mix(_seq(notes, 2.8, harmonics=(1.0, 0.6, 0.35, 0.2), decay=0.9) * 0.4, _horn(130.8, 2.4) * 0.5)


def s_defeat() -> np.ndarray:
    notes = [(0.0, 392.0, 0.8), (0.6, 349.2, 0.8), (1.2, 311.1, 0.9), (1.9, 261.6, 1.8), (1.9, 155.6, 1.8)]
    return _seq(notes, 4.0, harmonics=(1.0, 0.5, 0.3), decay=1.0) * 0.4


# --------------------------------------------------------------------------- powers
def s_meteor() -> np.ndarray:
    n = int(SR * 0.9)
    whistle = _sine(np.linspace(1500, 260, n), 0.9) * np.linspace(0.1, 1.0, n, dtype=np.float32) ** 2 * 0.3
    rush = _lp(_noise(n, 60), 8) * np.linspace(0.0, 1.0, n, dtype=np.float32) ** 2 * 0.8
    return _mix(whistle, rush)


def s_nova() -> np.ndarray:
    n = int(SR * 1.0)
    crash = _hp(_noise(n, 61), 3) * _env(n, 0.002, 0.25) * 0.5
    notes = [(0.0, 1318.5, 0.9), (0.02, 1975.5, 0.9), (0.05, 2637.0, 0.9), (0.08, 3136.0, 0.8)]
    chime = _seq(notes, 1.0, harmonics=(1.0, 0.2), decay=0.3) * 0.16
    low = _sine(np.linspace(300, 80, n), 1.0) * _env(n, 0.002, 0.3) * 0.5
    return _mix(crash, chime, low)


def s_overcharge() -> np.ndarray:
    n = int(SR * 0.9)
    f = np.linspace(180, 1100, n) ** 1.0
    sweep = (_sine(f, 0.9) * 0.4 + _sine(f * 1.5, 0.9) * 0.2) * _swell(n)
    buzz = _hp(_noise(n, 62), 2) * (np.random.default_rng(63).random(n) > 0.7) * np.linspace(0, 1, n, dtype=np.float32) * 0.25
    return _mix(sweep.astype(np.float32) * 0.5, buzz)


# --------------------------------------------------------------------------- loops
def _loop(x: np.ndarray, fade: float = 0.5) -> np.ndarray:
    """Make a seamless loop by cross-fading the tail into the head."""
    f = int(SR * fade)
    head, body, tail = x[:f], x[f:-f], x[-f:]
    w = np.linspace(0, 1, f, dtype=np.float32)
    return np.concatenate([body, tail * (1 - w) + head * w])


def s_wind() -> np.ndarray:
    n = int(SR * 9.0)
    t = _t(9.0)
    base = _lp(_noise(n, 16), 40) * 6.0
    gust = 0.55 + 0.45 * np.sin(t * 2 * np.pi / 4.5) * np.sin(t * 2 * np.pi / 9.0 + 1.0)
    whistle = _lp(_noise(n, 17), 3) * 0.06 * (0.5 + 0.5 * np.sin(t * 1.4))
    return _loop((base * gust + whistle) * 0.5, 1.0)


def s_night() -> np.ndarray:
    n = int(SR * 8.0)
    out = np.zeros(n, np.float32)
    rng = np.random.default_rng(22)
    for _ in range(26):
        i = int(rng.integers(0, n - SR // 3))
        f = rng.uniform(3800, 4600)
        chirp = np.concatenate([_note(f, 0.05, (1.0,), 0.02), np.zeros(int(SR * 0.03), np.float32)] * 3) * 0.08
        out[i:i + len(chirp)] += chirp[: n - i]
    return _loop(out + _lp(_noise(n, 23), 40) * 0.6, 0.8)


_A = {"A2": 110.0, "C3": 130.81, "D3": 146.83, "E3": 164.81, "F3": 174.61, "G3": 196.0, "A3": 220.0, "B3": 246.94,
      "C4": 261.63, "D4": 293.66, "E4": 329.63, "F4": 349.23, "G4": 392.0, "A4": 440.0, "B4": 493.88, "C5": 523.25,
      "D5": 587.33, "E5": 659.25, "F2": 87.31, "G2": 98.0, "D2": 73.42, "E2": 82.41, "C2": 65.41, "Bb2": 116.54,
      "Bb3": 233.08, "F5": 698.46, "G5": 783.99, "A5": 880.0}


def _pad(freqs: tuple[float, ...], dur: float) -> np.ndarray:
    t = _t(dur)
    env = np.clip(t / (dur * 0.3), 0, 1) * np.clip((dur - t) / (dur * 0.3), 0, 1)
    s = np.zeros_like(t)
    for f in freqs:
        for det in (0.996, 1.0, 1.004):
            s += np.sin(2 * np.pi * f * det * t) * 0.1
        s += np.sin(2 * np.pi * f * 2 * t) * 0.03
    return (s * env * (0.85 + 0.15 * np.sin(t * 1.1))).astype(np.float32)


def s_theme() -> np.ndarray:
    """Menu theme: a slow lute melody in A minor over soft string pads and a distant horn."""
    bar = 4.0
    chords = [("A2", "A3", "C4", "E4"), ("F2", "F3", "A3", "C4"), ("C3", "G3", "C4", "E4"), ("G2", "G3", "B3", "D4"),
              ("A2", "A3", "C4", "E4"), ("D3", "F3", "A3", "D4"), ("E2", "E3", "B3", "E4"), ("A2", "E3", "A3", "C4")]
    total = bar * len(chords)
    n = int(SR * total)
    out = np.zeros(n + int(SR * 3), np.float32)
    for i, ch in enumerate(chords):
        _put(out, i * bar - 0.4 if i else 0.0, _pad(tuple(_A[k] for k in ch), bar + 1.4), 0.5)
        # rolling lute arpeggio
        for k, step in enumerate((0, 1, 2, 3, 2, 1, 2, 3)):
            _put(out, i * bar + k * 0.5, _pluck(_A[ch[step]] * 2, 1.1, 0.995, seed=3 + k), 0.2)
    melody = [(0, "E5", 1.5), (1.5, "D5", 0.5), (2, "C5", 1), (3, "B4", 1), (4, "A4", 1.5), (5.5, "C5", 0.5), (6, "F5", 2),
              (8, "E5", 1.5), (9.5, "D5", 0.5), (10, "C5", 1), (11, "E5", 1), (12, "D5", 2), (14, "B4", 2),
              (16, "E5", 1), (17, "G5", 1), (18, "A5", 1.5), (19.5, "G5", 0.5), (20, "F5", 1), (21, "E5", 1), (22, "D5", 2),
              (24, "E5", 1.5), (25.5, "D5", 0.5), (26, "B4", 2), (28, "A4", 3.5)]
    for t0, name, dur in melody:
        _put(out, t0, _pluck(_A[name], dur + 0.6, 0.997, seed=11), 0.42)
    for t0, name in ((6.0, "A3"), (14.0, "G3"), (22.0, "D4"), (28.0, "E3")):
        _put(out, t0, _horn(_A[name], 2.4, 3.0), 0.3)
    return _loop(out[: n + int(SR * 2.0)] * 0.55, 2.0)


def s_battle() -> np.ndarray:
    """In-game loop: a driving plucked ostinato in D minor with low drums."""
    beat = 0.5                                      # 120 bpm
    bars = [("D2", "D3", "F3", "A3"), ("D2", "D3", "F3", "A3"), ("Bb2", "D3", "F3", "Bb3"), ("C3", "E3", "G3", "C4"),
            ("D2", "D3", "F3", "A3"), ("F2", "F3", "A3", "C4"), ("G2", "G3", "Bb3", "D4"), ("A2", "E3", "A3", "C4")]
    total = len(bars) * 4 * beat
    n = int(SR * total)
    out = np.zeros(n + int(SR * 2), np.float32)
    for i, ch in enumerate(bars):
        t0 = i * 4 * beat
        _put(out, t0, _pad(tuple(_A[k] for k in ch[1:]), 4 * beat + 0.6), 0.32)
        for k in range(8):                           # eighth-note ostinato
            name = ch[(1, 2, 3, 2, 1, 3, 2, 3)[k]]
            _put(out, t0 + k * beat * 0.5, _pluck(_A[name] * 2, 0.5, 0.99, seed=20 + k), 0.2)
        for k in (0, 2.5):                           # bass
            _put(out, t0 + k * beat, _pluck(_A[ch[0]], 1.0, 0.996, seed=30), 0.5)
        for k in range(4):                           # drums
            m = int(SR * 0.3)
            kick = _sine(np.linspace(85, 40, m), 0.3) * _env(m, 0.002, 0.14)
            _put(out, t0 + k * beat, kick, 0.6 if k % 2 == 0 else 0.3)
            if k % 2 == 1:
                m2 = int(SR * 0.12)
                _put(out, t0 + k * beat, _hp(_noise(m2, 70 + k), 3) * _env(m2, 0.001, 0.04), 0.16)
    lead = [(0, "A4", 1.5), (1.5, "F4", 0.5), (2, "D4", 2), (4, "A4", 1), (5, "C5", 1), (6, "A4", 2),
            (8, "Bb3", 1.0), (9, "D4", 1), (10, "F4", 2), (12, "G4", 1.5), (13.5, "E4", 0.5), (14, "C4", 2)]
    for t0, name, dur in lead:
        _put(out, t0 * beat * 2, _horn(_A[name] * 0.5, dur * beat * 2 + 0.2, 4.5), 0.34)
        _put(out, (t0 + 16) * beat * 2 if t0 < 8 else (t0 + 8) * beat * 2, _note(_A[name], dur * beat * 2, (1.0, 0.4, 0.2), 0.5, 0.03), 0.07)
    return _loop(out[: n + int(SR * 1.0)] * 0.5, 1.0)


RECIPES: dict[str, Callable[[], np.ndarray]] = {
    "bow": s_bow, "ballista": s_ballista, "cannon": s_cannon, "boom": s_boom, "explosion": s_explosion, "frost": s_frost,
    "flame": s_flame, "zap": s_zap, "spit": s_spit, "beam": s_beam,
    "build": s_build, "hammer": s_hammer, "upgrade": s_upgrade, "coin": s_coin, "click": s_click, "select": s_select,
    "error": s_error, "squish": s_squish, "boss_death": s_boss_death, "leak": s_leak, "wave_start": s_wave_start,
    "boss_warning": s_boss_warning, "wave_clear": s_wave_clear, "victory": s_victory, "defeat": s_defeat,
    "meteor": s_meteor, "nova": s_nova, "overcharge": s_overcharge,
    "amb_wind": s_wind, "amb_night": s_night, "theme": s_theme, "battle": s_battle,
}
MUSIC = ("theme", "battle")
# minimum seconds between two plays of the same sound (keeps big fights from turning into noise)
GAPS = {"bow": 0.07, "flame": 0.16, "zap": 0.1, "spit": 0.1, "squish": 0.07, "cannon": 0.1, "boom": 0.1, "frost": 0.09,
        "beam": 0.3, "ballista": 0.1, "coin": 0.05}


def _write_wav(path: Path, x: np.ndarray) -> None:
    peak = float(np.max(np.abs(x))) if len(x) else 1.0
    if peak > 0.98:
        x = x / peak * 0.98
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def generate_all(directory: Path) -> None:
    """Synthesise every sound that is not cached yet."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, fn in RECIPES.items():
        path = directory / f"{name}.wav"
        if not path.exists():
            _write_wav(path, fn())


class AudioSystem:
    """Generates (once), caches and plays all game sounds."""

    POOL = 4
    HEAR = 95.0          # sounds further than this from the camera focus are silent

    def __init__(self, listener: Callable[[], tuple[float, float] | None] | None = None) -> None:
        from ursina.audio import _audio_manager
        self.mgr = _audio_manager
        self.listener = listener
        self.dir = CACHE_DIR / f"sounds_v{AUDIO_VERSION}"
        generate_all(self.dir)
        self.sounds: dict[str, list[AudioSound]] = {}
        self._rr: dict[str, int] = {}
        self._last: dict[str, float] = {}
        self._clock = 0.0
        self.loops: dict[str, AudioSound] = {}
        self.loop_vol: dict[str, float] = {}
        self.loop_target: dict[str, float] = {}
        for name in RECIPES:
            path = self.dir / f"{name}.wav"
            if name.startswith("amb_") or name in MUSIC:
                snd = self.mgr.getSound(Filename.fromOsSpecific(str(path)))
                snd.setLoop(True)
                snd.setVolume(0.0)
                self.loops[name] = snd
                self.loop_vol[name] = 0.0
                self.loop_target[name] = 0.0
            else:
                self.sounds[name] = [self.mgr.getSound(Filename.fromOsSpecific(str(path))) for _ in range(self.POOL)]
        events.on("sound", self._on_sound)

    def _on_sound(self, name: str, volume: float = 1.0, pos: tuple[float, float] | None = None, **_: object) -> None:
        if pos is not None and self.listener is not None:
            lp = self.listener()
            if lp is not None:
                d = math.hypot(pos[0] - lp[0], pos[1] - lp[1])
                if d > self.HEAR:
                    return
                volume *= max(0.25, 1.0 - d / self.HEAR) ** 1.3
        if self._clock - self._last.get(name, -1.0) < GAPS.get(name, 0.04):
            return
        self._last[name] = self._clock
        self.play(name, volume, pitch=0.93 + 0.14 * ((self._rr.get(name, 0) * 37) % 10) / 10.0)

    def play(self, name: str, volume: float = 1.0, pitch: float = 1.0) -> None:
        pool = self.sounds.get(name)
        if not pool:
            return
        i = self._rr.get(name, 0)
        self._rr[name] = (i + 1) % len(pool)
        s = pool[i]
        s.setVolume(max(0.0, min(1.0, volume * user_settings.master_volume * user_settings.sfx_volume)))
        s.setPlayRate(pitch)
        s.play()

    def set_ambience(self, levels: dict[str, float]) -> None:
        """Target volume per looping track (0..1); they fade smoothly."""
        for name in self.loops:
            self.loop_target[name] = levels.get(name, 0.0)

    def update(self, dt: float) -> None:
        self._clock += dt
        for name, snd in self.loops.items():
            cur = self.loop_vol[name]
            tgt = self.loop_target[name]
            if abs(cur - tgt) > 0.001:
                cur += (tgt - cur) * min(1.0, dt * 1.2)
                self.loop_vol[name] = cur
            group = user_settings.music_volume if name in MUSIC else user_settings.ambient_volume
            v = cur * user_settings.master_volume * group
            snd.setVolume(v)
            playing = snd.status() == AudioSound.PLAYING
            if v > 0.002 and not playing:
                snd.play()
            elif v <= 0.002 and playing:
                snd.stop()
