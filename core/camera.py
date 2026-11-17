"""Top-down camera: pans with arrows / WASD-free screen edges / middle-mouse drag, zooms with the wheel."""
from __future__ import annotations

import math

import numpy as np
from panda3d.core import Point2, Point3, Vec3
from ursina import camera, held_keys, mouse, window

from core.util import clamp, damp

MIN_DIST = 16.0
MAX_DIST = 78.0
GROUND_Y = 0.2


class TDCamera:
    """Owns the Ursina camera while a match is running."""

    def __init__(self, base, level) -> None:
        self.base = base
        self.level = level
        g = level.grid
        self.lim_x = g.half_w - 4.0
        self.lim_z0 = -g.half_h - 9.0          # allow scrolling down so the bottom rows clear the HUD
        self.lim_z1 = g.half_h - 4.0
        self.fx = 0.0
        self.fz = -6.0
        self.fy = GROUND_Y
        self.distance = 66.0
        self.target_distance = 66.0
        self.yaw = 0.0
        self.pitch_override: float | None = None
        self.edge_scroll = True
        self.scroll_speed = 1.0
        self._drag_anchor: tuple[float, float] | None = None
        self.shake_t = 0.0
        self.shake_amp = 0.0
        self.locked = False
        camera.fov = 52
        camera.clip_plane_near = 0.5
        camera.clip_plane_far = 700
        self.view = np.eye(4, dtype=np.float32)
        self.apply()

    # ------------------------------------------------------------------ control
    def look_at(self, x: float, z: float) -> None:
        self.fx = clamp(x, -self.lim_x, self.lim_x)
        self.fz = clamp(z, self.lim_z0, self.lim_z1)
        self.apply()

    def zoom(self, steps: float) -> None:
        self.target_distance = clamp(self.target_distance - steps * 5.0, MIN_DIST, MAX_DIST)

    def shake(self, amount: float, duration: float = 0.3) -> None:
        self.shake_amp = max(self.shake_amp if self.shake_t > 0 else 0.0, amount)
        self.shake_t = max(self.shake_t, duration)

    @property
    def pitch(self) -> float:
        if self.pitch_override is not None:
            return self.pitch_override
        t = (self.distance - MIN_DIST) / (MAX_DIST - MIN_DIST)
        return 50.0 + 12.0 * t

    def begin_drag(self) -> None:
        p = self.ground_under_mouse()
        if p is not None:
            self._drag_anchor = p

    def end_drag(self) -> None:
        self._drag_anchor = None

    def update(self, dt: float, allow_input: bool = True) -> None:
        if allow_input and not self.locked:
            speed = (26.0 + self.distance * 0.8) * self.scroll_speed
            dx = held_keys["right arrow"] - held_keys["left arrow"]
            dz = held_keys["up arrow"] - held_keys["down arrow"]
            if self.edge_scroll and self._pointer_in_window():
                mx, my = mouse.x, mouse.y
                ax = window.aspect_ratio * 0.5
                if mx <= -ax + 0.004:
                    dx -= 1
                elif mx >= ax - 0.004:
                    dx += 1
                if my >= 0.496:
                    dz += 1
                elif my <= -0.496:
                    dz -= 1
            if dx or dz:
                ln = math.hypot(dx, dz)
                self.fx += dx / ln * speed * dt
                self.fz += dz / ln * speed * dt
            if self._drag_anchor is not None:
                p = self.ground_under_mouse()
                if p is not None:
                    self.fx += self._drag_anchor[0] - p[0]
                    self.fz += self._drag_anchor[1] - p[1]
        self.fx = clamp(self.fx, -self.lim_x, self.lim_x)
        self.fz = clamp(self.fz, self.lim_z0, self.lim_z1)
        self.distance = damp(self.distance, self.target_distance, 10.0, dt)
        if self.shake_t > 0:
            self.shake_t -= dt
        self.apply()

    def _pointer_in_window(self) -> bool:
        win = self.base.win
        if win is None or not win.getProperties().getForeground():
            return False
        return bool(win.getPointer(0).getInWindow())

    def apply(self) -> None:
        p = math.radians(self.pitch)
        y = math.radians(self.yaw)
        d = self.distance
        fx, fz = self.fx, self.fz
        if self.shake_t > 0:
            a = self.shake_amp * min(self.shake_t / 0.3, 1.0)
            fx += (np.random.random() - 0.5) * a
            fz += (np.random.random() - 0.5) * a
        cx = fx - math.sin(y) * math.cos(p) * d
        cz = fz - math.cos(y) * math.cos(p) * d
        cy = self.fy + math.sin(p) * d
        camera.setPos(cx, cy, cz)
        camera.lookAt(Point3(fx, self.fy, fz), Vec3(0, 1, 0))
        self._update_matrix()

    # ------------------------------------------------------------------ projection
    def _update_matrix(self) -> None:
        cam = self.base.cam
        view = cam.getMat(self.base.render)
        view.invertInPlace()
        m = view * self.base.camLens.getProjectionMat()
        self.view = np.array([[m.getCell(i, j) for j in range(4)] for i in range(4)], np.float32)

    def project(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """World points (N, 3) -> UI coordinates (N, 2) and an 'in front of camera' mask."""
        n = pts.shape[0]
        hom = np.ones((n, 4), np.float32)
        hom[:, :3] = pts
        clip = hom @ self.view
        w = clip[:, 3]
        front = w > 0.05
        w = np.where(front, w, 1.0)
        ndc = clip[:, :2] / w[:, None]
        ui = np.empty((n, 2), np.float32)
        ui[:, 0] = ndc[:, 0] * 0.5 * window.aspect_ratio
        ui[:, 1] = ndc[:, 1] * 0.5
        return ui, front

    def project_one(self, x: float, y: float, z: float) -> tuple[float, float] | None:
        ui, front = self.project(np.array([[x, y, z]], np.float32))
        if not front[0]:
            return None
        return float(ui[0, 0]), float(ui[0, 1])

    def mouse_ray(self, mx: float | None = None, my: float | None = None) -> tuple[Point3, Vec3]:
        if mx is None:
            mx, my = mouse.x, mouse.y
        ndc = Point2(mx / (0.5 * window.aspect_ratio), my / 0.5)
        near, far = Point3(), Point3()
        self.base.camLens.extrude(ndc, near, far)
        render = self.base.render
        a = render.getRelativePoint(self.base.cam, near)
        b = render.getRelativePoint(self.base.cam, far)
        d = b - a
        d.normalize()
        return a, d

    def ground_under_mouse(self, mx: float | None = None, my: float | None = None,
                           plane_y: float = GROUND_Y) -> tuple[float, float] | None:
        """Where the mouse ray meets the (nearly flat) playfield."""
        o, d = self.mouse_ray(mx, my)
        if abs(d.y) < 1e-4:
            return None
        t = (plane_y - o.y) / d.y
        return (o.x + d.x * t, o.z + d.z * t) if t > 0 else None
