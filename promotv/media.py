"""Media library: files on disk, playlist order, probing, thumbnails, warnings."""
import hashlib
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import threading

from . import config

log = logging.getLogger(__name__)

_BAD_CHARS = re.compile(r'[\x00-\x1f/\\:*?"<>|]')


def media_type(name):
    ext = os.path.splitext(name)[1].lower()
    if ext in config.VIDEO_EXT:
        return "video"
    if ext in config.IMAGE_EXT:
        return "image"
    return None


def sanitize_name(name):
    name = os.path.basename(name.replace("\\", "/"))
    name = _BAD_CHARS.sub("_", name).strip().strip(".")
    stem, ext = os.path.splitext(name)
    stem = stem[:120].strip() or "file"
    return stem + ext.lower()


def thumb_id(name):
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]


def _parse_fps(rate):
    try:
        num, den = rate.split("/")
        den = float(den)
        return float(num) / den if den else 0.0
    except (ValueError, AttributeError):
        return 0.0


def probe(path):
    """Return technical info about a media file using ffprobe (or None)."""
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
            capture_output=True, timeout=60, check=True,
        ).stdout
        data = json.loads(out.decode("utf-8", "replace"))
    except (subprocess.SubprocessError, ValueError, OSError) as e:
        log.warning("ffprobe failed for %s: %s", path, e)
        return {"ok": False}
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    info = {"ok": video is not None}
    if video:
        info.update(
            codec=video.get("codec_name"),
            profile=video.get("profile"),
            pix_fmt=video.get("pix_fmt"),
            width=video.get("width"),
            height=video.get("height"),
            fps=round(_parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate")), 2),
        )
    info["audio"] = audio.get("codec_name") if audio else None
    try:
        info["duration"] = float(data.get("format", {}).get("duration") or 0)
    except ValueError:
        info["duration"] = 0
    return info


def warnings_for(kind, info):
    """Human readable (Bulgarian) warnings about files a Raspberry Pi 3 may struggle with."""
    if info is None:
        return []
    if not info.get("ok"):
        return ["Файлът не може да бъде прочетен – може да е повреден или в неподдържан формат."]
    w = []
    width, height = info.get("width") or 0, info.get("height") or 0
    if kind == "image":
        if width > 4096 or height > 4096:
            w.append(f"Много голямо изображение ({width}×{height}) – може да се зарежда бавно. "
                     "Препоръка: до 1920×1080.")
        return w
    codec = (info.get("codec") or "").lower()
    if codec != "h264":
        w.append(f"Кодек {codec.upper() or '?'}: Raspberry Pi 3 декодира хардуерно само H.264 – "
                 "видеото може да насича или да не тръгне. Препоръка: MP4 с H.264.")
    elif "10" in (info.get("pix_fmt") or "") or "10" in (info.get("profile") or ""):
        w.append("10-битово H.264 не се декодира хардуерно – видеото ще насича. Препоръка: 8-битово H.264.")
    if width > 1920 or height > 1088:
        w.append(f"Резолюция {width}×{height} е над 1920×1080 – Raspberry Pi 3 няма да я възпроизведе гладко.")
    fps = info.get("fps") or 0
    if fps > 31 and height > 720:
        w.append(f"{fps:g} кадъра/сек при {height}p е твърде много за Raspberry Pi 3 – "
                 "препоръка: до 30 кадъра/сек при 1080p.")
    return w


class Library:
    def __init__(self, state):
        self.state = state
        self._lock = threading.RLock()
        self._meta = config.read_json(config.META_FILE, {})
        self._jobs = queue.Queue()
        self.on_change = []  # callbacks called when the playlist changes
        threading.Thread(target=self._worker, name="media-probe", daemon=True).start()
        self.sync()

    # ---------- helpers ----------
    def path(self, name):
        p = os.path.join(config.MEDIA_DIR, name)
        if os.path.dirname(os.path.abspath(p)) != os.path.abspath(config.MEDIA_DIR):
            raise ValueError("bad name")
        return p

    def exists(self, name):
        try:
            return os.path.isfile(self.path(name))
        except ValueError:
            return False

    def unique_name(self, name):
        stem, ext = os.path.splitext(name)
        candidate, n = name, 2
        while os.path.exists(os.path.join(config.MEDIA_DIR, candidate)):
            candidate = f"{stem} ({n}){ext}"
            n += 1
        return candidate

    def _save_meta(self):
        config.atomic_write_json(config.META_FILE, self._meta)

    def _notify(self):
        for cb in self.on_change:
            try:
                cb()
            except Exception:  # noqa: BLE001
                log.exception("on_change callback failed")

    # ---------- probing ----------
    def _worker(self):
        while True:
            name = self._jobs.get()
            try:
                self._process(name)
            except Exception:  # noqa: BLE001
                log.exception("Processing %s failed", name)

    def _process(self, name):
        if not self.exists(name):
            return
        path = self.path(name)
        st = os.stat(path)
        kind = media_type(name)
        info = probe(path)
        tid = thumb_id(name)
        thumb = os.path.join(config.THUMB_DIR, tid + ".jpg")
        has_thumb = False
        if shutil.which("ffmpeg"):
            seek = []
            if kind == "video":
                dur = (info or {}).get("duration") or 0
                seek = ["-ss", f"{min(2.0, dur / 3):.2f}"]
            try:
                subprocess.run(
                    ["nice", "-n", "19", "ffmpeg", "-v", "error", "-y", *seek, "-i", path,
                     "-frames:v", "1", "-vf", "scale=320:-2", thumb],
                    capture_output=True, timeout=120, check=True,
                )
                has_thumb = True
            except (subprocess.SubprocessError, OSError) as e:
                log.warning("Thumbnail failed for %s: %s", name, e)
        with self._lock:
            self._meta[name] = {
                "size": st.st_size, "mtime": int(st.st_mtime), "info": info,
                "warnings": warnings_for(kind, info), "thumb": tid if has_thumb else None,
            }
            self._save_meta()

    def process_now(self, name):
        """Probe synchronously (used right after upload so warnings can be shown)."""
        self._process(name)
        with self._lock:
            return self._meta.get(name, {}).get("warnings", [])

    # ---------- sync / listing ----------
    def sync(self, notify=False):
        """Make the playlist match the files in the media folder."""
        try:
            files = sorted(f for f in os.listdir(config.MEDIA_DIR)
                           if media_type(f) and os.path.isfile(os.path.join(config.MEDIA_DIR, f)))
        except FileNotFoundError:
            files = []
        on_disk = set(files)

        def apply(d):
            seen = set()
            pl = []
            for item in d["playlist"]:
                if item["file"] in on_disk and item["file"] not in seen:
                    pl.append({"file": item["file"], "enabled": bool(item.get("enabled", True))})
                    seen.add(item["file"])
            for f in files:
                if f not in seen:
                    pl.append({"file": f, "enabled": True})
            changed = pl != d["playlist"]
            d["playlist"] = pl
            if d["single_file"] not in on_disk:
                d["single_file"] = pl[0]["file"] if pl else None
            return changed

        changed = self.state.update(apply)
        with self._lock:
            for name in list(self._meta):
                if name not in on_disk:
                    self._drop_meta(name)
            for name in files:
                m = self._meta.get(name)
                try:
                    st = os.stat(self.path(name))
                except OSError:
                    continue
                if not m or m.get("size") != st.st_size or m.get("mtime") != int(st.st_mtime):
                    self._meta.setdefault(name, {"warnings": [], "info": None, "thumb": None})
                    self._jobs.put(name)
            self._save_meta()
        if notify or changed:
            self._notify()

    def _drop_meta(self, name):
        m = self._meta.pop(name, None)
        if m and m.get("thumb"):
            try:
                os.remove(os.path.join(config.THUMB_DIR, m["thumb"] + ".jpg"))
            except OSError:
                pass

    def list(self):
        pl = self.state.get("playlist")
        out = []
        with self._lock:
            for item in pl:
                name = item["file"]
                m = self._meta.get(name, {})
                info = m.get("info") or {}
                try:
                    size = os.path.getsize(self.path(name))
                except OSError:
                    size = 0
                out.append({
                    "name": name,
                    "enabled": item["enabled"],
                    "type": media_type(name),
                    "size": size,
                    "duration": info.get("duration"),
                    "width": info.get("width"),
                    "height": info.get("height"),
                    "codec": info.get("codec"),
                    "fps": info.get("fps"),
                    "warnings": m.get("warnings", []),
                    "thumb": m.get("thumb"),
                    "processing": m.get("info") is None and shutil.which("ffprobe") is not None,
                })
        return out

    def play_list(self):
        """Files that should be played according to the current mode."""
        st = self.state.get()
        if st["mode"] == "single":
            return [st["single_file"]] if st["single_file"] and self.exists(st["single_file"]) else []
        return [i["file"] for i in st["playlist"] if i["enabled"] and self.exists(i["file"])]

    # ---------- mutations ----------
    def add_uploaded(self, tmp_path, original_name):
        name = sanitize_name(original_name)
        if not media_type(name):
            raise ValueError("Неподдържан тип файл")
        with self._lock:
            name = self.unique_name(name)
            os.replace(tmp_path, os.path.join(config.MEDIA_DIR, name))
        warnings = self.process_now(name)
        self.sync(notify=True)
        return name, warnings

    def delete(self, name):
        os.remove(self.path(name))
        with self._lock:
            self._drop_meta(name)
            self._save_meta()
        self.sync(notify=True)

    def rename(self, old, new):
        if not self.exists(old):
            raise FileNotFoundError(old)
        new = sanitize_name(os.path.splitext(new)[0] + os.path.splitext(old)[1])
        if new == old:
            return old
        with self._lock:
            new = self.unique_name(new)
            os.replace(self.path(old), self.path(new))
            m = self._meta.pop(old, None)
            if m:
                if m.get("thumb"):
                    tid = thumb_id(new)
                    try:
                        os.replace(os.path.join(config.THUMB_DIR, m["thumb"] + ".jpg"),
                                   os.path.join(config.THUMB_DIR, tid + ".jpg"))
                        m["thumb"] = tid
                    except OSError:
                        m["thumb"] = None
                st = os.stat(self.path(new))
                m["mtime"] = int(st.st_mtime)
                self._meta[new] = m
                self._save_meta()

        def apply(d):
            for item in d["playlist"]:
                if item["file"] == old:
                    item["file"] = new
            if d["single_file"] == old:
                d["single_file"] = new
        self.state.update(apply)
        self.sync(notify=True)
        return new

    def set_playlist(self, order, enabled):
        """order: list of names; enabled: {name: bool}."""
        def apply(d):
            current = {i["file"]: i for i in d["playlist"]}
            pl = []
            for name in order:
                if name in current:
                    item = current.pop(name)
                    pl.append({"file": name, "enabled": bool(enabled.get(name, item["enabled"]))})
            pl.extend(current.values())  # anything the client did not know about
            d["playlist"] = pl
        self.state.update(apply)
        self._notify()
