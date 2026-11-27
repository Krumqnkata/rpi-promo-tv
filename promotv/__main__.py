import logging
import os
import socket
import threading
import time
import urllib.request

from . import config

log = logging.getLogger("promotv")


def sd_notify(message):
    """Tell systemd we are alive (Type=notify + WatchdogSec in the unit). No-op elsewhere."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr or not hasattr(socket, "AF_UNIX"):
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(message.encode(), addr)
    except OSError as e:
        log.warning("sd_notify failed: %s", e)


def watchdog():
    """Ping systemd only while the web server answers. If it hangs, systemd restarts us."""
    url = f"http://127.0.0.1:{config.HTTP_PORT}/api/ping"
    ready = False
    while True:
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                ok = r.status == 200
        except OSError:
            ok = False
        if ok:
            if not ready:
                sd_notify("READY=1")
                ready = True
            sd_notify("WATCHDOG=1")
        time.sleep(15)


def main():
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("PROMOTV_DEBUG") else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    from .app import App
    from .web import create_web

    app = App()
    app.start()
    web = create_web(app)
    threading.Thread(target=watchdog, name="watchdog", daemon=True).start()
    log.info("Web panel on port %s", config.HTTP_PORT)
    web.run(host="0.0.0.0", port=config.HTTP_PORT, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
