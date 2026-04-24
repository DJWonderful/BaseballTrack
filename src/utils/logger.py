"""Logging setup for the MiLB data pipeline."""

import logging
import sys


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s")
        )
        logger.addHandler(handler)
    return logger
