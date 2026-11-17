"""Portrait icons rendered from the real procedural models into an offscreen atlas (once per session)."""
from __future__ import annotations

import math

import numpy as np
from panda3d.core import (FrameBufferProperties, GraphicsOutput, GraphicsPipe, NodePath, OrthographicLens,
                          PTA_LVecBase4f, Texture as PTexture, Vec3, WindowProperties)
from PIL import Image, ImageDraw
from ursina import Texture

from core import data, shaders
from core.textures import np_to_texture
from settings import MAX_LEVEL, PLAYER_COLOR

CELL_PX = 112
_cache: dict[tuple, Texture] = {}


def _frame_bg(img: Image.Image, base: tuple[float, float, float]) -> Image.Image:
    """Put a portrait on a framed gradient background."""
    s = img.size[0]
    y, x = np.mgrid[0:s, 0:s].astype(np.float32)
    k = 1.0 - np.hypot(x - s * 0.5, y - s * 0.38) / s
    arr = np.stack([base[0] * (0.5 + k), base[1] * (0.5 + k), base[2] * (0.5 + k), np.ones_like(k)], -1)
    bg = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8), "RGBA")
    bg.alpha_composite(img)
    d = ImageDraw.Draw(bg)
    d.rectangle([0, 0, s - 1, s - 1], outline=(18, 12, 8, 255), width=3)
    d.rectangle([3, 3, s - 4, s - 4], outline=(255, 235, 190, 45), width=1)
    return bg


def render_portraits(base) -> None:
    """Render every tower level and every creep into the cache (does nothing once cached)."""
    from creeps.models import build_creep_rig
    from towers.models import tower_protos
    items: list[tuple] = [("tower", tid, lv) for tid in data.towers() for lv in range(1, MAX_LEVEL + 1)]
    items += [("creep", cid, 0) for cid in data.creeps()]
    items = [it for it in items if it not in _cache]
    if not items:
        return
    cols = 10
    rows = int(math.ceil(len(items) / cols))
    W, H = cols * CELL_PX, rows * CELL_PX
    fb = FrameBufferProperties()
    fb.setRgbColor(True)
    fb.setAlphaBits(8)
    fb.setDepthBits(24)
    wp = WindowProperties.size(W, H)
    tex = PTexture("portraits")
    buf = base.graphicsEngine.makeOutput(base.pipe, "portraits", -50, fb, wp, GraphicsPipe.BF_refuse_window,
                                         base.win.getGsg(), base.win)
    if buf is None:
        return
    buf.addRenderTexture(tex, GraphicsOutput.RTM_copy_ram)
    buf.setClearColor((0, 0, 0, 0))
    buf.setClearColorActive(True)
    scene = NodePath("portrait_scene")
    cam_np = base.makeCamera(buf)
    cam_np.reparentTo(scene)
    lens = OrthographicLens()
    cell = 1.0
    lens.setFilmSize(cols * cell, rows * cell)
    lens.setNearFar(-50, 50)
    cam_np.node().setLens(lens)
    view_dir = Vec3(-0.45, -0.5, -1.0)
    view_dir.normalize()
    cam_np.setPos(-view_dir * 20)
    cam_np.lookAt(Vec3(0, 0, 0), Vec3.up())
    right = scene.getRelativeVector(cam_np, Vec3.right())
    up = scene.getRelativeVector(cam_np, Vec3.up())
    scene.setShader(shaders.world_shader())
    sun = Vec3(-0.5, 0.8, 0.6)
    sun.normalize()
    scene.setShaderInput("u_sun_dir", sun)
    scene.setShaderInput("u_sun_color", Vec3(1.0, 0.92, 0.8))
    scene.setShaderInput("u_amb_sky", Vec3(0.58, 0.58, 0.66))
    scene.setShaderInput("u_amb_ground", Vec3(0.32, 0.27, 0.24))
    scene.setShaderInput("u_fog_color", Vec3(0, 0, 0))
    scene.setShaderInput("u_fog_range", (1000.0, 2000.0))
    scene.setShaderInput("u_cam_pos", cam_np.getPos(scene))
    scene.setShaderInput("u_lights_pos", PTA_LVecBase4f.emptyArray(8))
    scene.setShaderInput("u_lights_col", PTA_LVecBase4f.emptyArray(8))
    scene.setShaderInput("u_num_lights", 0)
    scene.setShaderInput("u_emissive", 0.0)
    scene.setShaderInput("u_detail", 0.0)
    scene.setShaderInput("u_rim", 0.3)
    scene.setShaderInput("u_flame", 0.0)
    white = np_to_texture(np.full((4, 4, 3), 255, np.uint8), "white", mipmap=False)
    scene.setShaderInput("u_detail_tex", white)
    rigs = []
    for k, (kind, tid, level) in enumerate(items):
        r, c = divmod(k, cols)
        holder = scene.attachNewNode("cell")
        if kind == "creep":
            rig = build_creep_rig(data.creeps()[tid]["model"])
            rig.update(0.016, 0.0)
            rig.root.reparentTo(holder)
            rigs.append(rig)
            model = rig.root
            model.setH(-28)
        else:
            protos = tower_protos(tid, level, PLAYER_COLOR)
            parts = protos["parts"]
            model = holder.attachNewNode("t")
            protos["base"].copyTo(model)
            if protos["glow"] is not None:
                protos["glow"].copyTo(model).setShaderInput("u_emissive", 1.6)
            head = model.attachNewNode("head")
            head.setPos(0, parts.pivot, 0)
            if protos["head"] is not None:
                protos["head"].copyTo(head)
            if protos["head_glow"] is not None:
                protos["head_glow"].copyTo(head).setShaderInput("u_emissive", 1.7)
            head.setH(30 if parts.aims else 0)
            model.setH(-20)
        lo, hi = model.getTightBounds(scene)
        if kind == "tower":
            lo = Vec3(lo.x, max(lo.y, 0.0), lo.z)          # ignore the buried foundation
        size = max(hi.x - lo.x, hi.y - lo.y, hi.z - lo.z, 0.01)
        sc = cell * (0.86 if kind == "creep" else 0.92) / size
        holder.setScale(sc)
        lo, hi = model.getTightBounds(scene)
        if kind == "tower":
            lo = Vec3(lo.x, max(lo.y, holder.getY(scene)), lo.z)
        centre = (lo + hi) * 0.5
        target = right * ((c - (cols - 1) / 2.0) * cell) + up * (((rows - 1) / 2.0 - r) * cell)
        holder.setPos(holder.getPos(scene) + target - centre)
    for _ in range(3):
        base.graphicsEngine.renderFrame()
    base.graphicsEngine.syncFrame()
    if tex.hasRamImage():
        raw = np.frombuffer(tex.getRamImageAs("RGBA"), np.uint8)
        img = raw.reshape(tex.getYSize(), tex.getXSize(), 4)[::-1]
        for k, item in enumerate(items):
            r, c = divmod(k, cols)
            sub = img[r * CELL_PX:(r + 1) * CELL_PX, c * CELL_PX:(c + 1) * CELL_PX].copy()
            pil = Image.fromarray(sub, "RGBA")
            if item[0] == "creep":
                boss = data.creeps()[item[1]].get("boss")
                bg = (0.34, 0.12, 0.16) if boss else (0.24, 0.16, 0.30)
            else:
                bg = tuple(data.towers()[item[1]].get("icon_bg", (0.2, 0.24, 0.3)))
            t = Texture(_frame_bg(pil, bg))
            t.filtering = "bilinear"
            _cache[item] = t
    for rig in rigs:
        rig.destroy()
    scene.removeNode()
    base.graphicsEngine.removeWindow(buf)


def tower_portrait(type_id: str, level: int = 1) -> Texture | None:
    return _cache.get(("tower", type_id, level))


def creep_portrait(type_id: str) -> Texture | None:
    return _cache.get(("creep", type_id, 0))
