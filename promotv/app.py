"""Wires all parts together."""
import logging
import threading
import time
from datetime import datetime

from . import __version__, config, scheduler, system
from .cec import Cec, FakeCec
from .media import Library
from .player import FakePlayer, MpvPlayer
from .scheduler import Scheduler
from .tvmenu import TvMenu, test_card

log = logging.getLogger(__name__)

BACKUP_KEYS = ("mode", "single_file", "image_duration", "volume", "fade", "fade_duration", "power_off_mode")


class App:
    def __init__(self):
        config.ensure_dirs()
        system.restore_clock()
        self.state = config.State()
        self.library = Library(self.state)
        self.player = (FakePlayer if config.DEV else MpvPlayer)(self.library, self.state)
        self.cec = (FakeCec if config.DEV else Cec)()
        self.menu = TvMenu(self)
        self.scheduler = Scheduler(self.state, self)
        self.cec.on_key = self.menu.on_key
        self.cec.on_power = self._tv_power_changed
        self.last_power_source = None
        self._last_off = 0
        self._power_seq = 0

    def start(self):
        self.player.set_idle_text(self.idle_screen())
        self.player.start()
        self.cec.start()
        self.scheduler.start()
        threading.Thread(target=self._date_watch, name="date-watch", daemon=True).start()
        threading.Thread(target=system.clock_saver, name="clock-saver", daemon=True).start()

    def idle_screen(self):
        # No Wi-Fi password here: the screen faces customers. Staff find it in the
        # TV menu ("Wi-Fi и уеб панел") with the remote.
        return test_card(system.get_wifi()["ssid"], f"http://{config.AP_IP}")

    def _date_watch(self):
        """Start/stop promotions when their dates begin or end (also after clock changes)."""
        while True:
            time.sleep(30)
            try:
                if self.player.output_on and self.player.needs_reload():
                    log.info("Playlist changed because of promotion dates")
                    self.player.reload()
            except Exception:  # noqa: BLE001
                log.exception("date watch failed")

    # ---------- TV power ----------
    def tv_on(self, source="manual"):
        self.last_power_source = source
        self._power_seq += 1
        self.player.set_output(True)
        self.player.set_pause(False)
        if self.state.get("power_off_mode") == "hdmi":
            return True
        return self.cec.power_on()

    def tv_off(self, source="manual"):
        """Turn the TV off. Returns False only if nothing could be done."""
        self.last_power_source = source
        self._last_off = time.time()
        self._power_seq += 1
        seq = self._power_seq
        mode = self.state.get("power_off_mode")
        if mode == "hdmi":
            threading.Thread(target=self.player.set_output, args=(False,), daemon=True).start()
            return True
        ok = self.cec.power_off()
        if mode == "cec":
            return ok
        # auto: if the TV does not confirm standby over CEC, also stop the HDMI signal
        # so the TV goes to sleep by itself on "no signal".
        def check():
            if ok:
                time.sleep(12)
                if seq != self._power_seq:
                    return  # turned on again meanwhile
                reply = self.cec.query_power()
                if reply == "standby":
                    return
                log.warning("TV did not confirm standby (%s) - switching HDMI signal off", reply)
            if seq == self._power_seq:
                self.player.set_output(False)
        threading.Thread(target=check, daemon=True).start()
        return True

    def _tv_power_changed(self, value):
        # Someone turned the TV on with its own remote while our HDMI signal was off.
        if value == "on" and not self.player.output_on and time.time() - self._last_off > 30:
            log.info("TV turned on - switching HDMI signal back on")
            self._power_seq += 1
            threading.Thread(target=self.player.set_output, args=(True,), daemon=True).start()

    # The scheduler calls these names.
    def power_on(self, source):
        return self.tv_on(source)

    def power_off(self, source):
        return self.tv_off(source)

    # ---------- settings ----------
    def set_mode(self, mode, single_file=None):
        if mode not in config.MODES:
            raise ValueError("bad mode")
        values = {"mode": mode}
        if single_file is not None:
            if not self.library.exists(single_file):
                raise ValueError("Файлът не съществува")
            values["single_file"] = single_file
        self.state.set(**values)
        self.player.reload()

    def apply_settings(self, data):
        """Validate and apply playback/TV settings (dict with any of BACKUP_KEYS)."""
        st = self.state.get()
        try:
            dur = int(data.get("image_duration", st["image_duration"]))
            vol = max(0, min(100, int(data.get("volume", st["volume"]))))
            fade = bool(data.get("fade", st["fade"]))
            fade_dur = float(data.get("fade_duration", st["fade_duration"]))
        except (TypeError, ValueError):
            raise ValueError("Невалидна стойност")
        if not 1 <= dur <= 3600:
            raise ValueError("Времето за снимка трябва да е между 1 и 3600 секунди.")
        if not 0.2 <= fade_dur <= 3:
            raise ValueError("Продължителността на прехода трябва да е между 0.2 и 3 секунди.")
        power_mode = data.get("power_off_mode", st["power_off_mode"])
        if power_mode not in config.POWER_OFF_MODES:
            raise ValueError("Невалиден начин на изключване")
        self.state.set(image_duration=dur, volume=vol, fade=fade, fade_duration=fade_dur,
                       power_off_mode=power_mode)
        self.player.set_image_duration(dur)
        self.player.set_volume(vol)
        mode = data.get("mode", st["mode"])
        single = data.get("single_file") or None
        if single is not None and not self.library.exists(single):
            single = None
        self.set_mode(mode, single)

    def set_clock(self, epoch, source):
        diff = system.set_time(epoch, source)
        self.scheduler.poke()
        if self.player.needs_reload():
            self.player.reload()
        return diff

    def wifi_changed(self):
        self.player.set_idle_text(self.idle_screen())

    # ---------- backup ----------
    def backup(self):
        st = self.state.get()
        return {
            "promotv_backup": 1,
            "version": __version__,
            "created": datetime.fromtimestamp(system.now()).isoformat(timespec="seconds"),
            "settings": {k: st[k] for k in BACKUP_KEYS},
            "schedule": st["schedule"],
            "playlist": st["playlist"],
            "wifi": system.get_wifi(),
        }

    def restore(self, data, restore_wifi=False):
        if not isinstance(data, dict) or data.get("promotv_backup") != 1:
            raise ValueError("Това не е файл с резервно копие от PromoTV.")
        report = []
        if isinstance(data.get("schedule"), dict):
            self.state.set(schedule=scheduler.validate(data["schedule"]))
            self.scheduler.poke(reset=True)
            report.append("график")
        if isinstance(data.get("playlist"), list):
            self.library.import_playlist(data["playlist"])
            report.append("ред и периоди на файловете")
        if isinstance(data.get("settings"), dict):
            self.apply_settings({k: v for k, v in data["settings"].items() if k in BACKUP_KEYS})
            report.append("настройки за показване")
        if restore_wifi and isinstance(data.get("wifi"), dict):
            system.set_wifi(str(data["wifi"].get("ssid", "")), str(data["wifi"].get("password", "")))
            threading.Timer(6, self.wifi_changed).start()
            report.append("Wi-Fi")
        return report
