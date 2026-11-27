"""Weekly on/off schedule for the TV."""
import logging
import threading
import time
from datetime import datetime, timedelta

from . import system

log = logging.getLogger(__name__)

DAY_NAMES = ["Понеделник", "Вторник", "Сряда", "Четвъртък", "Петък", "Събота", "Неделя"]
DAY_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]


def parse_hm(text):
    h, m = text.split(":")
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(text)
    return h, m


def validate(schedule):
    days = schedule.get("days")
    if not isinstance(days, list) or len(days) != 7:
        raise ValueError("Графикът трябва да съдържа 7 дни.")
    clean = []
    for d in days:
        on, off = str(d.get("on", "")), str(d.get("off", ""))
        parse_hm(on)
        parse_hm(off)
        if on == off and d.get("enabled"):
            raise ValueError("Часът за включване и изключване не може да е еднакъв.")
        clean.append({"enabled": bool(d.get("enabled")), "on": on, "off": off})
    return {"enabled": bool(schedule.get("enabled")), "days": clean}


def _intervals(schedule, start_date, days):
    """Yield (on_dt, off_dt) intervals for `days` days starting at start_date."""
    for k in range(days):
        d = start_date + timedelta(days=k)
        day = schedule["days"][d.weekday()]
        if not day["enabled"]:
            continue
        on = datetime.combine(d, datetime.min.time()).replace(hour=parse_hm(day["on"])[0],
                                                              minute=parse_hm(day["on"])[1])
        off = datetime.combine(d, datetime.min.time()).replace(hour=parse_hm(day["off"])[0],
                                                               minute=parse_hm(day["off"])[1])
        if off <= on:  # e.g. 18:00 -> 02:00 goes past midnight
            off += timedelta(days=1)
        yield on, off


def desired_on(schedule, now):
    return any(on <= now < off for on, off in _intervals(schedule, now.date() - timedelta(days=1), 2))


def next_event(schedule, now):
    """Return ("on"|"off", datetime) of the next change, or None."""
    if not schedule["enabled"]:
        return None
    events = []
    for on, off in _intervals(schedule, now.date() - timedelta(days=1), 9):
        events += [("on", on), ("off", off)]
    state = desired_on(schedule, now)
    for kind, when in sorted(events, key=lambda e: e[1]):
        if when > now and (kind == "on") != state:
            # skip events that do not change anything (overlapping intervals)
            if desired_on(schedule, when) != state:
                return kind, when
    return None


class Scheduler:
    def __init__(self, state, tv):
        self.state = state
        self.tv = tv            # object with power_on(source) / power_off(source)
        self._last = None       # last desired state we acted on
        self._wake = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, name="scheduler", daemon=True).start()

    def poke(self, reset=False):
        """Re-evaluate now (after clock or schedule changes)."""
        if reset:
            self._last = None
        self._wake.set()

    def _loop(self):
        time.sleep(5)  # let CEC start first
        while True:
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            self._wake.wait(20)
            self._wake.clear()

    def _tick(self):
        sched = self.state.get("schedule")
        if not sched["enabled"]:
            self._last = None
            return
        want = desired_on(sched, datetime.fromtimestamp(system.now()))
        if want != self._last:
            log.info("Schedule: TV should be %s", "ON" if want else "OFF")
            self._last = want
            if want:
                self.tv.power_on("schedule")
            else:
                self.tv.power_off("schedule")
