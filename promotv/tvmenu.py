"""On-screen menu on the TV, controlled with the TV remote over HDMI-CEC.

Drawn with mpv's OSD (ASS subtitles markup) on top of the video.
"""
import logging
import threading
import time
from datetime import datetime

from . import config, system
from .player import OVERLAY_MENU, OVERLAY_TOAST
from .scheduler import DAY_SHORT, next_event

log = logging.getLogger(__name__)

try:
    import qrcode  # python3-qrcode
except ImportError:  # pragma: no cover
    qrcode = None

AUTO_CLOSE = 45          # seconds without a key press
VISIBLE_ROWS = 10

MODE_LABELS = {"all": "Целия списък по ред", "shuffle": "Разбъркано", "single": "Само един файл"}

# ASS colours are &HBBGGRR&
C_TEXT = "&HFFFFFF&"
C_DIM = "&HB0B0B0&"
C_ACCENT = "&H00B4FF&"   # amber
C_SEL_BG = "&H6E3A12&"   # dark blue
C_BG = "&H141414&"
C_OK = "&H6FD46F&"
C_WARN = "&H4FA8FF&"


def esc(text):
    return str(text).replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def clip(text, n=48):
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def rect(x, y, w, h, color, alpha="&H00&"):
    return (f"{{\\an7\\pos({x},{y})\\bord0\\shad0\\1c{color}\\1a{alpha}\\p1}}"
            f"m 0 0 l {w} 0 {w} {h} 0 {h}{{\\p0}}")


def text(x, y, s, size=42, color=C_TEXT, bold=False, align=7):
    b = "\\b1" if bold else ""
    return (f"{{\\an{align}\\pos({x},{y})\\fnDejaVu Sans\\fs{size}{b}\\bord2\\shad0"
            f"\\3c&H000000&\\1c{color}}}{esc(s)}")


class TvMenu:
    def __init__(self, app):
        self.app = app
        self.screen = None       # None = closed
        self.sel = 0
        self.scroll = 0
        self.time_fields = None
        self.time_field = 0
        self._last_key = 0
        self._lock = threading.RLock()
        self._toast_timer = None
        threading.Thread(target=self._auto_close, name="menu-timeout", daemon=True).start()

    # ------------------------------------------------------------------ input
    def on_key(self, key):
        with self._lock:
            self._last_key = time.time()
            if self.screen is None:
                self._closed_key(key)
            else:
                getattr(self, f"_key_{self.screen}")(key)
                if self.screen is not None:
                    self.render()

    def _closed_key(self, key):
        p = self.app.player
        if key in ("ok", "menu", "up", "down"):
            self.open("main")
        elif key in ("right", "next", "ff"):
            p.next()
            self.toast("Следващ файл ►")
        elif key in ("left", "prev", "rew"):
            p.prev()
            self.toast("◄ Предишен файл")
        elif key in ("play", "pause", "playpause"):
            if key == "play":
                p.set_pause(False)
            elif key == "pause":
                p.set_pause(True)
            else:
                p.toggle_pause()
            self.toast("Пауза" if p.is_paused() else "Възпроизвеждане ►")

    def open(self, screen, sel=0):
        self.screen, self.sel, self.scroll = screen, sel, 0
        self.render()

    def close(self):
        self.screen = None
        self.app.player.overlay(OVERLAY_MENU, None)

    def _auto_close(self):
        while True:
            time.sleep(5)
            with self._lock:
                if self.screen is not None and time.time() - self._last_key > AUTO_CLOSE:
                    self.close()

    def _move(self, key, count):
        if key == "up":
            self.sel = (self.sel - 1) % count
        elif key == "down":
            self.sel = (self.sel + 1) % count

    # ------------------------------------------------------------------ main
    def _main_items(self):
        p, st = self.app.player, self.app.state.get()
        return [
            ("pause", "Продължи" if p.is_paused() else "Пауза"),
            ("next", "Следващ файл"),
            ("prev", "Предишен файл"),
            ("files", "Избери файл…"),
            ("mode", f"Режим: {MODE_LABELS[st['mode']]}"),
            ("tvoff", "Изключи телевизора"),
            ("time", "Сверяване на часа…"),
            ("wifi", "Wi-Fi и уеб панел…"),
            ("info", "Информация…"),
            ("close", "Затвори"),
        ]

    def _key_main(self, key):
        items = self._main_items()
        self._move(key, len(items))
        action = items[self.sel][0]
        if key == "back" or (key == "left" and action != "mode"):
            self.close()
            return
        if action == "mode" and key in ("left", "right", "ok"):
            modes = list(MODE_LABELS)
            cur = modes.index(self.app.state.get("mode"))
            self.app.set_mode(modes[(cur + (-1 if key == "left" else 1)) % len(modes)])
            return
        if key not in ("ok", "right"):
            return
        p = self.app.player
        if action == "pause":
            p.toggle_pause()
        elif action == "next":
            p.next()
        elif action == "prev":
            p.prev()
        elif action == "files":
            files = self._file_choices()
            cur = p.current()
            self.open("files", files.index(cur) if cur in files else 0)
        elif action == "tvoff":
            self.close()
            self.app.tv_off("menu")
        elif action == "time":
            t = datetime.fromtimestamp(system.now())
            self.time_fields = [t.day, t.month, t.year, t.hour, t.minute]
            self.time_field = 3
            self.open("time")
        elif action in ("wifi", "info"):
            self.open(action)
        elif action == "close":
            self.close()

    # ------------------------------------------------------------------ files
    def _file_choices(self):
        st = self.app.state.get()
        if st["mode"] == "single":
            return [i["file"] for i in st["playlist"]]
        return [i["file"] for i in st["playlist"] if i["enabled"]]

    def _key_files(self, key):
        files = self._file_choices()
        if key in ("back", "left") or not files:
            self.open("main", 3)
            return
        self._move(key, len(files))
        if key in ("ok", "right"):
            self.app.player.jump_to(files[self.sel])
            self.close()
            self.toast(f"► {clip(files[self.sel], 40)}")

    # ------------------------------------------------------------------ time
    _TIME_LIMITS = [(1, 31), (1, 12), (2024, 2099), (0, 23), (0, 59)]

    def _key_time(self, key):
        if key == "back":
            self.open("main", 6)
            return
        f = self.time_fields
        if key == "left":
            self.time_field = (self.time_field - 1) % 5
        elif key == "right":
            self.time_field = (self.time_field + 1) % 5
        elif key in ("up", "down"):
            lo, hi = self._TIME_LIMITS[self.time_field]
            v = f[self.time_field] + (1 if key == "up" else -1)
            f[self.time_field] = lo if v > hi else hi if v < lo else v
        elif key.startswith("num"):
            n = int(key[3:])
            v = f[self.time_field] * 10 + n
            lo, hi = self._TIME_LIMITS[self.time_field]
            f[self.time_field] = v if lo <= v <= hi else n
        elif key == "ok":
            try:
                dt = datetime(f[2], f[1], f[0], f[3], f[4])
            except ValueError:
                self.toast("Невалидна дата")
                return
            self.app.set_clock(dt.timestamp(), "tv")
            self.close()
            self.toast(f"Часът е сверен: {dt:%d.%m.%Y %H:%M}")

    # ------------------------------------------------------------------ info screens
    def _key_wifi(self, key):
        if key in ("back", "left", "ok"):
            self.open("main", 7)

    def _key_info(self, key):
        if key in ("back", "left", "ok"):
            self.open("main", 8)

    # ------------------------------------------------------------------ drawing
    def toast(self, message, seconds=2.5):
        x, y, w = 1920 - 80, 70, 40 + len(message) * 24
        ev = [rect(x - w, y - 10, w, 76, C_BG, "&H30&"), text(x - 20, y + 2, message, 44, align=9)]
        self.app.player.overlay(OVERLAY_TOAST, "\n".join(ev), z=2)
        if self._toast_timer:
            self._toast_timer.cancel()
        self._toast_timer = threading.Timer(seconds, lambda: self.app.player.overlay(OVERLAY_TOAST, None))
        self._toast_timer.daemon = True
        self._toast_timer.start()

    def render(self):
        if self.screen is None:
            return
        ev = getattr(self, f"_draw_{self.screen}")()
        self.app.player.overlay(OVERLAY_MENU, "\n".join(ev), z=1)

    def _frame(self, title, height, width=1000, x=110, y=90):
        ev = [rect(x, y, width, height, C_BG, "&H18&"),
              rect(x, y, width, 96, C_SEL_BG, "&H00&"),
              text(x + 40, y + 20, title, 52, bold=True)]
        return ev, x, y + 120

    def _footer(self, ev, x, y, hint):
        ev.append(text(x + 40, y, hint, 32, C_DIM))

    def _list(self, title, labels, current=None):
        rows = min(VISIBLE_ROWS, len(labels))
        if self.sel < self.scroll:
            self.scroll = self.sel
        elif self.sel >= self.scroll + rows:
            self.scroll = self.sel - rows + 1
        ev, x, y = self._frame(title, 120 + rows * 70 + 90)
        for i in range(self.scroll, self.scroll + rows):
            yy = y + (i - self.scroll) * 70
            if i == self.sel:
                ev.append(rect(x + 16, yy - 6, 968, 64, C_SEL_BG, "&H00&"))
                ev.append(rect(x + 16, yy - 6, 8, 64, C_ACCENT, "&H00&"))
            color = C_ACCENT if labels[i] == current else C_TEXT
            ev.append(text(x + 48, yy, clip(labels[i]), 42, color))
        if len(labels) > rows:
            ev.append(text(x + 960, y - 110, f"{self.sel + 1}/{len(labels)}", 32, C_DIM, align=9))
        self._footer(ev, x, y + rows * 70 + 20, "▲▼ избор    OK потвърди    ◄ назад")
        return ev

    def _draw_main(self):
        cur = self.app.player.current()
        ev = self._list("Меню", [lbl for _, lbl in self._main_items()])
        if cur:
            ev.append(text(1150, 110, "Сега:", 34, C_DIM))
            ev.append(text(1150, 155, clip(cur, 34), 38))
        return ev

    def _draw_files(self):
        files = self._file_choices()
        if not files:
            ev, x, y = self._frame("Файлове", 330)
            ev.append(text(x + 48, y, "Няма файлове. Качете от уеб панела.", 40, C_DIM))
            self._footer(ev, x, y + 110, "◄ назад")
            return ev
        return self._list("Избери файл", files, current=self.app.player.current())

    def _draw_time(self):
        ev, x, y = self._frame("Сверяване на часа", 470, width=1100)
        f = self.time_fields
        parts = [f"{f[0]:02d}", ".", f"{f[1]:02d}", ".", f"{f[2]}", "   ", f"{f[3]:02d}", ":", f"{f[4]:02d}"]
        field_of_part = {0: 0, 2: 1, 4: 2, 6: 3, 8: 4}
        s = ""
        for i, p in enumerate(parts):
            if field_of_part.get(i) == self.time_field:
                s += f"{{\\1c{C_ACCENT}\\u1}}{p}{{\\u0\\1c{C_TEXT}}}"
            else:
                s += p
        ev.append(f"{{\\an7\\pos({x + 48},{y + 10})\\fnDejaVu Sans\\fs96\\bord2\\shad0\\1c{C_TEXT}}}{s}")
        trusted = system.time_trusted()
        ev.append(text(x + 48, y + 150, "Часът е сверен" if trusted else "Часът НЕ е сверен след включване",
                       36, C_OK if trusted else C_WARN))
        self._footer(ev, x, y + 230, "◄► поле    ▲▼ / цифри промяна")
        self._footer(ev, x, y + 280, "OK запази    BACK отказ")
        return ev

    def _draw_wifi(self):
        w = system.get_wifi()
        ev, x, y = self._frame("Wi-Fi и уеб панел", 700, width=1500)
        rows = [("Мрежа:", w["ssid"]), ("Парола:", w["password"]), ("Адрес:", f"http://{config.AP_IP}")]
        for i, (k, v) in enumerate(rows):
            ev.append(text(x + 48, y + i * 80, k, 40, C_DIM))
            ev.append(text(x + 260, y + i * 80, v, 44, bold=True))
        ev.append(text(x + 48, y + 270, "Свържете телефона към мрежата –", 34, C_DIM))
        ev.append(text(x + 48, y + 315, "панелът се отваря сам или въведете адреса.", 34, C_DIM))
        ev.append(text(x + 48, y + 360, "Сканирайте QR кода с камерата →", 34, C_DIM))
        qr = qr_events(f"WIFI:T:WPA;S:{_qr_esc(w['ssid'])};P:{_qr_esc(w['password'])};;", x + 1010, y - 10, 440)
        ev += qr
        self._footer(ev, x, y + 470, "OK / ◄ назад")
        return ev

    def _draw_info(self):
        a = self.app
        st = a.state.get()
        t = datetime.fromtimestamp(system.now())
        disk = system.disk_usage()
        nxt = next_event(st["schedule"], t)
        if not st["schedule"]["enabled"]:
            sched = "изключен"
        elif nxt:
            kind, when = nxt
            sched = f"{'ВКЛ' if kind == 'on' else 'ИЗКЛ'} {DAY_SHORT[when.weekday()]} {when:%H:%M}"
        else:
            sched = "няма предстоящи"
        rows = [
            ("Час:", f"{t:%d.%m.%Y %H:%M}" + ("" if system.time_trusted() else "  (не е сверен!)")),
            ("Файлове:", f"{len(st['playlist'])}  (в плейлиста: {len(a.player.files)})"),
            ("Режим:", MODE_LABELS[st["mode"]]),
            ("График:", sched),
            ("Свободно:", f"{disk['free'] / 1e9:.1f} GB от {disk['total'] / 1e9:.1f} GB"),
            ("IP:", ", ".join(system.ip_addresses())),
            ("CEC:", "работи" if a.cec.available else "не отговаря"),
        ]
        ev, x, y = self._frame("Информация", 120 + len(rows) * 66 + 90, width=1300)
        for i, (k, v) in enumerate(rows):
            ev.append(text(x + 48, y + i * 66, k, 38, C_DIM))
            ev.append(text(x + 300, y + i * 66, clip(v, 50), 38))
        self._footer(ev, x, y + len(rows) * 66 + 20, "OK / ◄ назад")
        return ev


def _qr_esc(s):
    for ch in "\\;,:\"":
        s = s.replace(ch, "\\" + ch)
    return s


def qr_events(data, x, y, size):
    """Draw a QR code as ASS vector shapes. Returns [] if python3-qrcode is missing."""
    if qrcode is None:
        return []
    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    cell = max(1, size // n)
    total = cell * n
    shapes = []
    for r, row in enumerate(matrix):
        c = 0
        while c < n:
            if row[c]:
                start = c
                while c < n and row[c]:
                    c += 1
                x0, x1, y0, y1 = start * cell, c * cell, r * cell, (r + 1) * cell
                shapes.append(f"m {x0} {y0} l {x1} {y0} {x1} {y1} {x0} {y1}")
            else:
                c += 1
    return [rect(x, y, total, total, "&HFFFFFF&"),
            f"{{\\an7\\pos({x},{y})\\bord0\\shad0\\1c&H000000&\\p1}}{' '.join(shapes)}{{\\p0}}"]
