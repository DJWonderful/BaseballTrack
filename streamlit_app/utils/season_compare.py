"""Reusable 'this season vs prior' sparkline + delta metric.

Usage:
    from utils.season_compare import season_delta_metric

    season_delta_metric(
        label="Attendance",
        df=monthly_avg_df,       # columns: season, month, value
        value_col="avg_att",
        format="{:,.0f}",
    )
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils.theme import POSITIVE, NEGATIVE, NEUTRAL


def season_delta_metric(
    label: str,
    df: pd.DataFrame,
    value_col: str,
    season_col: str = "season",
    month_col: str = "month",
    fmt: str = "{:,.0f}",
    help: str | None = None,
) -> None:
    """Render a metric card + sparkline comparing latest season to prior.

    df: long-form monthly data with one row per (season, month).
    """
    if df.empty or season_col not in df.columns:
        st.metric(label, "-")
        return

    seasons = sorted(df[season_col].unique())
    if len(seasons) < 2:
        latest = float(df[value_col].mean())
        st.metric(label, fmt.format(latest), help=help)
        return

    latest, prior = seasons[-1], seasons[-2]
    latest_val = df[df[season_col] == latest][value_col].mean()
    prior_val  = df[df[season_col] == prior][value_col].mean()
    delta = latest_val - prior_val
    pct   = (delta / prior_val * 100) if prior_val else 0

    # Metric card
    col_m, col_s = st.columns([1, 2])
    with col_m:
        st.metric(
            label,
            fmt.format(float(latest_val)),
            delta=f"{pct:+.1f}% vs {int(prior)}",
            help=help,
        )
    with col_s:
        # Sparkline comparing both seasons, month-on-month
        fig = go.Figure()
        for szn, color in [(prior, NEUTRAL), (latest, POSITIVE if delta >= 0 else NEGATIVE)]:
            sub = df[df[season_col] == szn].sort_values(month_col)
            fig.add_trace(go.Scatter(
                x=sub[month_col],
                y=sub[value_col],
                mode="lines+markers",
                name=str(int(szn)),
                line=dict(color=color, width=2),
                marker=dict(size=5),
                showlegend=False,
            ))
        fig.update_layout(
            height=80,
            margin=dict(t=5, b=5, l=5, r=5),
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
