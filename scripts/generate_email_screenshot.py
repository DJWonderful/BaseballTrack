"""Generate the headline chart as a standalone PNG for the email to Molly.

Mirrors the Saturdays page Fri-vs-Sat bar chart but renders via matplotlib so
the email can carry the punchline without requiring her to click anything.

Output: docs/email_chart_fri_vs_sat.png

Run from repo root:
    python scripts/generate_email_screenshot.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Add streamlit_app to path so we can reuse the existing DB helper.
sys.path.insert(0, str(Path(__file__).parent.parent / "streamlit_app"))
os.environ.setdefault("APP_BACKEND", "postgres")

from utils.db import query_df  # noqa: E402

RUMBLE_PONIES_ID = 505
FRI_DOW, SAT_DOW = 4, 5

OUT_PATH = Path(__file__).parent.parent / "docs" / "email_chart_fri_vs_sat.png"


def load() -> pd.DataFrame:
    return query_df(f"""
        SELECT season, day_of_week,
               COUNT(*) AS n_games,
               AVG(attendance) AS avg_att
          FROM milb.game_features
         WHERE team_id = {RUMBLE_PONIES_ID}
           AND day_of_week IN ({FRI_DOW}, {SAT_DOW})
           AND game_type = 'R'
           AND attendance IS NOT NULL
           AND NOT (EXTRACT(MONTH FROM game_date) = 7
                    AND EXTRACT(DAY FROM game_date) IN (3, 4, 5))
         GROUP BY season, day_of_week
         ORDER BY season, day_of_week
    """)


def main() -> None:
    df = load()
    df["day"] = df["day_of_week"].map({FRI_DOW: "Friday", SAT_DOW: "Saturday"})
    wide = df.pivot_table(index="season", columns="day", values="avg_att").reset_index()
    wide = wide.sort_values("season")

    seasons = wide["season"].astype(str).tolist()
    fri = wide["Friday"].fillna(0).to_numpy()
    sat = wide["Saturday"].fillna(0).to_numpy()

    width = 0.36
    x = np.arange(len(seasons))

    fig, ax = plt.subplots(figsize=(9, 5), dpi=170)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    bars_fri = ax.bar(x - width / 2, fri, width, label="Friday",   color="#3a9bd5")
    bars_sat = ax.bar(x + width / 2, sat, width, label="Saturday", color="#b064a0")

    # Bar value labels
    for bars, vals in [(bars_fri, fri), (bars_sat, sat)]:
        for bar, v in zip(bars, vals):
            if v > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, v + 60,
                        f"{int(round(v)):,}",
                        ha="center", va="bottom", fontsize=9, color="#333333")

    ax.set_title("Binghamton Rumble Ponies — Friday vs Saturday home attendance",
                 fontsize=13, fontweight="bold", pad=14, color="#111111")
    ax.set_ylabel("Average attendance", fontsize=10, color="#444444")
    ax.set_xticks(x)
    ax.set_xticklabels(seasons, fontsize=10)
    ax.tick_params(axis="y", labelsize=9, colors="#555555")
    ax.tick_params(axis="x", colors="#222222")

    ymax = max(fri.max(), sat.max()) * 1.18
    ax.set_ylim(0, ymax)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{int(v):,}"))

    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#cccccc")

    ax.grid(axis="y", color="#eeeeee", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)

    legend = ax.legend(loc="upper right", frameon=False, fontsize=10)
    for txt in legend.get_texts():
        txt.set_color("#222222")

    sub = ("Friday outdraws Saturday every season since 2023. "
           "Note 2026 is mid-season (through June). July 3-5 holiday window excluded.")
    fig.text(0.5, 0.02, sub, ha="center", va="bottom",
             fontsize=8.5, color="#666666", wrap=True)

    plt.tight_layout(rect=(0, 0.04, 1, 1))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PATH, dpi=170, bbox_inches="tight", facecolor="white")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
