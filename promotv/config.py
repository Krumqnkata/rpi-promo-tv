"""Paths, settings and persistent state."""
import copy
import json
import logging
import os
import threading

log = logging.getLogger(__name__)

DEV = os.environ.get("PROMOTV_DEV") == "1"

_default_data = os.path.join(os.getcwd(), "devdata") if DEV else "/var/lib/promotv"
DATA_DIR = os.environ.get("PROMOTV_DATA", _default_data)
MEDIA_DIR = os.path.join(DATA_DIR, "media")
THUMB_DIR = os.path.join(DATA_DIR, "thumbs")
TMP_DIR = os.path.join(DATA_DIR, "tmp")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
META_FILE = os.path.join(DATA_DIR, "meta.json")
RUN_DIR = os.environ.get("PROMOTV_RUN", os.path.join(DATA_DIR, "run") if DEV else "/run/promotv")
MPV_CONF = os.environ.get("PROMOTV_MPV_CONF", "/etc/promotv/mpv.conf")

HTTP_PORT = int(os.environ.get("PROMOTV_PORT", "8080" if DEV else "80"))
AP_IP = os.environ.get("PROMOTV_AP_IP", "192.168.4.1")
AP_CONNECTION = os.environ.get("PROMOTV_AP_CON", "promotv-ap")

# Keep this much space free on the SD card when uploading (bytes).
RESERVED_SPACE = 300 * 1024 * 1024

VIDEO_EXT = {".mp4", ".m4v", ".mkv", ".mov", ".avi", ".webm", ".ts", ".mpg", ".mpeg", ".wmv", ".flv"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}

MODES = ("all", "shuffle", "single")
# auto = CEC standby, and if the TV does not confirm it, also switch the HDMI signal off
# cec  = CEC only;  hdmi = only switch the HDMI signal off (TV sleeps on "no signal")
POWER_OFF_MODES = ("auto", "cec", "hdmi")
CLOCK_FILE = os.path.join(DATA_DIR, "clock")

DEFAULT_STATE = {
    # Ordered playlist:
    # [{"file": "name.mp4", "enabled": true, "start": "2026-12-01" | null, "end": "2026-12-24" | null}]
    "playlist": [],
    # all = whole list in order, shuffle = random order, single = one file on repeat
    "mode": "all",
    "single_file": None,
    "image_duration": 10,
    "volume": 100,
    "fade": True,
    "fade_duration": 0.6,
    "power_off_mode": "auto",
    "schedule": {
        "enabled": False,
        # Monday .. Sunday
        "days": [{"enabled": i < 6, "on": "09:00", "off": "19:00"} for i in range(7)],
    },
}


def ensure_dirs():
    for d in (DATA_DIR, MEDIA_DIR, THUMB_DIR, TMP_DIR, RUN_DIR):
        os.makedirs(d, exist_ok=True)


def atomic_write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return copy.deepcopy(default)
    except (OSError, ValueError) as e:
        log.warning("Cannot read %s (%s), using defaults", path, e)
        return copy.deepcopy(default)


class State:
    """Thread-safe persistent settings stored as JSON."""

    def __init__(self, path=STATE_FILE):
        self.path = path
        self._lock = threading.RLock()
        data = read_json(path, DEFAULT_STATE)
        merged = copy.deepcopy(DEFAULT_STATE)
        merged.update({k: v for k, v in data.items() if k in DEFAULT_STATE})
        days = merged["schedule"].get("days") or []
        if len(days) != 7:
            merged["schedule"] = copy.deepcopy(DEFAULT_STATE["schedule"])
        if merged["mode"] not in MODES:
            merged["mode"] = "all"
        if merged["power_off_mode"] not in POWER_OFF_MODES:
            merged["power_off_mode"] = "auto"
        self._data = merged

    def get(self, key=None):
        with self._lock:
            if key is None:
                return copy.deepcopy(self._data)
            return copy.deepcopy(self._data[key])

    def update(self, fn):
        """Call fn(data) under the lock, then save. Returns fn's result."""
        with self._lock:
            result = fn(self._data)
            atomic_write_json(self.path, self._data)
            return result

    def set(self, **values):
        def apply(d):
            d.update(values)
        self.update(apply)
