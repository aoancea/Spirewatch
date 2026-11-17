"""The build / pathing grid: cell types from the map layout, tower occupancy and the flow field creeps follow.

Cell (col, row) has index ``row * w + col``. Row 0 is the far (north, +z) edge of the map.
The flow field is a Dijkstra distance-to-exit over walkable cells; every creep simply walks "downhill".
"""
from __future__ import annotations

import heapq
import math
from typing import Any, Iterable

from settings import CELL

WALK_CHARS = "=oxSE"
BUILD_CHARS = ".o"
INF = float("inf")
DIAG = math.sqrt(2.0)


class Grid:
    """Static cell data plus the dynamic tower layer and the current flow field."""

    def __init__(self, layout: list[str]) -> None:
        self.h = len(layout)
        self.w = max(len(r) for r in layout)
        rows = [r.ljust(self.w, "#") for r in layout]
        self.kind: list[str] = [ch for r in rows for ch in r]
        n = self.w * self.h
        self.n = n
        self.base_walk = [ch in WALK_CHARS for ch in self.kind]
        self.buildable = [ch in BUILD_CHARS for ch in self.kind]
        self.tower: list[Any] = [None] * n
        self.exits: list[int] = [i for i, ch in enumerate(self.kind) if ch == "E"]
        self.gates: list[list[int]] = self._group([i for i, ch in enumerate(self.kind) if ch == "S"])
        self.is_maze = any(ch == "o" for ch in self.kind)
        self.half_w = self.w * CELL * 0.5
        self.half_h = self.h * CELL * 0.5
        self.version = 0
        self.dist: list[float] = [INF] * n
        self.next: list[int] = list(range(n))
        self.recompute()

    def _group(self, cells: list[int]) -> list[list[int]]:
        """Group adjacent spawn cells into gates."""
        left = set(cells)
        gates: list[list[int]] = []
        while left:
            start = min(left)
            left.discard(start)
            gate, stack = [start], [start]
            while stack:
                c = stack.pop()
                col, row = c % self.w, c // self.w
                for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nc, nr = col + dc, row + dr
                    k = nr * self.w + nc
                    if 0 <= nc < self.w and 0 <= nr < self.h and k in left:
                        left.discard(k)
                        gate.append(k)
                        stack.append(k)
            gates.append(sorted(gate))
        return gates

    # ------------------------------------------------------------------ coordinates
    def center(self, c: int) -> tuple[float, float]:
        col, row = c % self.w, c // self.w
        return (col + 0.5) * CELL - self.half_w, self.half_h - (row + 0.5) * CELL

    def cell_xy(self, x: float, z: float) -> tuple[int, int]:
        return int(math.floor((x + self.half_w) / CELL)), int(math.floor((self.half_h - z) / CELL))

    def cell_of(self, x: float, z: float) -> int:
        """Cell index under a world point, or -1 outside the grid."""
        col, row = self.cell_xy(x, z)
        if 0 <= col < self.w and 0 <= row < self.h:
            return row * self.w + col
        return -1

    def index(self, col: int, row: int) -> int:
        if 0 <= col < self.w and 0 <= row < self.h:
            return row * self.w + col
        return -1

    def in_grid(self, x: float, z: float) -> bool:
        return self.cell_of(x, z) >= 0

    # ------------------------------------------------------------------ queries
    def walkable(self, c: int) -> bool:
        return c >= 0 and self.base_walk[c] and self.tower[c] is None

    def walkable_at(self, x: float, z: float) -> bool:
        c = self.cell_of(x, z)
        return c >= 0 and self.base_walk[c] and self.tower[c] is None

    def can_build(self, c: int) -> bool:
        return c >= 0 and self.buildable[c] and self.tower[c] is None

    def gate_center(self, gate: int) -> tuple[float, float]:
        cells = self.gates[gate % len(self.gates)]
        xs = [self.center(c) for c in cells]
        return sum(p[0] for p in xs) / len(xs), sum(p[1] for p in xs) / len(xs)

    def exit_center(self) -> tuple[float, float]:
        xs = [self.center(c) for c in self.exits]
        return sum(p[0] for p in xs) / len(xs), sum(p[1] for p in xs) / len(xs)

    def nearest_exit(self, x: float, z: float) -> tuple[float, float]:
        best, bd = self.center(self.exits[0]), INF
        for c in self.exits:
            ex, ez = self.center(c)
            d = (ex - x) ** 2 + (ez - z) ** 2
            if d < bd:
                best, bd = (ex, ez), d
        return best

    # ------------------------------------------------------------------ flow field
    def compute(self, blocked: Iterable[int] = ()) -> tuple[list[float], list[int]]:
        """Distance (in cells) to the nearest exit and the next cell towards it, with ``blocked`` treated as walls."""
        w, h, n = self.w, self.h, self.n
        walk = [self.base_walk[i] and self.tower[i] is None for i in range(n)]
        for b in blocked:
            walk[b] = False
        dist = [INF] * n
        nxt = list(range(n))
        heap: list[tuple[float, int]] = []
        for e in self.exits:
            if walk[e]:
                dist[e] = 0.0
                heap.append((0.0, e))
        heapq.heapify(heap)
        while heap:
            d, c = heapq.heappop(heap)
            if d > dist[c]:
                continue
            col, row = c % w, c // w
            left, right, up, down = col > 0, col < w - 1, row > 0, row < h - 1
            wl = left and walk[c - 1]
            wr = right and walk[c + 1]
            wu = up and walk[c - w]
            wd = down and walk[c + w]
            for ok, k, cost in ((wl, c - 1, 1.0), (wr, c + 1, 1.0), (wu, c - w, 1.0), (wd, c + w, 1.0),
                                (wl and wu and walk[c - w - 1], c - w - 1, DIAG), (wr and wu and walk[c - w + 1], c - w + 1, DIAG),
                                (wl and wd and walk[c + w - 1], c + w - 1, DIAG), (wr and wd and walk[c + w + 1], c + w + 1, DIAG)):
                if ok:
                    nd = d + cost
                    if nd < dist[k] - 1e-9:
                        dist[k] = nd
                        nxt[k] = c
                        heapq.heappush(heap, (nd, k))
        return dist, nxt

    def recompute(self) -> None:
        self.dist, self.next = self.compute()
        self.version += 1

    def try_block(self, cells: Iterable[int], occupied: Iterable[int] = ()) -> tuple[bool, list[float], list[int]]:
        """Flow field with ``cells`` walled off, and whether that would cut a rift gate (or any cell in
        ``occupied``) off from the exits."""
        cells = [c for c in cells if self.base_walk[c]]
        if not cells:
            return False, self.dist, self.next
        dist, nxt = self.compute(cells)
        for gate in self.gates:
            if any(dist[c] == INF for c in gate):
                return True, dist, nxt
        blocked = set(cells)
        for c in occupied:
            if c >= 0 and c not in blocked and dist[c] == INF and self.dist[c] != INF:
                return True, dist, nxt
        return False, dist, nxt

    def blocks_path(self, cells: Iterable[int], occupied: Iterable[int] = ()) -> bool:
        return self.try_block(cells, occupied)[0]

    def place(self, c: int, tower: Any) -> None:
        self.tower[c] = tower
        if self.base_walk[c]:
            self.recompute()

    def remove(self, c: int) -> None:
        self.tower[c] = None
        if self.base_walk[c]:
            self.recompute()

    # ------------------------------------------------------------------ paths
    def path_cells(self, start: int, dist: list[float] | None = None, nxt: list[int] | None = None) -> list[int]:
        """Cells from ``start`` down the flow field to an exit (empty if unreachable)."""
        dist = self.dist if dist is None else dist
        nxt = self.next if nxt is None else nxt
        if start < 0 or dist[start] == INF:
            return []
        out = [start]
        c = start
        for _ in range(self.n):
            k = nxt[c]
            if k == c:
                break
            out.append(k)
            c = k
        return out

    def route(self, gate: int, dist: list[float] | None = None, nxt: list[int] | None = None) -> list[tuple[float, float]]:
        """World-space polyline of the ground route from a gate to the exit."""
        cells = self.gates[gate % len(self.gates)]
        d = self.dist if dist is None else dist
        start = min(cells, key=lambda c: d[c])
        return [self.center(c) for c in self.path_cells(start, dist, nxt)]

    def route_length(self, gate: int = 0) -> float:
        cells = self.gates[gate % len(self.gates)]
        d = min(self.dist[c] for c in cells)
        return d * CELL if d != INF else 0.0

    def all_route_cells(self) -> list[int]:
        """Every cell on the current ground routes (deduplicated, ordered from the gates)."""
        seen: dict[int, None] = {}
        for gate in self.gates:
            start = min(gate, key=lambda c: self.dist[c])
            for c in self.path_cells(start):
                seen.setdefault(c, None)
        return list(seen)

    def line_clear(self, x0: float, z0: float, x1: float, z1: float, margin: float = 0.0) -> bool:
        """True if the straight segment (optionally fattened by ``margin``) crosses only walkable cells."""
        dx, dz = x1 - x0, z1 - z0
        length = math.hypot(dx, dz)
        if length < 1e-6:
            return self.walkable_at(x0, z0)
        steps = int(length / (CELL * 0.25)) + 1
        px, pz = -dz / length * margin, dx / length * margin
        for i in range(steps + 1):
            t = i / steps
            x, z = x0 + dx * t, z0 + dz * t
            if not self.walkable_at(x, z):
                return False
            if margin and (not self.walkable_at(x + px, z + pz) or not self.walkable_at(x - px, z - pz)):
                return False
        return True
