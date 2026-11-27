"""HDMI-CEC: turn the TV on/off and receive remote control key presses.

Uses a long running `cec-client` process (package cec-utils): commands are written to its
stdin and the raw CEC traffic it logs is parsed for key presses and power changes.
"""
import logging
import re
import shutil
import subprocess
import threading
import time

log = logging.getLogger(__name__)

# CEC "User Control" codes -> our key names
KEYS = {
    0x00: "ok", 0x2B: "ok",
    0x01: "up", 0x02: "down", 0x03: "left", 0x04: "right",
    0x09: "menu", 0x0A: "menu", 0x0B: "menu", 0x0C: "menu", 0x10: "menu", 0x11: "menu",
    0x0D: "back", 0x91: "back",
    0x44: "play", 0x45: "stop", 0x46: "pause", 0x61: "playpause",
    0x48: "rew", 0x49: "ff", 0x4B: "next", 0x4C: "prev",
    0x71: "blue", 0x72: "red", 0x73: "green", 0x74: "yellow",
}
KEYS.update({0x20 + n: f"num{n}" for n in range(10)})

# ">> 04:44:01" -> incoming frame from 0 to 4, opcode 0x44, operand 0x01
_TRAFFIC = re.compile(r">>\s+([0-9a-fA-F]{2})((?::[0-9a-fA-F]{2})*)")
_POWER = re.compile(r"power status:\s*(.+)$", re.I)


def _power_word(text):
    """cec-client prints: on | standby | in transition from X to Y | unknown."""
    text = text.strip().lower()
    if text == "on" or text.endswith("to on"):
        return "on"
    if text == "standby" or text.endswith("to standby"):
        return "standby"
    return "unknown"


class Cec:
    def __init__(self, osd_name="PromoTV"):
        self.osd_name = osd_name[:14]
        self.on_key = None          # callback(name)
        self.on_power = None        # callback("on" | "standby")
        self.available = False
        self.tv_power = "unknown"   # on | standby | unknown
        self.proc = None
        self._lock = threading.Lock()
        self._power_reply = threading.Event()
        self.last_power_reply = None

    def start(self):
        threading.Thread(target=self._supervise, name="cec", daemon=True).start()
        threading.Thread(target=self._poll_power, name="cec-poll", daemon=True).start()

    def _supervise(self):
        exe = shutil.which("cec-client")
        if not exe:
            log.error("cec-client not found (install cec-utils); TV control disabled")
            return
        while True:
            log.info("Starting cec-client")
            try:
                self.proc = subprocess.Popen(
                    [exe, "-t", "p", "-o", self.osd_name, "-d", "15"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                )
            except OSError as e:
                log.error("Cannot start cec-client: %s", e)
                time.sleep(30)
                continue
            for line in self.proc.stdout:
                self._parse(line.strip())
            self.available = False
            self.proc.wait()
            log.warning("cec-client exited (code %s), restarting in 10 s", self.proc.returncode)
            time.sleep(10)

    def _parse(self, line):
        if not line:
            return
        if "waiting for input" in line or "CEC client registered" in line:
            if not self.available:
                log.info("CEC ready")
            self.available = True
        m = _POWER.search(line)
        if m:
            self.last_power_reply = _power_word(m.group(1))
            if self.last_power_reply != "unknown":
                self._set_power(self.last_power_reply)
            self._power_reply.set()
            return
        m = _TRAFFIC.search(line)
        if not m:
            return
        header = int(m.group(1), 16)
        data = [int(b, 16) for b in m.group(2).split(":") if b]
        if not data:
            return
        self.available = True
        initiator = header >> 4
        opcode = data[0]
        if opcode == 0x44 and len(data) > 1:           # User Control Pressed
            name = KEYS.get(data[1])
            log.debug("CEC key 0x%02x -> %s", data[1], name)
            if name and self.on_key:
                try:
                    self.on_key(name)
                except Exception:  # noqa: BLE001
                    log.exception("key handler failed")
        elif opcode == 0x36 and initiator == 0:        # TV went to standby
            self._set_power("standby")
        elif opcode == 0x90 and initiator == 0 and len(data) > 1:  # Report Power Status
            self._set_power("on" if data[1] in (0x00, 0x02) else "standby")
        elif initiator == 0 and opcode in (0x80, 0x86, 0x85):     # routing / active source requests
            self._set_power("on")

    def _set_power(self, value):
        if value != self.tv_power:
            log.info("TV power: %s", value)
            self.tv_power = value
            if self.on_power:
                self.on_power(value)

    def _write(self, command):
        with self._lock:
            if not self.proc or self.proc.poll() is not None:
                log.warning("CEC not running, cannot send %r", command)
                return False
            try:
                self.proc.stdin.write(command + "\n")
                self.proc.stdin.flush()
                return True
            except OSError as e:
                log.warning("CEC write failed: %s", e)
                return False

    def _poll_power(self):
        while True:
            time.sleep(60)
            self._write("pow 0")

    # ---------- public ----------
    def query_power(self, timeout=6):
        """Ask the TV for its power status. Returns on | standby | unknown | None (no reply)."""
        self._power_reply.clear()
        if not self._write("pow 0"):
            return None
        if self._power_reply.wait(timeout):
            return self.last_power_reply
        return None

    def power_on(self):
        log.info("TV ON")
        ok = self._write("on 0")
        if ok:
            self.tv_power = "on"
            # Becoming the active source makes the TV switch to our HDMI input.
            # Some TVs ignore it while still booting, so repeat a few times.
            def later():
                for delay in (3, 6, 10):
                    time.sleep(delay)
                    self._write("as")
                self._write("pow 0")
            threading.Thread(target=later, daemon=True).start()
        return ok

    def power_off(self):
        log.info("TV OFF")
        ok = self._write("standby 0")
        if ok:
            self.tv_power = "standby"
            threading.Timer(5, lambda: self._write("pow 0")).start()
        return ok


class FakeCec(Cec):
    """Dev mode: no hardware, just logs."""

    def start(self):
        self.available = True
        self.tv_power = "on"

    def _write(self, command):
        log.info("[fake cec] %s", command)
        return True

    def power_on(self):
        self._write("on 0")
        self._set_power("on")
        return True

    def power_off(self):
        self._write("standby 0")
        self._set_power("standby")
        return True

    def query_power(self, timeout=6):
        return self.tv_power
