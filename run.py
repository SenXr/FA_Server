import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fa_server.app import create_app
from fa_server.config import AppConfig
from fa_server.services.purge_service import start_purge_service

config = AppConfig.from_env()
app = create_app(config)


if __name__ == "__main__":
    debug = True
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_purge_service(config)
    app.run(host=app.config["FA_HOST"], port=app.config["FA_PORT"], debug=debug)
