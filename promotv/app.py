"""Wires all parts together."""
import logging

from . import config, system
from .cec import Cec, FakeCec
from .media import Library
from .player import FakePlayer, MpvPlayer
from .scheduler import Scheduler
from .tvmenu import TvMenu, rect, text

log = logging.getLogger(__name__)


class App:
    def __init__(self):
        config.ensure_dirs()
        self.state = config.State()
        self.library = Library(self.state)
        self.player = (FakePlayer if config.DEV else MpvPlayer)(self.library, self.state)
        self.cec = (FakeCec if config.DEV else Cec)()
        self.menu = TvMenu(self)
        self.scheduler = Scheduler(self.state, self)
        self.cec.on_key = self.menu.on_key
        self.last_power_source = None

    def start(self):
        self.player.set_idle_text(self.idle_screen())
        self.player.start()
        self.cec.start()
        self.scheduler.start()

    def idle_screen(self):
        w = system.get_wifi()
        lines = [
            rect(0, 0, 1920, 1080, "&H141414&"),
            text(960, 330, "Няма файлове за показване", 72, bold=True, align=8),
            text(960, 470, f"Свържете се към Wi-Fi „{w['ssid']}“", 52, align=8),
            text(960, 550, f"парола: {w['password']}", 52, align=8),
            text(960, 650, f"и отворете http://{config.AP_IP}", 52, "&H00B4FF&", align=8),
        ]
        return "\n".join(lines)

    # ---------- actions used by the web panel, the TV menu and the scheduler ----------
    def tv_on(self, source="manual"):
        self.last_power_source = source
        ok = self.cec.power_on()
        self.player.set_pause(False)
        return ok

    def tv_off(self, source="manual"):
        self.last_power_source = source
        return self.cec.power_off()

    # The scheduler calls these names.
    def power_on(self, source):
        return self.tv_on(source)

    def power_off(self, source):
        return self.tv_off(source)

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

    def set_clock(self, epoch, source):
        diff = system.set_time(epoch, source)
        self.scheduler.poke()
        return diff

    def wifi_changed(self):
        self.player.set_idle_text(self.idle_screen())
