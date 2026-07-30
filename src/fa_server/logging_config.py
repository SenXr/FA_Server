from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fa_server.config import PROJECT_ROOT


DEFAULT_LOG_PATH = PROJECT_ROOT / "logs" / "fa_server.log"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5
_MANAGED_HANDLER_MARKER = "_fa_server_managed_handler"


def configure_logging(
    log_path: Path = DEFAULT_LOG_PATH,
    *,
    level: int = logging.INFO,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> Path:
    resolved_path = log_path.expanduser()
    if not resolved_path.is_absolute():
        resolved_path = PROJECT_ROOT / resolved_path
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        resolved_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        if getattr(handler, _MANAGED_HANDLER_MARKER, False):
            root_logger.removeHandler(handler)
            handler.close()

    setattr(console_handler, _MANAGED_HANDLER_MARKER, True)
    setattr(file_handler, _MANAGED_HANDLER_MARKER, True)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.setLevel(level)

    logging.captureWarnings(True)
    logging.getLogger("werkzeug").setLevel(level)
    return resolved_path
