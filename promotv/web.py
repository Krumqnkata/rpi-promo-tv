"""Web panel (Flask) + captive portal redirects."""
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import unquote

from flask import Flask, Response, abort, jsonify, redirect, request, send_from_directory

from . import __version__, config, scheduler, system
from .media import date_status, media_type

log = logging.getLogger(__name__)

STATIC = os.path.join(os.path.dirname(__file__), "static")
_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
CAPTIVE_PATHS = {
    "/generate_204", "/gen_204", "/hotspot-detect.html", "/library/test/success.html",
    "/ncsi.txt", "/connecttest.txt", "/redirect", "/success.txt", "/canonical.html",
    "/mobile/status.php", "/kindle-wifi/wifistub.html",
}


def create_web(app):
    web = Flask(__name__, static_folder=STATIC, static_url_path="/static")
    web.config["MAX_CONTENT_LENGTH"] = None
    shot_lock = threading.Lock()

    def err(message, code=400):
        return jsonify({"error": message}), code

    def panel_url():
        return f"http://{config.AP_IP}/"

    # ---------------- captive portal ----------------
    @web.before_request
    def captive():
        host = (request.host or "").split(":")[0].lower()
        if config.DEV:
            return None
        known = _IPV4.match(host) or host.endswith(".local") or host in ("localhost", system.hostname().lower())
        if not known or request.path in CAPTIVE_PATHS:
            # Any foreign hostname = a phone checking for internet or opening a website.
            return redirect(panel_url(), 302)
        return None

    @web.after_request
    def no_cache(resp):
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @web.get("/")
    def index():
        return send_from_directory(STATIC, "index.html", max_age=0)

    # ---------------- status ----------------
    @web.get("/api/status")
    def status():
        st = app.state.get()
        now = datetime.fromtimestamp(system.now())
        nxt = scheduler.next_event(st["schedule"], now)
        return jsonify({
            "version": __version__,
            "player": app.player.status(),
            "tv": {"power": app.cec.tv_power, "cec": app.cec.available},
            "time": {"now": now.strftime("%Y-%m-%d %H:%M:%S"), "trusted": system.time_trusted()},
            "schedule": {
                "enabled": st["schedule"]["enabled"],
                "next": {"kind": nxt[0], "day": nxt[1].weekday(), "time": nxt[1].strftime("%H:%M"),
                         "date": nxt[1].strftime("%d.%m")} if nxt else None,
            },
            "settings": {k: st[k] for k in ("mode", "single_file", "image_duration", "volume",
                                            "fade", "fade_duration", "power_off_mode")},
            "disk": system.disk_usage(),
            "protection": system.protection_status(),
        })

    @web.get("/api/ping")
    def ping():
        return jsonify({"ok": True})

    @web.post("/api/time")
    def set_time():
        data = request.get_json(force=True, silent=True) or {}
        try:
            epoch = float(data["epoch_ms"]) / 1000.0
        except (KeyError, TypeError, ValueError):
            return err("bad time")
        if epoch < 1_700_000_000:
            return err("bad time")
        diff = app.set_clock(epoch, "phone")
        return jsonify({"ok": diff is not None, "correction": diff})

    # ---------------- files ----------------
    @web.get("/api/files")
    def files():
        return jsonify({"files": app.library.list(), "current": app.player.current()})

    @web.post("/api/upload")
    def upload():
        name = unquote(request.headers.get("X-Filename", ""))
        if not name or not media_type(name):
            return err("Неподдържан тип файл. Разрешени са видеа (mp4, mkv, mov…) и снимки (jpg, png…).")
        length = request.content_length
        if length is not None and length > system.disk_usage()["free"]:
            return err("Няма достатъчно свободно място на картата.", 507)
        tmp = os.path.join(config.TMP_DIR, uuid.uuid4().hex + ".part")
        written = 0
        try:
            with open(tmp, "wb") as f:
                stream = request.stream
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    written += len(chunk)
            if length is not None and written != length:
                raise IOError("incomplete upload")
            final, warnings = app.library.add_uploaded(tmp, name)
        except Exception as e:  # noqa: BLE001
            log.warning("Upload of %s failed: %s", name, e)
            try:
                os.remove(tmp)
            except OSError:
                pass
            return err("Качването не успя: " + str(e), 500)
        return jsonify({"ok": True, "name": final, "warnings": warnings})

    @web.delete("/api/files/<path:name>")
    def delete(name):
        if not app.library.exists(name):
            return err("Няма такъв файл", 404)
        app.library.delete(name)
        return jsonify({"ok": True})

    @web.post("/api/files/<path:name>/rename")
    def rename(name):
        new = ((request.get_json(force=True, silent=True) or {}).get("name") or "").strip()
        if not new:
            return err("Въведете ново име")
        try:
            return jsonify({"ok": True, "name": app.library.rename(name, new)})
        except FileNotFoundError:
            return err("Няма такъв файл", 404)

    @web.post("/api/files/<path:name>/play")
    def play(name):
        if not app.library.exists(name):
            return err("Няма такъв файл", 404)
        st = app.state.get()
        if st["mode"] != "single":
            item = next((i for i in st["playlist"] if i["file"] == name), None)
            if item and date_status(item) != "active":
                return err("Файлът е извън периода си на показване – променете периода.")
            if item and not item["enabled"]:
                app.library.set_playlist([i["file"] for i in st["playlist"]], {name: True})
        app.player.jump_to(name)
        return jsonify({"ok": True})

    @web.post("/api/files/<path:name>/dates")
    def dates(name):
        data = request.get_json(force=True, silent=True) or {}
        try:
            app.library.set_dates(name, data.get("start"), data.get("end"))
        except FileNotFoundError:
            return err("Няма такъв файл", 404)
        except ValueError as e:
            return err(str(e) if "дата" in str(e) else "Невалидна дата")
        return jsonify({"ok": True})

    @web.post("/api/playlist")
    def playlist():
        data = request.get_json(force=True, silent=True) or {}
        order = data.get("order")
        if not isinstance(order, list):
            return err("bad order")
        app.library.set_playlist([str(n) for n in order], dict(data.get("enabled") or {}))
        return jsonify({"ok": True})

    @web.get("/thumbs/<tid>.jpg")
    def thumb(tid):
        if not re.fullmatch(r"[0-9a-f]{16}", tid):
            abort(404)
        return send_from_directory(config.THUMB_DIR, tid + ".jpg", max_age=3600)

    @web.get("/media/<path:name>")
    def media(name):
        if not app.library.exists(name):
            abort(404)
        return send_from_directory(config.MEDIA_DIR, name, conditional=True)

    # ---------------- playback ----------------
    @web.post("/api/player")
    def player():
        action = (request.get_json(force=True, silent=True) or {}).get("action")
        p = app.player
        actions = {"pause": lambda: p.set_pause(True), "resume": lambda: p.set_pause(False),
                   "toggle": p.toggle_pause, "next": p.next, "prev": p.prev, "reload": p.reload}
        if action not in actions:
            return err("bad action")
        actions[action]()
        return jsonify({"ok": True})

    @web.post("/api/settings/playback")
    def playback():
        data = request.get_json(force=True, silent=True) or {}
        try:
            app.apply_settings(data)
        except ValueError as e:
            return err(str(e))
        return jsonify({"ok": True})

    @web.get("/api/screenshot")
    def screenshot():
        path = os.path.join(config.RUN_DIR, "screen.jpg")
        with shot_lock:
            fresh = os.path.isfile(path) and time.time() - os.path.getmtime(path) < 2
            if not fresh:
                if not app.player.output_on:
                    return err("HDMI сигналът е спрян – няма какво да се покаже.", 409)
                try:
                    os.remove(path)
                except OSError:
                    pass
                if not app.player.screenshot(path):
                    return err("Няма картина в момента.", 404)
        resp = send_from_directory(config.RUN_DIR, "screen.jpg", max_age=0)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @web.get("/api/backup")
    def backup():
        data = json.dumps(app.backup(), ensure_ascii=False, indent=2)
        stamp = datetime.fromtimestamp(system.now()).strftime("%Y-%m-%d")
        return Response(data, mimetype="application/json", headers={
            "Content-Disposition": f'attachment; filename="promotv-backup-{stamp}.json"'})

    @web.post("/api/restore")
    def restore():
        body = request.get_json(force=True, silent=True) or {}
        try:
            done = app.restore(body.get("backup"), bool(body.get("restore_wifi")))
        except ValueError as e:
            return err(str(e))
        return jsonify({"ok": True, "restored": done})

    # ---------------- schedule & TV ----------------
    @web.get("/api/schedule")
    def get_schedule():
        return jsonify(app.state.get("schedule"))

    @web.post("/api/schedule")
    def save_schedule():
        try:
            sched = scheduler.validate(request.get_json(force=True, silent=True) or {})
        except (ValueError, TypeError, AttributeError) as e:
            return err(f"Невалиден график: {e}")
        app.state.set(schedule=sched)
        app.scheduler.poke(reset=True)
        return jsonify({"ok": True})

    @web.post("/api/tv")
    def tv():
        action = (request.get_json(force=True, silent=True) or {}).get("action")
        if action == "on":
            ok = app.tv_on("web")
        elif action == "off":
            ok = app.tv_off("web")
        else:
            return err("bad action")
        if not ok:
            return err("Няма връзка с телевизора по HDMI-CEC.", 503)
        return jsonify({"ok": True})

    # ---------------- settings ----------------
    @web.get("/api/wifi")
    def get_wifi():
        return jsonify(system.get_wifi())

    @web.post("/api/wifi")
    def set_wifi():
        data = request.get_json(force=True, silent=True) or {}
        try:
            system.set_wifi(str(data.get("ssid", "")), str(data.get("password", "")))
        except ValueError as e:
            return err(str(e))
        # Refresh the idle screen text once the change is applied.
        import threading
        threading.Timer(6, app.wifi_changed).start()
        return jsonify({"ok": True})

    @web.get("/api/system")
    def sys_info():
        return jsonify({"ips": system.ip_addresses(), "hostname": system.hostname(),
                        "version": __version__, "disk": system.disk_usage()})

    @web.post("/api/system")
    def sys_action():
        action = (request.get_json(force=True, silent=True) or {}).get("action")
        if action not in ("reboot", "shutdown", "restart", "protect-on", "protect-off"):
            return err("bad action")
        system.system_action(action)
        return jsonify({"ok": True})

    @web.errorhandler(404)
    def not_found(_e):
        if request.path.startswith("/api/"):
            return err("Not found", 404)
        return redirect("/", 302)

    return web
