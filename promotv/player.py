"""Controls an mpv process over its JSON IPC socket."""
import json
import logging
import os
import random
import shutil
import socket
import subprocess
import threading
import time

from . import config

log = logging.getLogger(__name__)

OVERLAY_MENU = 1
OVERLAY_IDLE = 2
OVERLAY_TOAST = 3


class BasePlayer:
    """Shared playlist logic. Subclasses implement the low-level calls."""

    def __init__(self, library, state):
        self.library = library
        self.state = state
        self._lock = threading.RLock()
        self.files = []          # what is currently loaded, in order
        self.loaded_mode = None
        self.idle_text = None    # ASS text shown when there is nothing to play
        library.on_change.append(self.reload)

    # --- to be implemented ---
    def start(self): ...
    def current(self): return None
    def is_paused(self): return False
    def set_pause(self, value): ...
    def next(self): ...
    def prev(self): ...
    def jump_to(self, name): ...
    def overlay(self, oid, ass_text, z=0): ...
    def set_volume(self, vol): ...
    def set_image_duration(self, sec): ...

    def toggle_pause(self):
        self.set_pause(not self.is_paused())

    def status(self):
        return {"current": self.current(), "paused": self.is_paused(),
                "count": len(self.files), "mode": self.loaded_mode}

    def build_list(self):
        files = self.library.play_list()
        mode = self.state.get("mode")
        if mode == "shuffle":
            random.shuffle(files)
        return mode, files


class MpvPlayer(BasePlayer):
    def __init__(self, library, state):
        super().__init__(library, state)
        self.sock_path = os.path.join(config.RUN_DIR, "mpv.sock")
        self.proc = None
        self.sock = None
        self._send_lock = threading.Lock()
        self._req = 0
        self._pending = {}
        self._props = {}
        self._overlays = {}

    # ---------- process management ----------
    def _args(self):
        st = self.state.get()
        args = [
            "mpv", "--no-config", "--idle=yes", "--force-window=yes", "--fullscreen",
            "--no-terminal", "--really-quiet", "--no-osc", "--no-input-default-bindings",
            "--input-vo-keyboard=no", "--cursor-autohide=always", "--osd-level=0",
            f"--input-ipc-server={self.sock_path}",
            "--loop-playlist=inf", "--keep-open=no",
            f"--image-display-duration={st['image_duration']}",
            f"--volume={st['volume']}",
            "--osd-font=DejaVu Sans",
        ]
        if os.path.isfile(config.MPV_CONF):
            args.append(f"--include={config.MPV_CONF}")
        return args

    def start(self):
        threading.Thread(target=self._supervise, name="mpv", daemon=True).start()

    def _supervise(self):
        if not shutil.which("mpv"):
            log.error("mpv is not installed")
            return
        while True:
            try:
                os.remove(self.sock_path)
            except FileNotFoundError:
                pass
            log.info("Starting mpv")
            self.proc = subprocess.Popen(self._args(), stdin=subprocess.DEVNULL)
            if self._connect():
                reader = threading.Thread(target=self._read_loop, name="mpv-ipc", daemon=True)
                reader.start()
                self.loaded_mode = None
                self.files = []
                self._overlays_restore()
                self.reload()
                reader.join()  # returns when mpv dies
            self.sock = None
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            log.warning("mpv exited (code %s), restarting in 3 s", self.proc.returncode)
            time.sleep(3)

    def _connect(self):
        for _ in range(100):
            if self.proc.poll() is not None:
                return False
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(self.sock_path)
                self.sock = s
                for i, prop in enumerate(("path", "pause", "idle-active", "playlist-pos")):
                    self._send({"command": ["observe_property", i + 1, prop]})
                return True
            except OSError:
                time.sleep(0.2)
        log.error("Could not connect to mpv IPC socket")
        self.proc.kill()
        return False

    def _read_loop(self):
        buf = b""
        while True:
            try:
                chunk = self.sock.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("event") == "property-change":
                    self._props[msg.get("name")] = msg.get("data")
                elif "request_id" in msg:
                    waiter = self._pending.pop(msg["request_id"], None)
                    if waiter:
                        waiter[1] = msg
                        waiter[0].set()

    # ---------- IPC ----------
    def _send(self, payload, wait=False):
        sock = self.sock
        if sock is None:
            return None
        with self._send_lock:
            self._req += 1
            rid = self._req
            payload = dict(payload, request_id=rid)
            waiter = [threading.Event(), None] if wait else None
            if waiter:
                self._pending[rid] = waiter
            try:
                sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
            except OSError as e:
                log.warning("mpv IPC send failed: %s", e)
                self._pending.pop(rid, None)
                return None
        if waiter and waiter[0].wait(3):
            return waiter[1]
        return None

    def cmd(self, *args, wait=False):
        return self._send({"command": list(args)}, wait=wait)

    def set_prop(self, name, value):
        return self.cmd("set_property", name, value)

    # ---------- playlist ----------
    def reload(self):
        """(Re)load the playlist, keeping the current file playing when possible."""
        with self._lock:
            if self.sock is None:
                return
            mode, files = self.build_list()
            paths = [self.library.path(f) for f in files]
            current = self.current()
            if not files:
                self.cmd("stop")
            elif mode == self.loaded_mode and mode != "single" and current in files:
                # Rotate so the current file stays first: cyclic order stays the same and
                # playback is not interrupted.
                i = files.index(current)
                rotated = paths[i + 1:] + paths[:i]
                self.cmd("playlist-clear", wait=True)
                for p in rotated:
                    self.cmd("loadfile", p, "append")
            else:
                self.cmd("loadfile", paths[0], "replace", wait=True)
                for p in paths[1:]:
                    self.cmd("loadfile", p, "append")
            self.set_prop("loop-file", "inf" if mode == "single" else "no")
            self.files = files
            self.loaded_mode = mode
            self._update_idle()

    def _update_idle(self):
        if not self.files and self.idle_text:
            self.overlay(OVERLAY_IDLE, self.idle_text)
        else:
            self.overlay(OVERLAY_IDLE, None)

    def set_idle_text(self, text):
        self.idle_text = text
        self._update_idle()

    def current(self):
        path = self._props.get("path")
        if not path or self._props.get("idle-active"):
            return None
        return os.path.basename(path)

    def is_paused(self):
        return bool(self._props.get("pause"))

    def set_pause(self, value):
        self.set_prop("pause", bool(value))
        self._props["pause"] = bool(value)

    def next(self):
        self.cmd("playlist-next", "force")

    def prev(self):
        self.cmd("playlist-prev", "force")

    def jump_to(self, name):
        if self.loaded_mode == "single":
            self.state.set(single_file=name)
            self.reload()
            return
        if name not in self.files:
            return
        # mpv's playlist may be rotated (see reload); look the file up there.
        resp = self._send({"command": ["get_property", "playlist"]}, wait=True)
        entries = (resp or {}).get("data") or []
        for idx, e in enumerate(entries):
            if os.path.basename(e.get("filename", "")) == name:
                self.set_prop("playlist-pos", idx)
                self.set_pause(False)
                return

    def set_volume(self, vol):
        self.set_prop("volume", int(vol))

    def set_image_duration(self, sec):
        self.set_prop("image-display-duration", int(sec))

    # ---------- OSD ----------
    def overlay(self, oid, ass_text, z=0):
        if ass_text:
            self._overlays[oid] = (ass_text, z)
            self._send({"command": {"name": "osd-overlay", "id": oid, "format": "ass-events",
                                    "data": ass_text, "res_x": 1920, "res_y": 1080, "z": z}})
        else:
            self._overlays.pop(oid, None)
            self._send({"command": {"name": "osd-overlay", "id": oid, "format": "none", "data": ""}})

    def _overlays_restore(self):
        for oid, (text, z) in list(self._overlays.items()):
            self.overlay(oid, text, z)


class FakePlayer(BasePlayer):
    """Used in dev mode (no mpv): pretends to play each file for 8 seconds."""

    def __init__(self, library, state):
        super().__init__(library, state)
        self._pos = 0
        self._paused = False
        self.overlays = {}

    def start(self):
        self.reload()
        threading.Thread(target=self._tick, daemon=True).start()

    def _tick(self):
        while True:
            time.sleep(8)
            if not self._paused and self.loaded_mode != "single":
                self.next()

    def reload(self):
        with self._lock:
            cur = self.current()
            self.loaded_mode, self.files = self.build_list()
            self._pos = self.files.index(cur) if cur in self.files else 0

    def current(self):
        return self.files[self._pos % len(self.files)] if self.files else None

    def is_paused(self):
        return self._paused

    def set_pause(self, value):
        self._paused = bool(value)

    def next(self):
        if self.files:
            self._pos = (self._pos + 1) % len(self.files)

    def prev(self):
        if self.files:
            self._pos = (self._pos - 1) % len(self.files)

    def jump_to(self, name):
        if self.loaded_mode == "single":
            self.state.set(single_file=name)
            self.reload()
        elif name in self.files:
            self._pos = self.files.index(name)

    def overlay(self, oid, ass_text, z=0):
        if ass_text:
            self.overlays[oid] = ass_text
        else:
            self.overlays.pop(oid, None)

    def set_idle_text(self, text):
        self.idle_text = text
