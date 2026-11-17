"""Visual effects: projectile models, batched particles, beams and lightning, shockwave rings, floating text.

Nothing here affects the simulation. Headless matches use :class:`NullFX`, which swallows every call.
"""
from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING, Any, Callable

import numpy as np
from panda3d.core import (ColorBlendAttrib, Geom, GeomNode, GeomTriangles, GeomVertexData, NodePath, OmniBoundingVolume,
                          TransparencyAttrib, Vec3)

from core.events import events
from core.meshgen import MeshBuilder, _vertex_format, make_geom_node
from core.textures import np_to_texture, radial_texture, ring_texture

if TYPE_CHECKING:
    from core.match import Match


class NullFX:
    """Stand-in used when a match runs without graphics."""

    def __getattr__(self, name: str) -> Callable[..., None]:
        def _noop(*args: Any, **kwargs: Any) -> None:
            return None
        return _noop


PARTICLE_COLORS = {
    "spark": (1.0, 0.85, 0.5), "fire": (1.0, 0.5, 0.12), "ember": (1.0, 0.3, 0.06), "ichor": (0.55, 0.2, 0.7),
    "heal": (0.4, 1.0, 0.5), "frost": (0.65, 0.88, 1.0), "dust": (0.72, 0.62, 0.48), "gold": (1.0, 0.85, 0.3),
    "smoke": (0.24, 0.22, 0.22), "venom": (0.5, 1.0, 0.3), "storm": (0.7, 0.8, 1.0), "sun": (1.0, 0.92, 0.6),
    "rift": (0.8, 0.4, 1.0), "debris": (0.5, 0.46, 0.42), "red": (1.0, 0.25, 0.2),
}

_protos: dict[str, NodePath] = {}


def _proto(name: str) -> NodePath:
    node = _protos.get(name)
    if node is None:
        mb = MeshBuilder()
        if name == "arrow":
            mb.beam((0, 0, -0.45), (0, 0, 0.45), 0.03, (0.55, 0.4, 0.25, 1), segments=3)
            mb.cone((0, 0, 0.45), 0.06, 0.16, (0.75, 0.75, 0.78, 1), rot=(90, 0, 0), segments=4)
            mb.box((0, 0, -0.4), (0.15, 0.01, 0.13), (0.92, 0.92, 0.88, 1))
        elif name == "bolt":
            mb.beam((0, 0, -0.8), (0, 0, 0.8), 0.05, (0.5, 0.36, 0.22, 1), segments=4)
            mb.cone((0, 0, 0.8), 0.11, 0.36, (0.8, 0.8, 0.84, 1), rot=(90, 0, 0), segments=4)
            for r in (0, 90):
                mb.box((0, 0, -0.7), (0.26, 0.012, 0.22), (0.25, 0.45, 0.9, 1), rot=(0, 0, r))
        elif name == "shell":
            mb.sphere((0, 0, 0), 0.2, (0.2, 0.2, 0.22, 1), detail=1)
        elif name == "frost":
            mb.cone((0, 0, 0), 0.16, 0.42, (0.65, 0.88, 1.0, 1), segments=4, rot=(90, 0, 0))
            mb.cone((0, 0, 0), 0.16, 0.25, (0.9, 0.97, 1.0, 1), segments=4, rot=(-90, 0, 0))
        elif name == "venom":
            mb.sphere((0, 0, 0), (0.16, 0.16, 0.24), (0.5, 1.0, 0.3, 1), detail=1)
            mb.sphere((0, 0, -0.2), 0.09, (0.75, 1.0, 0.5, 1), detail=0)
        elif name == "meteor":
            mb.sphere((0, 0, 0), 0.75, (0.3, 0.2, 0.18, 1), detail=1, noise=0.3, seed=5)
            mb.sphere((0, 0.1, 0), 0.62, (1.0, 0.5, 0.1, 1), detail=1, noise=0.35, seed=8)
            mb.cone((0, 0.3, 0), 0.6, 2.4, (1.0, 0.75, 0.3, 1), segments=6)
        node = mb.build(name)
        _protos[name] = node
    return node


EMISSIVE = {"frost": 1.8, "venom": 1.7, "meteor": 1.9}


class ParticleBatch:
    """Up to ``cap`` camera-facing square particles drawn in one call."""

    def __init__(self, parent: NodePath, cap: int = 1500) -> None:
        self.cap = cap
        self.pos = np.zeros((cap, 3), np.float32)
        self.vel = np.zeros((cap, 3), np.float32)
        self.col = np.zeros((cap, 4), np.float32)
        self.size = np.zeros(cap, np.float32)
        self.life = np.zeros(cap, np.float32)
        self.age = np.zeros(cap, np.float32)
        self.grav = np.zeros(cap, np.float32)
        self.n = 0
        self._used = 0
        self.vdata = GeomVertexData("particles", _vertex_format(True), Geom.UH_dynamic)
        self.vdata.uncleanSetNumRows(cap * 4)
        self.buf = np.zeros((cap * 4, 12), np.float32)
        self.buf[:, 3:6] = (0, 1, 0)
        self.buf[:, 10:12] = np.tile(np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32), (cap, 1))
        memoryview(self.vdata.modifyArray(0)).cast("B")[:] = self.buf.tobytes()
        prim = GeomTriangles(Geom.UH_static)
        prim.setIndexType(Geom.NT_uint32)
        base = (np.arange(cap, dtype=np.uint32) * 4)[:, None]
        idx = (base + np.array([0, 1, 2, 0, 2, 3], np.uint32)[None, :]).ravel()
        parr = prim.modifyVertices()
        parr.uncleanSetNumRows(len(idx))
        memoryview(parr).cast("B")[:] = idx.tobytes()
        prim.closePrimitive()
        geom = Geom(self.vdata)
        geom.addPrimitive(prim)
        gn = GeomNode("particles")
        gn.addGeom(geom)
        gn.setBounds(OmniBoundingVolume())
        gn.setFinal(True)
        self.node = parent.attachNewNode(gn)
        self.node.setTexture(radial_texture(32, 0.0, 1.0, (255, 255, 255), power=1.2), 1)
        self.node.setTransparency(TransparencyAttrib.M_alpha)
        self.node.setDepthWrite(False)
        self.node.setBin("transparent", 30)
        self.node.setShaderInput("u_emissive", 1.0)
        self.node.setTwoSided(True)

    def emit(self, pos: tuple[float, float, float], color: tuple[float, float, float], count: int, speed: float,
             life: float, size: float, gravity: float = 9.0, up: float = 1.0, alpha: float = 1.0,
             direction: tuple[float, float, float] | None = None, spread: float = 0.35, jitter: float = 0.0) -> None:
        count = min(count, self.cap - self.n)
        if count <= 0:
            return
        s = slice(self.n, self.n + count)
        v = np.random.uniform(-1, 1, (count, 3)).astype(np.float32)
        if direction is None:
            v[:, 1] = np.random.uniform(0.2, 1.0, count) * up
            v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-6
        else:
            v = v * spread + np.asarray(direction, np.float32)[None, :]
        self.pos[s] = pos
        if jitter:
            self.pos[s] += np.random.uniform(-jitter, jitter, (count, 3)).astype(np.float32)
        self.vel[s] = v * speed * np.random.uniform(0.45, 1.0, (count, 1))
        self.col[s, :3] = color
        self.col[s, 3] = alpha
        self.size[s] = size * np.random.uniform(0.6, 1.3, count)
        self.life[s] = life * np.random.uniform(0.7, 1.2, count)
        self.age[s] = 0
        self.grav[s] = gravity
        self.n += count

    def update(self, dt: float, cam_right: np.ndarray, cam_up: np.ndarray) -> None:
        n = self.n
        if n:
            self.age[:n] += dt
            alive = self.age[:n] < self.life[:n]
            if not alive.all():
                k = int(alive.sum())
                for arr in (self.pos, self.vel, self.col, self.size, self.life, self.age, self.grav):
                    arr[:k] = arr[:n][alive]
                n = self.n = k
        buf = self.buf
        prev_used = self._used
        if n:
            self.vel[:n, 1] -= self.grav[:n] * dt
            self.vel[:n] *= (1.0 - min(0.9, 1.2 * dt))
            self.pos[:n] += self.vel[:n] * dt
            fade = np.clip(1.0 - self.age[:n] / self.life[:n], 0, 1)
            s = self.size[:n, None]
            r = cam_right[None, :] * s
            u = cam_up[None, :] * s
            p = self.pos[:n]
            v = buf[: n * 4].reshape(n, 4, 12)
            v[:, 0, 0:3] = p - r - u
            v[:, 1, 0:3] = p + r - u
            v[:, 2, 0:3] = p + r + u
            v[:, 3, 0:3] = p - r + u
            c = self.col[:n].copy()
            c[:, 3] *= fade
            v[:, :, 6:10] = c[:, None, :]
        if prev_used > n:
            buf[n * 4: prev_used * 4, 0:3] = 0
            buf[n * 4: prev_used * 4, 9] = 0
        self._used = n
        if n or prev_used:
            memoryview(self.vdata.modifyArray(0)).cast("B")[:] = buf.tobytes()

    def clear(self) -> None:
        self.n = 0


class BeamBatch:
    """Camera-facing glowing line segments (prism beams, lightning), additively blended, one draw call."""

    def __init__(self, parent: NodePath, cap: int = 400) -> None:
        self.cap = cap
        self.n = 0
        self._used = 0
        self.p0 = np.zeros((cap, 3), np.float32)
        self.p1 = np.zeros((cap, 3), np.float32)
        self.w = np.zeros(cap, np.float32)
        self.col = np.zeros((cap, 4), np.float32)
        self.vdata = GeomVertexData("beams", _vertex_format(True), Geom.UH_dynamic)
        self.vdata.uncleanSetNumRows(cap * 4)
        self.buf = np.zeros((cap * 4, 12), np.float32)
        self.buf[:, 3:6] = (0, 1, 0)
        self.buf[:, 10:12] = np.tile(np.array([[0, 0], [0, 1], [1, 1], [1, 0]], np.float32), (cap, 1))
        memoryview(self.vdata.modifyArray(0)).cast("B")[:] = self.buf.tobytes()
        prim = GeomTriangles(Geom.UH_static)
        prim.setIndexType(Geom.NT_uint32)
        base = (np.arange(cap, dtype=np.uint32) * 4)[:, None]
        idx = (base + np.array([0, 1, 2, 0, 2, 3], np.uint32)[None, :]).ravel()
        parr = prim.modifyVertices()
        parr.uncleanSetNumRows(len(idx))
        memoryview(parr).cast("B")[:] = idx.tobytes()
        prim.closePrimitive()
        geom = Geom(self.vdata)
        geom.addPrimitive(prim)
        gn = GeomNode("beams")
        gn.addGeom(geom)
        gn.setBounds(OmniBoundingVolume())
        gn.setFinal(True)
        self.node = parent.attachNewNode(gn)
        # soft bright core across the width (v), constant along the length (u)
        v = np.abs(np.linspace(-1, 1, 32, dtype=np.float32))
        a = np.clip(1.0 - v, 0, 1) ** 1.6
        img = np.zeros((32, 4, 4), np.uint8)
        img[..., :3] = 255
        img[..., 3] = (a[:, None] * 255).astype(np.uint8)
        self.node.setTexture(np_to_texture(img, "beam", mipmap=False, repeat=False), 1)
        self.node.setTransparency(TransparencyAttrib.M_alpha)
        self.node.setAttrib(ColorBlendAttrib.make(ColorBlendAttrib.M_add, ColorBlendAttrib.O_incoming_alpha, ColorBlendAttrib.O_one))
        self.node.setDepthWrite(False)
        self.node.setBin("transparent", 32)
        self.node.setShaderInput("u_emissive", 1.0)
        self.node.setTwoSided(True)

    def add(self, p0: tuple, p1: tuple, width: float, color: tuple, alpha: float = 1.0) -> None:
        if self.n >= self.cap:
            return
        i = self.n
        self.p0[i] = p0
        self.p1[i] = p1
        self.w[i] = width
        self.col[i] = (color[0], color[1], color[2], alpha)
        self.n += 1

    def flush(self, cam_pos: np.ndarray) -> None:
        """Write this frame's segments and reset for the next frame."""
        n = self.n
        buf = self.buf
        if n:
            p0, p1 = self.p0[:n], self.p1[:n]
            d = p1 - p0
            side = np.cross(d, cam_pos[None, :] - (p0 + p1) * 0.5)
            ln = np.linalg.norm(side, axis=1, keepdims=True)
            side = side / np.maximum(ln, 1e-6) * (self.w[:n, None] * 0.5)
            v = buf[: n * 4].reshape(n, 4, 12)
            v[:, 0, 0:3] = p0 - side
            v[:, 1, 0:3] = p0 + side
            v[:, 2, 0:3] = p1 + side
            v[:, 3, 0:3] = p1 - side
            v[:, :, 6:10] = self.col[:n, None, :]
        if self._used > n:
            buf[n * 4: self._used * 4, 0:3] = 0
            buf[n * 4: self._used * 4, 9] = 0
        if n or self._used:
            memoryview(self.vdata.modifyArray(0)).cast("B")[:] = buf.tobytes()
        self._used = n
        self.n = 0


class Ring:
    """Expanding flat ring (shockwaves, ability feedback) or a fading decal."""

    def __init__(self, node: NodePath, radius: float, color: tuple, life: float, grow: bool = True) -> None:
        self.node = node
        self.radius = radius
        self.life = life
        self.color = color
        self.grow = grow
        self.t = 0.0

    def update(self, dt: float) -> bool:
        self.t += dt
        k = min(1.0, self.t / self.life)
        self.node.setScale(self.radius * (0.25 + 0.75 * k) if self.grow else self.radius)
        self.node.setAlphaScale(max(0.0, 1.0 - k * k) * self.color[3])
        return self.t < self.life


class FloatingText:
    """Pooled UI texts that rise from a world position and fade (gold, crits, lost lives)."""

    POOL = 36

    def __init__(self, match: "Match") -> None:
        from ursina import Text, camera
        self.match = match
        self.pool = [Text(parent=camera.ui, text="", origin=(0, 0), scale=1.0, enabled=False, z=0.3) for _ in range(self.POOL)]
        self.live: list[list[Any]] = []        # [text, x, y, z, age, life, rgb, rise]

    def spawn(self, x: float, y: float, z: float, text: str, color: tuple, scale: float = 1.0, life: float = 1.0,
              rise: float = 1.6) -> None:
        from ursina import color as ucolor
        if not self.pool:
            old = self.live.pop(0)
            self.pool.append(old[0])
        t = self.pool.pop()
        t.text = text
        t.scale = scale
        t.color = ucolor.rgba(color[0], color[1], color[2], 1)
        t.enabled = True
        self.live.append([t, x, y, z, 0.0, life, color, rise])
        self._place(self.live[-1])

    def _place(self, item: list[Any]) -> None:
        t, x, y, z, age, life, col, rise = item
        p = self.match.cam.project_one(x, y + age * rise, z)
        if p is None or abs(p[1]) > 0.5:
            t.visible = False
            return
        t.visible = True
        t.position = (p[0], p[1], 0.3)

    def update(self, dt: float) -> None:
        from ursina import color as ucolor
        keep = []
        for item in self.live:
            item[4] += dt
            t, age, life, col = item[0], item[4], item[5], item[6]
            if age >= life:
                t.enabled = False
                self.pool.append(t)
                continue
            self._place(item)
            t.color = ucolor.rgba(col[0], col[1], col[2], min(1.0, (life - age) / (life * 0.4)))
            keep.append(item)
        self.live = keep

    def clear(self) -> None:
        for item in self.live:
            item[0].enabled = False
            self.pool.append(item[0])
        self.live = []

    def destroy(self) -> None:
        from ursina import destroy
        self.clear()
        for t in self.pool:
            destroy(t)
        self.pool = []


class EffectsManager:
    """Owns every short-lived visual of a match."""

    def __init__(self, match: "Match") -> None:
        self.match = match
        self.root = match.fx_root.attachNewNode("effects")
        self.particles = ParticleBatch(self.root)
        self.beams = BeamBatch(self.root)
        self.rings: list[Ring] = []
        self.bolts: list[list[Any]] = []           # [segments, age, life, color]
        self.falling: list[dict[str, Any]] = []    # meteors on their way down
        self.ring_tex = ring_texture(128, 0.16)
        self.disc_tex = radial_texture(64, 0.0, 1.0, (255, 255, 255), power=0.9)
        self.scorch_tex = radial_texture(64, 0.2, 1.0, (0, 0, 0), power=1.4)
        self._quad_proto: NodePath | None = None
        self.text = FloatingText(match)
        self._t = 0.0

    # ------------------------------------------------------------------ helpers
    def _flat_quad(self, tex, bin_order: int = 6) -> NodePath:
        if self._quad_proto is None:
            pos = np.array([[-1, 0, -1], [1, 0, -1], [1, 0, 1], [-1, 0, 1]], np.float32)
            uvs = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32)
            nrm = np.tile(np.array([0, 1, 0], np.float32), (4, 1))
            col = np.ones((4, 4), np.float32)
            self._quad_proto = make_geom_node(pos, nrm, col, uvs=uvs, indices=np.array([0, 1, 2, 0, 2, 3], np.uint32), name="fxquad")
        node = self._quad_proto.copyTo(self.root)
        node.setTexture(tex, 1)
        node.setTransparency(TransparencyAttrib.M_alpha)
        node.setDepthWrite(False)
        node.setBin("transparent", bin_order)
        node.setShaderInput("u_emissive", 1.0)
        node.setTwoSided(True)
        return node

    def burst(self, pos, kind: str = "spark", count: int = 8, speed: float = 4.0, life: float = 0.45, size: float = 0.13,
              gravity: float = 9.0, up: float = 1.0, jitter: float = 0.0) -> None:
        c = PARTICLE_COLORS.get(kind, PARTICLE_COLORS["spark"])
        self.particles.emit((pos[0], pos[1], pos[2]), c, count, speed, life, size, gravity, up, jitter=jitter)

    def ring(self, x: float, y: float, z: float, radius: float, color: tuple, life: float = 0.5, grow: bool = True,
             filled: bool = False) -> None:
        node = self._flat_quad(self.disc_tex if filled else self.ring_tex)
        node.setPos(x, y + 0.12, z)
        node.setColorScale(color[0], color[1], color[2], 1)
        r = Ring(node, radius, color, life, grow)
        r.update(0.0)
        self.rings.append(r)

    def float_text(self, x: float, y: float, z: float, text: str, color: tuple, scale: float = 1.0, life: float = 1.0) -> None:
        self.text.spawn(x, y, z, text, color, scale, life)

    def _ground(self, x: float, z: float) -> float:
        return self.match.level.height(x, z)

    # ------------------------------------------------------------------ projectiles
    def projectile_spawned(self, p) -> None:
        node = _proto(p.kind).copyTo(self.root)
        if p.kind in EMISSIVE:
            node.setShaderInput("u_emissive", EMISSIVE[p.kind])
        node.setPos(p.sx, p.sy, p.sz)
        p.node = node

    def projectile_landed(self, p, silent: bool = False) -> None:
        if p.node is not None:
            p.node.removeNode()
            p.node = None

    def tower_fired(self, tower, sx: float, sy: float, sz: float) -> None:
        if tower.view is not None:
            tower.view.kick(1.0 if tower.type_id in ("cannon", "ballista") else 0.4)
        if tower.type_id == "cannon":
            a = math.radians(tower.aim_yaw)
            self.particles.emit((sx, sy, sz), PARTICLE_COLORS["smoke"], 7, 2.6, 0.7, 0.3, -1.0,
                                direction=(math.sin(a), 0.25, math.cos(a)), spread=0.5, alpha=0.8)
            self.particles.emit((sx, sy, sz), PARTICLE_COLORS["fire"], 5, 5.0, 0.18, 0.22, 0.0,
                                direction=(math.sin(a), 0.1, math.cos(a)), spread=0.3)

    # ------------------------------------------------------------------ hits
    def creep_hit(self, c, type_id: str, dealt: float, crit: bool = False) -> None:
        if c.node is None:
            return
        kind = {"arrow": "spark", "ballista": "spark", "venom": "venom", "frost": "frost", "cannon": "fire"}.get(type_id, "spark")
        self.burst((c.x, c.center_y(), c.z), kind, 4, 3.0, 0.32, 0.1)
        if crit:
            from settings import user_settings
            if user_settings.damage_numbers:
                self.float_text(c.x, c.y + c.height + 0.3, c.z, f"{int(round(dealt))}!", (1.0, 0.9, 0.3), 1.05, 0.8)

    def impale(self, a, b) -> None:
        self.bolts.append([[((a.x, a.center_y(), a.z), (b.x, b.center_y(), b.z))], 0.0, 0.12, (0.85, 0.9, 1.0), 0.1])

    def splash(self, x: float, z: float, radius: float, type_id: str, stunned: bool = False) -> None:
        y = self._ground(x, z)
        if type_id == "frost":
            self.burst((x, y + 0.5, z), "frost", 14, 3.6, 0.55, 0.16, gravity=2.0)
            self.ring(x, y, z, radius, (0.6, 0.88, 1.0, 0.8), 0.4)
        else:
            self.burst((x, y + 0.4, z), "fire", 16, 5.5, 0.4, 0.26, gravity=5.0)
            self.burst((x, y + 0.4, z), "smoke", 8, 2.2, 1.0, 0.4, gravity=-1.4)
            self.burst((x, y + 0.2, z), "debris", 6, 5.0, 0.6, 0.1)
            self.ring(x, y, z, radius, (1.0, 0.72, 0.4, 0.85), 0.35)
            if stunned:
                self.ring(x, y, z, radius * 1.15, (1.0, 1.0, 0.8, 0.9), 0.5)
            events.emit("sound", name="boom", pos=(x, z), volume=0.6)
            self.match.cam.shake(0.12, 0.15)

    def frozen(self, c) -> None:
        self.burst((c.x, c.center_y(), c.z), "frost", 10, 2.5, 0.6, 0.15, gravity=1.0)

    def lightning(self, pts: list[tuple[float, float, float]]) -> None:
        segs = []
        for a, b in zip(pts[:-1], pts[1:]):
            ax, ay, az = a
            bx, by, bz = b
            n = max(2, int(math.dist(a, b) / 0.9))
            prev = a
            for k in range(1, n + 1):
                f = k / n
                j = 0.0 if k == n else 0.28
                p = (ax + (bx - ax) * f + random.uniform(-j, j), ay + (by - ay) * f + random.uniform(-j, j),
                     az + (bz - az) * f + random.uniform(-j, j))
                segs.append((prev, p))
                prev = p
            self.burst(b, "storm", 5, 3.0, 0.25, 0.12, gravity=2.0)
        self.bolts.append([segs, 0.0, 0.24, (0.72, 0.82, 1.0), 0.22])

    def flame_jet(self, tower, yaw: float, rng: float, cone: float) -> None:
        sx, sy, sz = tower.muzzle()
        a = math.radians(yaw)
        speed = rng / 0.42
        self.particles.emit((sx, sy, sz), PARTICLE_COLORS["fire"], 5, speed, 0.42, 0.3, -1.5,
                            direction=(math.sin(a), -0.18, math.cos(a)), spread=math.radians(cone) * 0.42)
        self.particles.emit((sx, sy, sz), PARTICLE_COLORS["sun"], 2, speed * 0.9, 0.3, 0.2, -1.0,
                            direction=(math.sin(a), -0.18, math.cos(a)), spread=math.radians(cone) * 0.3)

    def beam(self, p0: tuple, p1: tuple, power: float) -> None:
        """A prism beam for this frame; ``power`` is 0..1 along the tower's ramp."""
        flick = 0.9 + 0.1 * math.sin(self._t * 40.0)
        self.beams.add(p0, p1, 0.2 + 0.3 * power, (1.0, 0.82, 0.35), 0.55 * flick)
        self.beams.add(p0, p1, 0.07 + 0.1 * power, (1.0, 0.98, 0.85), 0.95)
        if random.random() < 0.3 + 0.4 * power:
            self.burst(p1, "sun", 1, 2.5, 0.3, 0.1 + 0.08 * power, gravity=-1.0)

    # ------------------------------------------------------------------ creeps
    def creep_died(self, c, gold: int) -> None:
        if c.node is None:
            return
        y = c.center_y()
        if c.boss:
            self.burst((c.x, y, c.z), "rift", 50, 8.0, 1.1, 0.35, gravity=3.0)
            self.burst((c.x, y, c.z), "smoke", 20, 3.0, 1.8, 0.7, gravity=-1.2)
            self.ring(c.x, c.y, c.z, 6.0, (0.85, 0.5, 1.0, 0.9), 0.8)
            self.match.cam.shake(0.6, 0.5)
        else:
            self.burst((c.x, y, c.z), "ichor", 7, 3.4, 0.5, 0.14)
        if gold > 0:
            self.float_text(c.x, c.y + c.height + 0.2, c.z, f"+{gold}", (1.0, 0.85, 0.3), 1.25 if c.boss else 0.9, 1.0)

    def creep_leaked(self, c) -> None:
        m = self.match
        fx, fy, fz = m.level.flame_pos
        self.burst((fx, fy, fz), "red", 26, 6.0, 0.8, 0.3, gravity=2.0)
        self.ring(fx, self._ground(fx, fz), fz, 4.5, (1.0, 0.25, 0.2, 0.95), 0.6)
        self.float_text(fx, fy + 1.0, fz, f"-{c.lives if c.lives < 90 else 'ALL'}", (1.0, 0.3, 0.25), 1.5, 1.4)
        m.cam.shake(0.35, 0.3)

    def heal_pulse(self, healer, radius: float, healed: list) -> None:
        self.ring(healer.x, healer.y, healer.z, radius, (0.4, 1.0, 0.5, 0.55), 0.6)
        for c in healed[:10]:
            self.burst((c.x, c.y + 0.5, c.z), "heal", 4, 1.6, 0.7, 0.12, gravity=-3.5)

    def poison_spread(self, c, radius: float) -> None:
        self.ring(c.x, c.y, c.z, radius, (0.5, 1.0, 0.3, 0.7), 0.45)
        self.burst((c.x, c.center_y(), c.z), "venom", 12, 4.0, 0.6, 0.16, gravity=3.0)

    def spawn_puff(self, x: float, y: float, z: float) -> None:
        self.burst((x, y + 1.0, z), "rift", 6, 2.6, 0.5, 0.16, gravity=-1.0)

    # ------------------------------------------------------------------ towers
    def tower_built(self, t) -> None:
        self.burst((t.x, t.y + 0.3, t.z), "dust", 16, 3.2, 0.7, 0.3, gravity=1.0, up=0.5)
        self.ring(t.x, t.y, t.z, 1.6, (0.9, 0.82, 0.6, 0.7), 0.4)

    def upgrade_done(self, t) -> None:
        self.burst((t.x, t.y + 1.5, t.z), "gold", 22, 3.6, 0.9, 0.16, gravity=-2.0, jitter=0.5)
        self.ring(t.x, t.y, t.z, 2.2, (1.0, 0.85, 0.35, 0.9), 0.55)

    def tower_sold(self, x: float, y: float, z: float, gold: int) -> None:
        self.burst((x, y + 0.6, z), "dust", 18, 3.5, 0.8, 0.34, gravity=1.0)
        self.burst((x, y + 1.0, z), "gold", 10, 3.0, 0.7, 0.13)
        self.float_text(x, y + 2.0, z, f"+{gold}", (1.0, 0.85, 0.3), 1.0, 1.0)

    def overcharge(self, x: float, z: float, radius: float, towers: list) -> None:
        y = self._ground(x, z)
        self.ring(x, y, z, radius, (1.0, 0.9, 0.35, 0.95), 0.7)
        for t in towers:
            self.burst((t.x, t.y + 2.0, t.z), "sun", 10, 3.0, 0.8, 0.15, gravity=-2.5, jitter=0.4)

    def nova(self, x: float, z: float, radius: float) -> None:
        y = self._ground(x, z)
        self.ring(x, y, z, radius, (0.65, 0.9, 1.0, 1.0), 0.6)
        self.ring(x, y, z, radius, (0.6, 0.85, 1.0, 0.35), 1.6, grow=False, filled=True)
        for _ in range(10):
            a, r = random.uniform(0, math.tau), random.uniform(0, radius)
            px, pz = x + math.cos(a) * r, z + math.sin(a) * r
            self.burst((px, self._ground(px, pz) + 0.4, pz), "frost", 6, 3.0, 0.9, 0.18, gravity=1.0)

    def meteor_called(self, x: float, z: float, radius: float, delay: float) -> None:
        y = self._ground(x, z)
        self.ring(x, y, z, radius, (1.0, 0.4, 0.15, 0.9), delay, grow=False)
        node = _proto("meteor").copyTo(self.root)
        node.setShaderInput("u_emissive", EMISSIVE["meteor"])
        start = Vec3(x - 7.0, y + 26.0, z + 4.0)
        node.setPos(start)
        self.falling.append({"node": node, "start": start, "end": Vec3(x, y, z), "t": 0.0, "life": delay})

    def meteor_impact(self, x: float, z: float, radius: float) -> None:
        y = self._ground(x, z)
        self.burst((x, y + 0.6, z), "fire", 46, 9.0, 0.8, 0.42, gravity=5.0)
        self.burst((x, y + 0.6, z), "ember", 26, 6.0, 1.1, 0.3, gravity=3.0)
        self.burst((x, y + 0.8, z), "smoke", 22, 3.2, 2.2, 0.8, gravity=-1.4)
        self.burst((x, y + 0.3, z), "debris", 16, 8.0, 0.9, 0.14)
        self.ring(x, y, z, radius * 1.25, (1.0, 0.75, 0.4, 1.0), 0.5)
        node = self._flat_quad(self.scorch_tex, 3)
        node.setPos(x, y + 0.07, z)
        r = Ring(node, radius * 0.95, (1, 1, 1, 0.7), 9.0, grow=False)
        r.update(0.0)
        self.rings.append(r)
        self.match.cam.shake(0.8, 0.45)

    # ------------------------------------------------------------------ per frame
    def update(self, dt: float, cam_right: np.ndarray, cam_up: np.ndarray, cam_pos: np.ndarray) -> None:
        self._t += dt
        m = self.match
        for p in m.combat.projectiles:
            node = p.node
            if node is None:
                continue
            x, y, z, f = p.position()
            prev = node.getPos()
            node.setPos(x, y, z)
            d = Vec3(x, y, z) - prev
            if d.lengthSquared() > 1e-6:
                node.lookAt(Vec3(x, y, z) + d, Vec3(0, 1, 0))
            if p.kind == "shell" and random.random() < 0.5:
                self.particles.emit((x, y, z), PARTICLE_COLORS["smoke"], 1, 0.4, 0.5, 0.16, -0.6, alpha=0.6)
            elif p.kind == "frost" and random.random() < 0.4:
                self.particles.emit((x, y, z), PARTICLE_COLORS["frost"], 1, 0.6, 0.35, 0.1, 0.5)
            elif p.kind == "venom" and random.random() < 0.3:
                self.particles.emit((x, y, z), PARTICLE_COLORS["venom"], 1, 0.5, 0.35, 0.09, 3.0)
        if self.falling:
            keep_f = []
            for f in self.falling:
                f["t"] += dt
                k = min(1.0, f["t"] / f["life"])
                pos = f["start"] + (f["end"] - f["start"]) * (k * k)
                f["node"].setPos(pos)
                self.particles.emit((pos.x, pos.y, pos.z), PARTICLE_COLORS["fire"], 3, 1.5, 0.5, 0.4, -1.0, jitter=0.3)
                if k >= 1.0:
                    f["node"].removeNode()
                else:
                    keep_f.append(f)
            self.falling = keep_f
        if self.rings:
            keep_r = []
            for r in self.rings:
                if r.update(dt):
                    keep_r.append(r)
                else:
                    r.node.removeNode()
            self.rings = keep_r
        if self.bolts:
            keep_b = []
            for bolt in self.bolts:
                segs, age, life, col, width = bolt
                bolt[1] = age = age + dt
                if age >= life:
                    continue
                a = 1.0 - age / life
                for s0, s1 in segs:
                    self.beams.add(s0, s1, width * 1.9, col, 0.55 * a)
                    self.beams.add(s0, s1, width * 0.7, (1.0, 1.0, 1.0), a)
                keep_b.append(bolt)
            self.bolts = keep_b
        self.particles.update(dt, cam_right, cam_up)
        self.beams.flush(cam_pos)

    def update_text(self, real_dt: float) -> None:
        self.text.update(real_dt)

    def clear(self) -> None:
        for r in self.rings:
            r.node.removeNode()
        for f in self.falling:
            f["node"].removeNode()
        self.rings.clear()
        self.falling.clear()
        self.bolts.clear()
        self.particles.clear()
        self.text.destroy()
