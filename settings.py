"""Global constants and persistent user settings for Spirewatch."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def base_dir() -> Path:
    """Folder that holds writable files (settings, scores, cache)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_dir() -> Path:
    """Folder that holds bundled read-only resources (data/*.json)."""
    return Path(getattr(sys, "_MEIPASS", base_dir()))


ROOT = base_dir()
DATA_DIR = resource_dir() / "data"
CACHE_DIR = ROOT / "cache"
SCREENSHOT_DIR = ROOT / "screenshots"
SETTINGS_FILE = ROOT / "settings.json"
SCORES_FILE = ROOT / "scores.json"

GAME_TITLE = "Spirewatch"
VERSION = "1.0.0"

# --- Grid / world -----------------------------------------------------------------
CELL = 2.0                  # world units per build / pathing cell
SUB = 4                     # terrain quads per cell edge
BORDER = 16                 # scenery cells around the playable grid
SEA_LEVEL = -0.22
FLY_HEIGHT = 4.2
ROAD_DEPTH = 0.38           # how far roads sink below the building terraces

# --- Time -------------------------------------------------------------------------
DAY_LENGTH_SECONDS = 1500.0  # a full day/night cycle (maps may override)
START_HOUR = 10.0

# --- Rules ------------------------------------------------------------------------
SELL_REFUND = 0.75
FULL_REFUND_SECONDS = 4.0   # a tower sold this soon after placing is fully refunded
BUILD_TIME = 1.1
UPGRADE_TIME = 1.4
MAX_LEVEL = 3
SPEEDS = (1.0, 2.0, 3.0)

PLAYER_COLOR = (0.20, 0.45, 0.95)

PERSIST = True              # the scripted tests switch this off so they never touch your settings or scores

RESOLUTIONS: list[tuple[int, int]] = [
    (1280, 720), (1600, 900), (1920, 1080), (2560, 1440), (3840, 2160),
]


@dataclass
class UserSettings:
    """Options the player can change from the settings menu."""

    scroll_speed: float = 1.0
    edge_scroll: bool = True
    master_volume: float = 0.8
    sfx_volume: float = 0.9
    music_volume: float = 0.6
    ambient_volume: float = 0.5
    resolution: list[int] = field(default_factory=lambda: [1600, 900])
    fullscreen: bool = False
    show_fps: bool = False
    damage_numbers: bool = True
    auto_send: bool = False
    last_map: str = "emberfall_road"
    last_difficulty: str = "normal"
    last_mode: str = "campaign"

    @classmethod
    def load(cls) -> "UserSettings":
        """Load settings from disk, falling back to defaults on any problem."""
        s = cls()
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            for key, value in raw.items():
                if hasattr(s, key):
                    setattr(s, key, value)
        except (OSError, ValueError):
            pass
        return s

    def save(self) -> None:
        """Write settings to disk (errors are ignored)."""
        if not PERSIST:
            return
        try:
            SETTINGS_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except OSError:
            pass


user_settings = UserSettings.load()
