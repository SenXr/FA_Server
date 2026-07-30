import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fa_server.logging_config import configure_logging

LOG_PATH = configure_logging()
logging.getLogger(__name__).info("FA Server log initialized: %s", LOG_PATH)

from fa_server.app import create_app
from fa_server.config import AppConfig
from fa_server.services.purge_service import start_purge_service

config = AppConfig.from_env()
app = create_app(config)


if __name__ == "__main__":
    debug = os.getenv("FA_DEBUG", "false").lower() in {"1", "true", "yes", "on"}
    start_purge_service(config)
    app.run(
        host=app.config["FA_HOST"],
        port=app.config["FA_PORT"],
        debug=debug,
        use_reloader=False,
    )
