"""
logging_config/logger.py
========================
Structured logging for all system events.
Every module imports `get_logger` from here.
"""

import logging
import logging.handlers
import re
import sys
from pathlib import Path

import structlog
from config import settings

# RPC URLs carry the Helius api-key, Telegram URLs carry the bot token
_SECRET_PATTERNS = (
    (re.compile(r"(api[-_]key=)[^&\s\"']+", re.IGNORECASE), r"\1<redacted>"),
    (re.compile(r"/bot\d+:[A-Za-z0-9_-]+"), "/bot<redacted>"),
)


class _RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = message
        for pattern, replacement in _SECRET_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


def _setup_stdlib_logging() -> None:
    """Configure stdlib logging so structlog can wrap it."""
    log_dir = Path(settings.LOG_FILE).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    # Rotating file handler — 50 MB per file, 10 backups
    file_handler = logging.handlers.RotatingFileHandler(
        settings.LOG_FILE,
        maxBytes=50 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    handlers.append(file_handler)
    for handler in handlers:
        handler.addFilter(_RedactSecrets())

    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
        handlers=handlers,
        force=True,
    )
    # httpx (and solana>=0.41's httpx2 fork) log every request URL at INFO
    for name in ("httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.WARNING)


def configure_logging() -> None:
    """Call once at application startup."""
    _setup_stdlib_logging()

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger for the given module name."""
    return structlog.get_logger(name)
