"""Shared economic assumptions for the briefing book.

The MLB Stats API does not expose ticket prices, concessions revenue, or any
per-fan dollar figure. Where pages translate an attendance gap into a dollar
impact, they use the constants here so every page tells the same story with
the same arithmetic.

Held constant on purpose. Any change here affects every dollar figure in the
report at once.
"""

REVENUE_PER_FAN_USD = 30

REVENUE_PER_FAN_BREAKDOWN = {
    "ticket":      12,
    "concessions": 10,
    "parking":      5,
    "souvenirs":    3,
}

REVENUE_PER_FAN_NOTE = (
    "$30 per fan is a conservative composite estimate combining ticket, "
    "concessions, parking, and souvenirs. The MLB Stats API does not "
    "publish actual per-fan revenue, so this figure is an order-of-"
    "magnitude benchmark, not a forecast. It is held constant across "
    "every page so that dollar figures stay comparable."
)


def fans_to_dollars(n_fans: float) -> int:
    """Multiply a fan count by the report's per-fan revenue assumption."""
    return int(round(n_fans * REVENUE_PER_FAN_USD))


def format_dollars_short(amount: float) -> str:
    """Format a dollar amount as a compact string ($1.2M, $340K, $1,234)."""
    if abs(amount) >= 1_000_000:
        return f"${amount/1_000_000:.1f}M"
    if abs(amount) >= 10_000:
        return f"${amount/1_000:.0f}K"
    return f"${amount:,.0f}"
