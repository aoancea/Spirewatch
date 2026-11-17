"""Procedurally drawn 64x64 icons (PIL): resources, commands, targeting modes and powers."""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from ursina import Texture

S = 64
_cache: dict[tuple, Texture] = {}

LIGHT = (0.95, 0.92, 0.85)
METAL = (0.78, 0.8, 0.84)
WOOD = (0.55, 0.36, 0.2)
GOLD = (1.0, 0.82, 0.25)


def _bg(col: tuple[float, float, float], frame: bool = True) -> Image.Image:
    y, x = np.mgrid[0:S, 0:S].astype(np.float32)
    dx, dy = (x - S * 0.4) / S, (y - S * 0.35) / S
    k = np.maximum(0.35, 1.15 - np.sqrt(dx * dx + dy * dy) * 1.3)
    n = ((x * 7 + y * 13) % 5) * 0.01
    arr = np.stack([col[0] * k + n, col[1] * k + n, col[2] * k + n, np.ones_like(k)], -1)
    img = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8), "RGBA")
    if frame:
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, S - 1, S - 1], outline=(20, 14, 10, 255), width=3)
        d.rectangle([3, 3, S - 4, S - 4], outline=(255, 255, 255, 40), width=1)
    return img


def _c(col: tuple, a: int = 255) -> tuple[int, int, int, int]:
    return (int(col[0] * 255), int(col[1] * 255), int(col[2] * 255), a)


def _glyph(d: ImageDraw.ImageDraw, g: str) -> None:
    c, a, w, gold = _c(LIGHT), _c(METAL), _c(WOOD), _c(GOLD)
    if g == "gold":
        for x, y in ((22, 38), (40, 38), (31, 24)):
            d.ellipse([x - 12, y - 8, x + 12, y + 8], fill=gold, outline=(120, 80, 10, 255), width=2)
    elif g == "heart":
        d.polygon([(32, 6), (46, 28), (44, 46), (32, 58), (20, 46), (18, 28)], fill=_c((1.0, 0.5, 0.12)))
        d.polygon([(32, 24), (40, 40), (32, 54), (24, 40)], fill=_c((1.0, 0.9, 0.35)))
    elif g == "wave":
        d.ellipse([16, 10, 48, 42], fill=c, outline=(40, 30, 30, 255), width=2)
        d.rectangle([24, 36, 40, 50], fill=c, outline=(40, 30, 30, 255))
        for x in (24, 36):
            d.ellipse([x - 1, 20, x + 7, 30], fill=(30, 20, 30, 255))
        for x in (28, 32, 36):
            d.line([(x, 42), (x, 50)], fill=(40, 30, 30, 255), width=1)
    elif g == "upgrade":
        d.polygon([(32, 6), (54, 30), (42, 30), (42, 56), (22, 56), (22, 30), (10, 30)], fill=_c((0.95, 0.8, 0.3)),
                  outline=(90, 60, 10, 255))
    elif g == "sell":
        d.ellipse([10, 18, 44, 52], fill=gold, outline=(120, 80, 10, 255), width=3)
        d.line([(27, 26), (27, 44)], fill=(120, 80, 10, 255), width=3)
        d.polygon([(40, 8), (58, 8), (58, 26)], fill=_c((1.0, 0.35, 0.3)))
        d.line([(42, 24), (56, 10)], fill=_c((1.0, 0.35, 0.3)), width=5)
    elif g == "send":
        d.polygon([(8, 24), (30, 18), (52, 8), (56, 30), (50, 52), (30, 44), (8, 40)], fill=_c((0.85, 0.7, 0.4)),
                  outline=(60, 40, 20, 255))
        d.ellipse([46, 14, 60, 48], fill=_c((0.3, 0.2, 0.12)))
    elif g == "pause":
        d.rectangle([18, 14, 28, 50], fill=c)
        d.rectangle([36, 14, 46, 50], fill=c)
    elif g == "play":
        d.polygon([(18, 12), (50, 32), (18, 52)], fill=c)
    elif g == "fast":
        d.polygon([(8, 14), (30, 32), (8, 50)], fill=c)
        d.polygon([(32, 14), (56, 32), (32, 50)], fill=c)
    elif g == "x":
        d.line([(16, 16), (48, 48)], fill=_c((1.0, 0.3, 0.25)), width=8)
        d.line([(48, 16), (16, 48)], fill=_c((1.0, 0.3, 0.25)), width=8)
    # ---- targeting modes
    elif g == "first":
        d.polygon([(10, 22), (34, 22), (34, 12), (56, 32), (34, 52), (34, 42), (10, 42)], fill=_c((0.4, 0.95, 0.5)),
                  outline=(20, 60, 20, 255))
    elif g == "last":
        d.polygon([(54, 22), (30, 22), (30, 12), (8, 32), (30, 52), (30, 42), (54, 42)], fill=_c((0.95, 0.75, 0.35)),
                  outline=(70, 50, 10, 255))
    elif g == "strong":
        d.polygon([(32, 6), (52, 14), (50, 36), (32, 58), (14, 36), (12, 14)], fill=a, outline=(40, 40, 45, 255))
        d.line([(32, 12), (32, 50)], fill=_c((0.6, 0.15, 0.1)), width=5)
    elif g == "weak":
        d.ellipse([18, 10, 46, 38], fill=c, outline=(40, 30, 30, 255), width=2)
        d.line([(12, 46), (52, 58)], fill=c, width=5)
        d.line([(52, 46), (12, 58)], fill=c, width=5)
    elif g == "near":
        d.ellipse([10, 10, 54, 54], outline=c, width=3)
        d.ellipse([20, 20, 44, 44], outline=c, width=3)
        d.ellipse([28, 28, 36, 36], fill=_c((1.0, 0.35, 0.3)))
    # ---- powers
    elif g == "meteor":
        d.polygon([(8, 6), (30, 22), (22, 30)], fill=_c((1.0, 0.85, 0.35)))
        d.polygon([(16, 4), (40, 26), (26, 40), (4, 18)], fill=_c((1.0, 0.5, 0.12)))
        d.ellipse([26, 26, 56, 56], fill=_c((0.42, 0.26, 0.2)), outline=_c((1.0, 0.6, 0.2)), width=3)
        d.ellipse([34, 34, 42, 42], fill=_c((0.25, 0.15, 0.12)))
    elif g == "snow":
        for k in range(6):
            ang = math.radians(k * 60)
            x, y = 32 + math.cos(ang) * 24, 32 + math.sin(ang) * 24
            d.line([(32, 32), (x, y)], fill=_c((0.85, 0.96, 1.0)), width=4)
            for s in (-1, 1):
                bx, by = 32 + math.cos(ang) * 15, 32 + math.sin(ang) * 15
                d.line([(bx, by), (bx + math.cos(ang + s * 0.9) * 8, by + math.sin(ang + s * 0.9) * 8)],
                       fill=_c((0.85, 0.96, 1.0)), width=3)
        d.ellipse([27, 27, 37, 37], fill=(255, 255, 255, 255))
    elif g == "bolt":
        pts = [(38, 6), (20, 34), (32, 34), (24, 58), (46, 26), (34, 26), (42, 6)]
        d.polygon(pts, fill=_c((1.0, 0.95, 0.5)), outline=(255, 255, 255, 255))
    elif g == "back":
        d.polygon([(10, 32), (30, 14), (30, 26), (54, 26), (54, 38), (30, 38), (30, 50)], fill=c)
    else:
        d.ellipse([18, 18, 46, 46], fill=c)


def _to_texture(img: Image.Image) -> Texture:
    t = Texture(img.filter(ImageFilter.SMOOTH))
    t.filtering = "bilinear"
    return t


def icon(glyph: str, bg: tuple = (0.3, 0.3, 0.3), frame: bool = True) -> Texture:
    """A framed glyph icon (cached)."""
    key = (glyph, tuple(bg), frame)
    if key not in _cache:
        img = _bg(tuple(bg), frame)
        _glyph(ImageDraw.Draw(img), glyph)
        _cache[key] = _to_texture(img)
    return _cache[key]


def spell_icon(sid: str) -> Texture:
    from core import data
    ic = data.spells()[sid].get("icon", {})
    return icon(ic.get("glyph", ""), tuple(ic.get("bg", (0.3, 0.3, 0.3))))


MODE_BG = {"first": (0.12, 0.3, 0.14), "last": (0.32, 0.24, 0.1), "strong": (0.2, 0.22, 0.32), "weak": (0.3, 0.14, 0.14),
           "near": (0.14, 0.24, 0.3)}


def mode_icon(mode: str) -> Texture:
    return icon(mode, MODE_BG.get(mode, (0.3, 0.3, 0.3)))
