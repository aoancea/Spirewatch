"""A playable map: the grid, the terrain sculpted around it and all of its scenery.

The terrain is a heightfield with SUB quads per grid cell that also covers a wide scenery border.
Cells creeps walk on or towers stand on are kept flat right up to their edges; roads sit a little
lower than the building terraces, rocks and water rise and fall inside their own cells.
"""
from __future__ import annotations

import math
import random
from typing import Any

import numpy as np

from core import data
from core.noise import fbm, ridged, smoothstep, value_noise
from settings import BORDER, CELL, PLAYER_COLOR, ROAD_DEPTH, SEA_LEVEL, SUB
from world.grid import Grid

OUT, ROAD, TERRACE, OPEN, PAD, ROCK, TREES, WATER = range(8)
_CLASS = {" ": OUT, "=": ROAD, "S": ROAD, "E": ROAD, "r": ROAD, ".": TERRACE, "o": OPEN, "x": PAD, "#": ROCK, "T": TREES,
          "~": WATER}

RGB = tuple[float, float, float]
PALETTES: dict[str, dict[str, RGB]] = {
    "vale": {
        "grass": (0.36, 0.52, 0.24), "grass2": (0.45, 0.58, 0.27), "dry": (0.55, 0.56, 0.30), "forest": (0.27, 0.40, 0.21),
        "road": (0.60, 0.47, 0.32), "road2": (0.52, 0.40, 0.28), "bank": (0.40, 0.33, 0.24), "pad": (0.62, 0.60, 0.56),
        "rock": (0.52, 0.50, 0.47), "rock2": (0.40, 0.39, 0.37), "silt": (0.42, 0.42, 0.32), "heath": (0.42, 0.46, 0.27),
        "peak": (0.60, 0.60, 0.63), "snow": (0.92, 0.93, 0.96),
    },
    "dusk": {
        "grass": (0.50, 0.48, 0.23), "grass2": (0.58, 0.50, 0.23), "dry": (0.62, 0.50, 0.26), "forest": (0.40, 0.36, 0.20),
        "road": (0.55, 0.42, 0.31), "road2": (0.47, 0.36, 0.27), "bank": (0.37, 0.29, 0.22), "pad": (0.60, 0.56, 0.52),
        "rock": (0.50, 0.45, 0.42), "rock2": (0.39, 0.35, 0.34), "silt": (0.40, 0.38, 0.30), "heath": (0.50, 0.42, 0.26),
        "peak": (0.58, 0.54, 0.54), "snow": (0.93, 0.90, 0.88),
    },
    "moor": {
        "grass": (0.30, 0.40, 0.33), "grass2": (0.36, 0.43, 0.30), "dry": (0.45, 0.35, 0.47), "forest": (0.24, 0.31, 0.28),
        "road": (0.45, 0.42, 0.44), "road2": (0.38, 0.36, 0.39), "bank": (0.28, 0.27, 0.30), "pad": (0.50, 0.49, 0.54),
        "rock": (0.38, 0.37, 0.43), "rock2": (0.29, 0.28, 0.34), "silt": (0.30, 0.33, 0.33), "heath": (0.36, 0.33, 0.40),
        "peak": (0.50, 0.50, 0.57), "snow": (0.82, 0.84, 0.92),
    },
}


class Terrain:
    """Heightfield derived from the grid layout (pure numpy, so it also works without a window)."""

    def __init__(self, grid: Grid, mapdef: dict[str, Any]) -> None:
        self.grid = grid
        self.seed = int(mapdef.get("seed", 1))
        B, S = BORDER, SUB
        self.cw, self.ch = grid.w + 2 * B, grid.h + 2 * B
        self.nx, self.nz = self.cw * S + 1, self.ch * S + 1
        self.step = CELL / S
        self.x0 = -grid.half_w - B * CELL
        self.z0 = -grid.half_h - B * CELL
        self.size_x = self.cw * CELL
        self.size_z = self.ch * CELL
        self.ground = ROAD_DEPTH if any(ch == "." for ch in grid.kind) else 0.0
        self.ext = self._extended_cells()
        self.cls = np.vectorize(_CLASS.get)(self.ext).astype(np.int8)
        xs = self.x0 + np.arange(self.nx, dtype=np.float32) * self.step
        zs = self.z0 + np.arange(self.nz, dtype=np.float32) * self.step
        self.X, self.Z = np.meshgrid(xs, zs)
        self.h = self._generate()
        self.hl: list[float] = self.h.ravel().tolist()
        self.has_water = bool((self.cls == WATER).any())

    # ------------------------------------------------------------------ layout
    def _extended_cells(self) -> np.ndarray:
        g = self.grid
        B = BORDER
        ext = np.full((self.ch, self.cw), " ", dtype="<U1")
        for row in range(g.h):
            ext[B + g.h - 1 - row, B:B + g.w] = g.kind[row * g.w:(row + 1) * g.w]
        # short approach roads lead off the map from every gate / exit on the edge
        for c in [c for gate in g.gates for c in gate] + list(g.exits):
            col, row = c % g.w, c // g.w
            ei, ej = B + col, B + g.h - 1 - row
            di = -1 if col == 0 else 1 if col == g.w - 1 else 0
            dj = 1 if row == 0 else -1 if row == g.h - 1 else 0
            if di:
                dj = 0
            for k in range(1, 4):
                if di or dj:
                    ext[ej + dj * k, ei + di * k] = "r"
        return ext

    def ext_index(self, x: float, z: float) -> tuple[int, int]:
        ei = min(max(int((x - self.x0) / CELL), 0), self.cw - 1)
        ej = min(max(int((z - self.z0) / CELL), 0), self.ch - 1)
        return ei, ej

    def class_at(self, x: float, z: float) -> int:
        ei, ej = self.ext_index(x, z)
        return int(self.cls[ej, ei])

    # ------------------------------------------------------------------ heights
    def _generate(self) -> np.ndarray:
        g = self.grid
        S = SUB
        cls = self.cls
        seed = self.seed
        cj, ci = np.mgrid[0:self.ch, 0:self.cw].astype(np.float32)
        rnd = value_noise(ci * 0.71 + 3.3, cj * 0.71 + 1.7, seed + 40)
        ch = np.full(cls.shape, self.ground, np.float32)
        ch[cls == ROAD] = 0.0
        ch[cls == OPEN] = 0.0
        ch[cls == PAD] = 0.0
        ch[cls == TERRACE] = ROAD_DEPTH
        ch[cls == ROCK] = (self.ground + 0.55 + rnd * 1.15)[cls == ROCK]
        ch[cls == WATER] = SEA_LEVEL - 0.85
        play = (cls == ROAD) | (cls == OPEN) | (cls == PAD) | (cls == TERRACE)
        self.cell_h = ch

        jj, ii = np.arange(self.nz), np.arange(self.nx)
        js = (np.clip((jj - 1) // S, 0, self.ch - 1), np.clip(jj // S, 0, self.ch - 1))
        is_ = (np.clip((ii - 1) // S, 0, self.cw - 1), np.clip(ii // S, 0, self.cw - 1))
        hs = np.stack([ch[np.ix_(ja, ia)] for ja in js for ia in is_])
        ps = np.stack([play[np.ix_(ja, ia)] for ja in js for ia in is_])
        cs = np.stack([cls[np.ix_(ja, ia)] for ja in js for ia in is_])
        any_play = ps.any(axis=0)
        play_max = np.where(ps, hs, -1e9).max(axis=0)
        h = np.where(any_play, play_max, hs.mean(axis=0)).astype(np.float32)

        X, Z = self.X, self.Z
        all_out = (cs == OUT).all(axis=0)
        rocky = (cs == ROCK).any(axis=0) & ~any_play
        wet = (cs == WATER).all(axis=0)
        woody = (cs == TREES).any(axis=0) & ~any_play & ~rocky
        h += np.where(any_play, (fbm(X, Z, 2.5, 2, seed + 1) - 0.5) * 0.05, 0.0)
        h += np.where(rocky, (value_noise(X * 0.85, Z * 0.85, seed + 2) - 0.45) * 1.2, 0.0)
        h += np.where(woody, (fbm(X, Z, 3.0, 2, seed + 3) - 0.5) * 0.22, 0.0)
        if wet.any():
            # relax the basin towards its fixed rim: banks slope gently and the shoreline rounds off
            h = np.where(wet, SEA_LEVEL - 1.05, h)
            for _ in range(14):
                avg = (np.roll(h, 1, 0) + np.roll(h, -1, 0) + np.roll(h, 1, 1) + np.roll(h, -1, 1)) * 0.25
                h = np.where(wet, 0.45 * h + 0.55 * avg, h)
            h += np.where(wet, (fbm(X, Z, 2.0, 2, seed + 4) - 0.5) * 0.16, 0.0)

        # scenery beyond the grid: rolling ground, then a rim of mountains; flat near gates and the shrine
        ex = np.maximum(np.abs(X) - g.half_w, 0.0)
        ez = np.maximum(np.abs(Z) - g.half_h, 0.0)
        e = np.sqrt(ex * ex + ez * ez)
        near = np.full(X.shape, 1e9, np.float32)
        for px, pz in [g.gate_center(i) for i in range(len(g.gates))] + [g.exit_center()]:
            near = np.minimum(near, np.sqrt((X - px) ** 2 + (Z - pz) ** 2))
        calm = smoothstep(5.0, 13.0, near)
        hills = (fbm(X, Z, 13.0, 3, seed + 5) - 0.36) * 2.8 * smoothstep(1.0, 9.0, e)
        rim = smoothstep(11.0, BORDER * CELL - 3.0, e) ** 1.25 * (4.0 + ridged(X, Z, 10.0, 3, seed + 6) * 11.0)
        out = np.maximum(hills * calm + rim * (0.35 + 0.65 * calm), -0.15)
        h += np.where(all_out, out, 0.0)
        self.edge_dist = e
        return h.astype(np.float32)

    def height(self, x: float, z: float) -> float:
        """Exact height of the rendered triangle surface at (x, z)."""
        nx = self.nx
        fx = (x - self.x0) / self.step
        fz = (z - self.z0) / self.step
        if fx < 0.0:
            fx = 0.0
        elif fx > nx - 1.001:
            fx = nx - 1.001
        if fz < 0.0:
            fz = 0.0
        elif fz > self.nz - 1.001:
            fz = self.nz - 1.001
        ix = int(fx)
        iz = int(fz)
        tx = fx - ix
        tz = fz - iz
        i = iz * nx + ix
        hl = self.hl
        a = hl[i]
        c = hl[i + nx + 1]
        if tx > tz:
            b = hl[i + 1]
            return a + (b - a) * tx + (c - b) * tz
        d = hl[i + nx]
        return a + (d - a) * tz + (c - d) * tx

    # ------------------------------------------------------------------ mesh
    def build_mesh(self, parent, palette_name: str, chunk: int = 48):
        """Create flat-shaded, vertex-coloured terrain chunks under ``parent``."""
        from core.meshgen import grid_indices, make_geom_node, tri_normals
        pal = PALETTES.get(palette_name, PALETTES["vale"])
        root = parent.attachNewNode("terrain")
        verts = np.stack([self.X, self.h, self.Z], -1)
        cells_x, cells_z = self.nx - 1, self.nz - 1
        for cz in range(0, cells_z, chunk):
            for cx in range(0, cells_x, chunk):
                nx = min(chunk, cells_x - cx)
                nz = min(chunk, cells_z - cz)
                sub = verts[cz:cz + nz + 1, cx:cx + nx + 1].reshape(-1, 3)
                tris = sub[grid_indices(nx, nz)]
                cols = self._face_colors(tris, pal)
                normals = np.repeat(tri_normals(tris), 3, axis=0)
                node = make_geom_node(tris.reshape(-1, 3), normals, np.repeat(cols, 3, axis=0), name=f"terrain_{cx}_{cz}")
                node.reparentTo(root)
        root.setShaderInput("u_detail", 0.5)
        return root

    def _face_colors(self, tris: np.ndarray, pal: dict[str, RGB]) -> np.ndarray:
        from core.meshgen import tri_normals
        cent = tris.mean(axis=1)
        cx, cy, cz = cent[:, 0], cent[:, 1], cent[:, 2]
        up = tri_normals(tris)[:, 1]
        seed = self.seed
        ei = np.clip(((cx - self.x0) / CELL).astype(int), 0, self.cw - 1)
        ej = np.clip(((cz - self.z0) / CELL).astype(int), 0, self.ch - 1)
        cls = self.cls[ej, ei]
        n1 = fbm(cx, cz, 7.0, 3, seed + 201)[:, None]
        n2 = fbm(cx, cz, 19.0, 3, seed + 202)[:, None]
        speck = value_noise(cx * 2.3, cz * 2.3, seed + 203)[:, None]

        def col(name: str) -> np.ndarray:
            return np.asarray(pal[name], np.float32)[None, :]

        def mix(a: np.ndarray, b: np.ndarray, t: np.ndarray) -> np.ndarray:
            return a * (1 - t) + b * t

        grass = mix(col("grass"), col("grass2"), n2)
        grass = mix(grass, col("dry"), smoothstep(0.60, 0.80, n1))
        steep = smoothstep(0.90, 0.70, up)[:, None]
        rock = mix(col("rock"), col("rock2"), speck)
        c = grass.copy()
        # buildable ground: a faint chequer shows the grid
        buildable = ((cls == TERRACE) | (cls == OPEN))[:, None]
        parity = (((ei + ej) % 2) * 2 - 1)[:, None].astype(np.float32)
        c = np.where(buildable, grass * (1.0 + 0.036 * parity), c)
        # roads: packed dirt, darker on the banks
        road = mix(col("road"), col("road2"), smoothstep(0.35, 0.7, n1))
        road = mix(road, col("bank"), smoothstep(0.985, 0.90, up)[:, None])
        c = np.where((cls == ROAD)[:, None], road, c)
        pad = col("pad") * (1.0 + 0.06 * parity) * (0.94 + 0.1 * speck)
        c = np.where((cls == PAD)[:, None], pad, c)
        c = np.where((cls == TREES)[:, None], mix(col("forest"), grass, 0.35 * n1), c)
        mossy = mix(rock, grass, 0.4 * smoothstep(0.93, 0.99, up)[:, None])
        c = np.where((cls == ROCK)[:, None], mix(mossy, rock, steep), c)
        bed = mix(col("bank"), col("silt"), smoothstep(SEA_LEVEL + 0.2, SEA_LEVEL - 0.3, cy)[:, None])
        c = np.where((cls == WATER)[:, None], bed, c)
        # scenery: grass -> heath -> bare rock -> snow
        rel = (cy - self.ground)[:, None]
        wild = mix(grass, col("heath"), smoothstep(1.5, 4.0, rel) * 0.8)
        wild = mix(wild, rock, smoothstep(0.88, 0.62, up)[:, None])
        wild = mix(wild, col("peak"), smoothstep(6.0, 10.0, rel))
        wild = mix(wild, col("snow"), smoothstep(10.5, 13.5, rel) * smoothstep(0.5, 0.8, up)[:, None])
        c = np.where((cls == OUT)[:, None], wild, c)
        c = c * (0.94 + 0.12 * speck)
        out = np.ones((c.shape[0], 4), np.float32)
        out[:, :3] = np.clip(c, 0, 1)
        return out


class Level:
    """Grid + terrain + scenery of one match."""

    def __init__(self, map_id: str) -> None:
        self.id = map_id
        self.defn: dict[str, Any] = data.maps()[map_id]
        self.name: str = self.defn["name"]
        self.palette: str = self.defn.get("palette", "vale")
        self.grid = Grid(self.defn["layout"])
        self.terrain = Terrain(self.grid, self.defn)
        self.hp_mult = float(self.defn.get("hp_mult", 1.0))
        g = self.grid
        self.cell_y = [self._cell_height(c) for c in range(g.n)]
        self.exit_pos = g.exit_center()
        # where each gate's creeps appear, which way they face, and where the shrine stands
        self.gate_info: list[dict[str, Any]] = []
        for i, cells in enumerate(g.gates):
            gx, gz = g.gate_center(i)
            dx, dz = self._outward(cells[0])
            self.gate_info.append({"pos": (gx, gz), "dir": (-dx, -dz), "width": 1.0 + 0.5 * (len(cells) - 1)})
        ex, ez = self.exit_pos
        dx, dz = self._outward(g.exits[0])
        self.shrine_pos = (ex + dx * 2.6, ez + dz * 2.6)
        self.shrine_dir = (dx, dz)
        self.root = None
        self.gates_fx: list[dict[str, Any]] = []
        self.flame = None
        self.flame_light = None
        self.flame_pos = (self.shrine_pos[0], 3.0, self.shrine_pos[1])
        self._flame_k = 1.0
        self._t = 0.0

    def _cell_height(self, c: int) -> float:
        ch = self.grid.kind[c]
        return ROAD_DEPTH if ch == "." else 0.0

    def _outward(self, c: int) -> tuple[float, float]:
        """Unit vector pointing off the map from an edge cell (world x, z)."""
        g = self.grid
        col, row = c % g.w, c // g.w
        if col == 0:
            return -1.0, 0.0
        if col == g.w - 1:
            return 1.0, 0.0
        if row == 0:
            return 0.0, 1.0
        if row == g.h - 1:
            return 0.0, -1.0
        return 1.0, 0.0

    def height(self, x: float, z: float) -> float:
        return self.terrain.height(x, z)

    # ================================================================== visuals
    def build(self, parent, env=None) -> None:
        """Create every scene node of the map under ``parent``."""
        from core.meshgen import MeshBuilder
        self.root = parent.attachNewNode("level")
        self.terrain.build_mesh(self.root, self.palette)
        if self.terrain.has_water:
            self._build_water()
        self._build_skirt()
        self._build_scenery()
        self._build_landmarks(env)

    def _build_water(self) -> None:
        from panda3d.core import TransparencyAttrib
        from core import shaders
        from core.meshgen import grid_indices, make_geom_node
        from core.textures import np_to_texture
        t = self.terrain
        depth = np.clip((SEA_LEVEL - t.h) / 4.0, 0, 1)
        # only real water cells may show water (dips in the scenery stay dry)
        keep = np.zeros(t.h.shape, bool)
        ys, xs = np.nonzero(t.cls == WATER)
        for ej, ei in zip(ys, xs):
            keep[ej * SUB:(ej + 1) * SUB + 1, ei * SUB:(ei + 1) * SUB + 1] = True
        depth = np.where(keep, depth, 0.0)
        tex = np_to_texture((depth[::-1] * 255).astype(np.uint8), "depth", mipmap=False, repeat=False)
        x_lo, x_hi = t.x0 + xs.min() * CELL - 1.0, t.x0 + (xs.max() + 1) * CELL + 1.0
        z_lo, z_hi = t.z0 + ys.min() * CELL - 1.0, t.z0 + (ys.max() + 1) * CELL + 1.0
        nx = max(2, int((x_hi - x_lo) / 1.0))
        nz = max(2, int((z_hi - z_lo) / 1.0))
        gx = np.linspace(x_lo, x_hi, nx + 1, dtype=np.float32)
        gz = np.linspace(z_lo, z_hi, nz + 1, dtype=np.float32)
        X, Z = np.meshgrid(gx, gz)
        pos = np.stack([X, np.zeros_like(X), Z], -1).reshape(-1, 3)
        nrm = np.tile(np.array([0, 1, 0], np.float32), (pos.shape[0], 1))
        col = np.ones((pos.shape[0], 4), np.float32)
        node = make_geom_node(pos, nrm, col, indices=grid_indices(nx, nz), name="water")
        node.reparentTo(self.root)
        node.setPos(0, SEA_LEVEL, 0)
        node.setShader(shaders.water_shader())
        node.setShaderInput("u_depth_tex", tex)
        node.setShaderInput("u_depth_rect", (t.x0, t.z0, t.size_x, t.size_z))
        node.setTransparency(TransparencyAttrib.M_alpha)
        node.setBin("transparent", 4)
        node.setDepthWrite(False)
        self.water = node

    def _build_skirt(self) -> None:
        """Far ground beyond the heightfield so the void is never visible."""
        from core.meshgen import MeshBuilder
        t = self.terrain
        far = 900.0
        y = t.ground + 9.0
        x_lo, x_hi, z_lo, z_hi = t.x0, t.x0 + t.size_x, t.z0, t.z0 + t.size_z
        pal = PALETTES.get(self.palette, PALETTES["vale"])
        col = tuple(v * 0.8 for v in pal["rock2"]) + (1.0,)
        mb = MeshBuilder()
        for x0, z0, x1, z1 in ((-far, -far, far, z_lo), (-far, z_hi, far, far), (-far, z_lo, x_lo, z_hi), (x_hi, z_lo, far, z_hi)):
            mb.quad((x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1), col, facing=(0, 1, 0))
        mb.build("skirt").reparentTo(self.root)

    def _build_scenery(self) -> None:
        from core.meshgen import MeshBuilder
        from world.props import boulder_mesh, tree_mesh
        t, g = self.terrain, self.grid
        rng = random.Random(self.seed_base + 7)
        pal = PALETTES.get(self.palette, PALETTES["vale"])
        protos = {k: tree_mesh(self.palette, k).arrays() for k in range(6)}
        rock_col = pal["rock"] + (1.0,)
        rock_protos = [boulder_mesh(rock_col, s).arrays() for s in range(5)]
        trees: list[tuple[float, float, float, float, int]] = []     # x, z, scale, yaw, kind
        rocks: list[tuple[float, float, float, float, int]] = []
        # authored cells
        for c, ch in enumerate(g.kind):
            x, z = g.center(c)
            if ch == "T":
                for _ in range(2 if rng.random() < 0.65 else 1):
                    kind = rng.randrange(6) if rng.random() < 0.6 else rng.randrange(3)
                    trees.append((x + rng.uniform(-0.6, 0.6), z + rng.uniform(-0.6, 0.6), rng.uniform(0.72, 1.02), rng.random() * 360, kind))
            elif ch == "#" and rng.random() < 0.75:
                rocks.append((x + rng.uniform(-0.4, 0.4), z + rng.uniform(-0.4, 0.4), rng.uniform(0.7, 1.25), rng.random() * 360, rng.randrange(5)))
        # scenery border: groves where a low-frequency noise says "forest"
        spacing = 2.3
        nxs = int(t.size_x / spacing)
        nzs = int(t.size_z / spacing)
        gates = [gi["pos"] for gi in self.gate_info] + [self.exit_pos, self.shrine_pos]
        for j in range(nzs):
            for i in range(nxs):
                x = t.x0 + (i + rng.random()) * spacing
                z = t.z0 + (j + rng.random()) * spacing
                if t.class_at(x, z) != OUT:
                    continue
                if any(math.hypot(x - px, z - pz) < 5.5 for px, pz in gates):
                    continue
                y = t.height(x, z)
                rel = y - t.ground
                slope = max(abs(t.height(x + 0.6, z) - y), abs(t.height(x, z + 0.6) - y)) / 0.6
                dens = float(fbm(np.float32(x), np.float32(z), 16.0, 2, t.seed + 60))
                if rel < 6.5 and slope < 0.7 and rng.random() < (dens - 0.36) * 3.2:
                    kind = rng.randrange(3) if rel > 3.0 else (rng.randrange(6) if rng.random() < 0.6 else rng.randrange(3))
                    trees.append((x, z, rng.uniform(0.85, 1.35), rng.random() * 360, kind))
                elif rng.random() < 0.028 and slope < 1.2:
                    rocks.append((x, z, rng.uniform(0.6, 1.9), rng.random() * 360, rng.randrange(5)))
        self.tree_count = len(trees)
        # batch into a few big meshes (by region, so off-screen chunks are culled)
        buckets: dict[tuple[int, int], tuple[list, list]] = {}
        for items, table, sink in ((trees, protos, 0.1), (rocks, rock_protos, 0.25)):
            for x, z, sc, yaw, kind in items:
                tris, cols = table[kind]
                a = math.radians(yaw)
                ca, sa = math.cos(a), math.sin(a)
                rot = np.array([[ca, 0, sa], [0, 1, 0], [-sa, 0, ca]], np.float32)
                key = (int((x - t.x0) // 34), int((z - t.z0) // 34))
                b = buckets.setdefault(key, ([], []))
                b[0].append(tris @ rot.T * sc + np.array([x, t.height(x, z) - sink * sc, z], np.float32))
                b[1].append(cols)
        for key, (tl, cl) in buckets.items():
            mb = MeshBuilder()
            mb.add_tris(np.concatenate(tl), np.concatenate(cl))
            mb.build(f"scenery_{key[0]}_{key[1]}").reparentTo(self.root)

    @property
    def seed_base(self) -> int:
        return int(self.defn.get("seed", 1))

    def _build_landmarks(self, env) -> None:
        from core.util import set_yaw, yaw_to
        from world.props import heartfire_meshes, rift_gate_meshes
        from world.sky import PointLight
        t = self.terrain
        for gi in self.gate_info:
            (gx, gz), (dx, dz) = gi["pos"], gi["dir"]
            px, pz = gx - dx * 1.5, gz - dz * 1.5
            y = t.height(gx, gz)
            node = self.root.attachNewNode("rift_gate")
            node.setPos(px, y - 0.05, pz)
            # gates on the east / west edge are turned a little towards the camera so the portal is not seen edge-on
            set_yaw(node, yaw_to(dx, dz) + (35.0 if dx > 0.5 else -35.0 if dx < -0.5 else 0.0))
            solid, glow, shards = rift_gate_meshes(gi["width"])
            solid.build("gate").reparentTo(node)
            gnode = glow.build("portal")
            gnode.reparentTo(node)
            gnode.setShaderInput("u_emissive", 1.45)
            snode = shards.build("shards")
            snode.reparentTo(node)
            snode.setPos(0, 1.95, 0.05)
            snode.setShaderInput("u_emissive", 1.9)
            light = None
            if env is not None:
                light = env.add_light(PointLight((px, y + 2.0, pz), 11.0, (0.75, 0.35, 1.0), 0.32, flicker=0.25, night_boost=1.6))
            self.gates_fx.append({"shards": snode, "glow": gnode, "light": light, "pos": (px, y + 1.9, pz)})
        sx, sz = self.shrine_pos
        y = t.height(*self.exit_pos)
        shrine = self.root.attachNewNode("heartfire")
        shrine.setPos(sx, y - 0.05, sz)
        solid, flame = heartfire_meshes(PLAYER_COLOR + (1.0,))
        solid.build("shrine").reparentTo(shrine)
        self.flame = flame.build("flame")
        self.flame.reparentTo(shrine)
        self.flame.setPos(0, 1.9, 0)
        self.flame.setShaderInput("u_emissive", 1.3)
        self.flame.setShaderInput("u_flame", 1.0)
        self.flame_pos = (sx, y + 2.6, sz)
        if env is not None:
            self.flame_light = env.add_light(PointLight((sx, y + 3.2, sz), 15.0, (1.0, 0.62, 0.25), 0.3, flicker=0.2, night_boost=2.0))

    def update(self, dt: float, lives_frac: float = 1.0) -> None:
        """Animate the portals and the Heartfire (its flame shrinks as lives are lost)."""
        if self.root is None:
            return
        self._t += dt
        t = self._t
        for i, gfx in enumerate(self.gates_fx):
            gfx["shards"].setR(t * 70.0 + i * 40.0)
            k = 1.0 + 0.05 * math.sin(t * 3.1 + i)
            gfx["glow"].setScale(k, 1.0 + 0.03 * math.sin(t * 2.3 + i), 1.0)
        if self.flame is not None:
            target = 0.35 + 0.65 * max(0.0, min(1.0, lives_frac))
            cur = self._flame_k + (target - self._flame_k) * min(1.0, dt * 3.0)
            self._flame_k = cur
            self.flame.setScale(0.6 + 0.4 * cur, cur, 0.6 + 0.4 * cur)
            if self.flame_light is not None:
                self.flame_light.intensity = 0.1 + 0.22 * cur

    def destroy(self) -> None:
        if self.root is not None:
            self.root.removeNode()
            self.root = None
