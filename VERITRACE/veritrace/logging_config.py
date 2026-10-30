"""Console and rotating file logging for API processes and reload workers."""

from pathlib import Path


def build_log_config(log_file=None, level="INFO"):
    level = level.upper()
    handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
            "formatter": "standard",
        }
    }
    if log_file:
        path = Path(log_file).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(path),
            "encoding": "utf-8",
            "errors": "backslashreplace",
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 3,
            "formatter": "standard",
        }
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s",
            }
        },
        "handlers": handlers,
        "root": {"level": level, "handlers": list(handlers)},
        "loggers": {
            name: {"level": level, "handlers": [], "propagate": True}
            for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
        },
    }
