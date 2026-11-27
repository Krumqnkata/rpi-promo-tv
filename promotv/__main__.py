import logging
import os

from . import config


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
    logging.getLogger(__name__).info("Web panel on port %s", config.HTTP_PORT)
    web.run(host="0.0.0.0", port=config.HTTP_PORT, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
