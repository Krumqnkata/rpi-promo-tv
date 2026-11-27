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
    save_clock()
    return diff


def save_clock():
    """Remember the current time on the data partition. With the read-only system
    fake-hwclock cannot save it, so after a power cut we restore at least this."""
    try:
        tmp = config.CLOCK_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write(f"{now():.0f}\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, config.CLOCK_FILE)
    except OSError as e:
        log.warning("Cannot save clock: %s", e)


def restore_clock():
    """At start-up: if the clock is behind the last saved time, move it forward."""
    global _dev_offset
    try:
        with open(config.CLOCK_FILE) as f:
            saved = float(f.read().strip())
    except (OSError, ValueError):
        return
    if saved > now() + 60:
        log.info("Clock is behind the last saved time, restoring %+.0f s", saved - now())
        if config.DEV:
            _dev_offset += saved - now()
        else:
            _run(["date", "-u", "-s", f"@{saved:.0f}"])


def clock_saver():
    while True:
        time.sleep(600)
        save_clock()


# ---------------- HDMI signal ----------------
_FB_BLANK = "/sys/class/graphics/fb0/blank"


def hdmi_power(on):
    """Switch the HDMI signal on/off through the kernel console (mpv must not be running)."""
    if config.DEV:
        log.info("[dev] HDMI signal %s", "on" if on else "off")
        return True
    try:
        with open(_FB_BLANK, "w") as f:
            f.write("0" if on else "4")   # 4 = FB_BLANK_POWERDOWN
        return True
    except OSError as e:
        log.warning("Cannot switch HDMI %s: %s", "on" if on else "off", e)
        return False


# ---------------- SD card protection (read-only root) ----------------
def _boot_cmdline():
    for p in ("/boot/firmware/cmdline.txt", "/boot/cmdline.txt"):
        try:
            with open(p) as f:
                return f.read()
        except OSError:
            continue
    return ""


def protection_status():
    if config.DEV:
        return {"active": False, "next_boot": False, "data_persistent": True, "supported": False}
    try:
        with open("/proc/mounts") as f:
            root_overlay = any(line.split()[1] == "/" and line.split()[2] == "overlay"
                               for line in f if len(line.split()) > 2)
    except OSError:
        root_overlay = False
    return {
        "active": root_overlay,
        "next_boot": "overlayroot=" in _boot_cmdline(),
        # With the overlay active the data folder must be a separate (bind) mount,
        # otherwise uploads would only live in RAM.
        "data_persistent": (not root_overlay) or os.path.ismount(config.DATA_DIR),
        "supported": shutil.which("raspi-config") is not None,
    }


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
        elif action in ("protect-on", "protect-off"):
            # raspi-config rebuilds the initramfs, which takes a while on a Pi 3.
            r = _run(["raspi-config", "nonint", "do_overlayfs", "0" if action == "protect-on" else "1"],
                     timeout=600)
            log.info("raspi-config do_overlayfs: %s %s", r.returncode, r.stdout.strip()[-300:])
            if action == "protect-off":
                _run(["raspi-config", "nonint", "disable_bootro"])  # unlock the boot partition too
            _run(["systemctl", "reboot"])
    threading.Thread(target=run, daemon=True).start()


def disk_usage():
    u = shutil.disk_usage(config.MEDIA_DIR)
    return {"total": u.total, "free": max(0, u.free - config.RESERVED_SPACE), "used": u.used}
