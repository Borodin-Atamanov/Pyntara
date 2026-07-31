from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("pyntara")
    logger.setLevel(level.upper())

    if not logger.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s",
                datefmt="%Y-%m-%d-%H-%M-%S",
            )
        )
        logger.addHandler(stream_handler)

    return logger
