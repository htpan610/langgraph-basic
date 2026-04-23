from __future__ import annotations

from loguru import logger

from core.config import Settings


def configure_logging(settings: Settings) -> None:
    settings.app.log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(settings.app.log_dir / "app.log", rotation="10 MB", retention="14 days", encoding="utf-8")
