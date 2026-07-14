from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable


class _CallbackHandler(logging.Handler):
    def __init__(self, callback: Callable[[str], None]):
        super().__init__()
        self.callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        self.callback(self.format(record))


class LoggerManager:
    @staticmethod
    def setup_logger(
        name: str,
        config: dict[str, Any] | None = None,
        *,
        log_callback: Callable[[str], None] | None = None,
        log_file_name: str = "fa_processor",
        format_type: str = "default",
    ) -> logging.Logger:
        del config, format_type
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.propagate = False

        if logger.handlers:
            return logger

        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        log_dir = Path("log")
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(
                log_dir / f"{log_file_name}.log",
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError:
            pass

        if log_callback is not None:
            callback_handler = _CallbackHandler(log_callback)
            callback_handler.setFormatter(formatter)
            logger.addHandler(callback_handler)

        return logger
