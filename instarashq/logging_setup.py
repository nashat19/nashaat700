"""تهيئة السجلات على الطرفية والملف."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from rich.logging import RichHandler

_FORMAT = "%(message)s"
_FILE_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> logging.Logger:
    """يهيّئ الجذر ويُرجع لوجر التطبيق."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    numeric = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(numeric)

    console = RichHandler(rich_tracebacks=True, show_path=False, markup=False)
    console.setLevel(numeric)
    console.setFormatter(logging.Formatter(_FORMAT, datefmt="%H:%M:%S"))
    root.addHandler(console)

    if log_file:
        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setLevel(numeric)
        file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
        root.addHandler(file_handler)

    logging.getLogger("instagrapi").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return logging.getLogger("instarashq")


def quiet_console() -> None:
    """يخفض ضجيج الطرفية (لأوامر التقارير)."""
    for handler in logging.getLogger().handlers:
        if isinstance(handler, RichHandler):
            handler.setLevel(logging.WARNING)


def stderr_logger(name: str = "instarashq") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FILE_FORMAT))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
