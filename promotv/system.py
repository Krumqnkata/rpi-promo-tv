"""System helpers: clock, Wi-Fi access point (NetworkManager), reboot."""
import logging
import os
import shutil
import socket
import subprocess
import threading
import time

from . import config

log = logging.getLogger(__name__)

_SYNC_MARK = os.path.join(config.RUN_DIR, "time_synced")
_dev_offset = 0.0  # dev mode: pretend clock offset instead of changing the PC clock


def now():
    return time.time() + _dev_offset


def _run(args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


# ---------------- clock ----------------
def time_trusted():
    """True when the clock was set from a phone/TV menu since boot, or NTP synced."""
    if os.path.exists(_SYNC_MARK):
        return True
    if config.DEV or not shutil.which("timedatectl"):
        return False
    try:
        out = _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"]).stdout.strip()
        return out == "yes"
    except (subprocess.SubprocessError, OSError):
        return False


def set_time(epoch, source="phone"):
    """Set the system clock (UTC epoch seconds). Returns the correction in seconds."""
    global _dev_offset
    diff = epoch - now()
    if abs(diff) > 2:
        log.info("Setting clock from %s (correction %+.1f s)", source, diff)
        if config.DEV:
            _dev_offset += diff
        else:
            r = _run(["date", "-u", "-s", f"@{epoch:.3f}"])
            if r.returncode != 0:
                log.error("date failed: %s", r.stderr.strip())
                return None
            if shutil.which("fake-hwclock"):
                _run(["fake-hwclock", "save", "force"])
    try:
        with open(_SYNC_MARK, "w") as f:
            f.write(source)
    except OSError:
        pass
    return diff


# ---------------- network ----------------
def ip_addresses():
    ips = set()
    if config.DEV:
        return ["127.0.0.1"]
    try:
        out = _run(["hostname", "-I"]).stdout.split()
        ips.update(i for i in out if ":" not in i)
    except (subprocess.SubprocessError, OSError):
        pass
    return sorted(ips) or [config.AP_IP]


def hostname():
    return socket.gethostname()


_dev_wifi = {"ssid": "PromoTV", "password": "promo12345"}


def get_wifi():
    if config.DEV:
        return dict(_dev_wifi)
    try:
        r = _run(["nmcli", "-s", "-g", "802-11-wireless.ssid,802-11-wireless-security.psk",
                  "connection", "show", config.AP_CONNECTION])
        lines = r.stdout.splitlines()
        if r.returncode == 0 and lines:
            return {"ssid": lines[0], "password": lines[1] if len(lines) > 1 else ""}
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("nmcli failed: %s", e)
    return {"ssid": "?", "password": "?"}


def set_wifi(ssid, password):
    """Change the access point name/password. Applied after a short delay so the
    HTTP response can still reach the phone before the network restarts."""
    ssid = ssid.strip()
    if not (1 <= len(ssid.encode("utf-8")) <= 32):
        raise ValueError("Името на мрежата трябва да е между 1 и 32 символа.")
    if not (8 <= len(password) <= 63) or not password.isascii():
        raise ValueError("Паролата трябва да е между 8 и 63 символа (латиница, цифри, знаци).")

    def apply():
        time.sleep(2)
        if config.DEV:
            _dev_wifi.update(ssid=ssid, password=password)
            return
        _run(["nmcli", "connection", "modify", config.AP_CONNECTION,
              "802-11-wireless.ssid", ssid, "802-11-wireless-security.psk", password])
        _run(["nmcli", "connection", "up", config.AP_CONNECTION], timeout=60)
    threading.Thread(target=apply, daemon=True).start()


# ---------------- power ----------------
def system_action(action):
    if config.DEV:
        log.info("[dev] system action %s", action)
        return

    def run():
        time.sleep(1.5)
        if action == "reboot":
            _run(["systemctl", "reboot"])
        elif action == "shutdown":
            _run(["systemctl", "poweroff"])
        elif action == "restart":
            _run(["systemctl", "restart", "promotv"])
    threading.Thread(target=run, daemon=True).start()


def disk_usage():
    u = shutil.disk_usage(config.MEDIA_DIR)
    return {"total": u.total, "free": max(0, u.free - config.RESERVED_SPACE), "used": u.used}
