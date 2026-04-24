"""Delta loading helpers for the collection pipeline."""

import os
from datetime import datetime

from src.utils.logger import get_logger

logger = get_logger("delta")


def parse_seasons_env() -> list[int]:
    """Parse SEASONS env var into a list of ints."""
    return [int(s) for s in os.getenv("SEASONS", "2023,2024,2025").split(",")]


def active_seasons(seasons: list[int], force: bool = False) -> tuple[list[int], list[int]]:
    """Filter season list to only active (current-year+) seasons.

    MiLB seasons end by mid-October, so season < current_year is safely complete.

    Returns:
        (to_fetch, skipped) — seasons to collect and seasons being skipped.
    """
    if force:
        return seasons, []
    current_year = datetime.now().year
    to_fetch = [s for s in seasons if s >= current_year]
    skipped = [s for s in seasons if s < current_year]
    if skipped:
        logger.info(f"Skipping completed seasons: {skipped}")
    return to_fetch, skipped
