"""
dashboard.py — Streamlit UI for the AI Sports Handicapping Tracker.

Usage:
  streamlit run dashboard.py
"""

import os
import json

import streamlit as st
import pandas as pd
import plotly.express as px

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PICKS_FILE = os.path.join(os.path.dirname(__file__), "daily_picks.json")
GRADED_FILE = os.path.join(os.path.dirname(__file__), "graded_history.json")

st.set_page_config(
    page_title="AI Sports Handicapping Tracker",
    page_icon="🏀",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
@st.cache_data(ttl=30)
def load_daily_picks() -> dict:
    if not os.path.exists(PICKS_FILE):
        return {}
    with open(PICKS_FILE, "r") as f:
        return json.load(f)


@st.cache_data(ttl=30)
def load_graded() -> pd.DataFrame:
    if not os.path.exists(GRADED_FILE):
        return pd.DataFrame()
    with open(GRADED_FILE, "r") as f:
        data = json.load(f)
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# Title
# ---------------------------------------------------------------------------
st.title("AI Sports Handicapping Tracker")

# ---------------------------------------------------------------------------
# Tab layout: Today's Picks | Graded History
# ---------------------------------------------------------------------------
tab_today, tab_graded = st.tabs(["Today's Picks", "Graded History"])

# ===== TAB 1: Today's Picks ==============================================
with tab_today:
    picks_data = load_daily_picks()

    if not picks_data:
        st.info("No picks yet. Run `python main.py --collect` to fetch picks.")
    else:
        st.markdown(f"**Date:** {picks_data.get('date', 'N/A')}  \n"
                    f"**Collected at:** {picks_data.get('timestamp', 'N/A')}  \n"
                    f"**Prompt:** {picks_data.get('prompt', 'N/A')}")

        st.markdown("---")

        results = picks_data.get("results", [])

        # Summary metrics
        models_with_picks = [r for r in results if r.get("picks")]
        models_without = [r for r in results if not r.get("picks")]
        total_picks = sum(len(r.get("picks", [])) for r in results)

        col1, col2, col3 = st.columns(3)
        col1.metric("Models Responding", f"{len(models_with_picks)}/{len(results)}")
        col2.metric("Total Picks", total_picks)
        col3.metric("Models Down", len(models_without))

        st.markdown("---")

        # Show each model's picks
        for result in results:
            model_name = result.get("model", "Unknown")
            picks = result.get("picks", [])

            if picks:
                st.subheader(f"✅ {model_name}")
                picks_df = pd.DataFrame(picks)
                st.dataframe(picks_df, use_container_width=True, hide_index=True)
            else:
                st.subheader(f"❌ {model_name}")
                st.caption("No picks returned (check API key / billing)")

        # Combined view
        if models_with_picks:
            st.markdown("---")
            st.subheader("All Picks Combined")
            all_rows = []
            for result in results:
                for pick in result.get("picks", []):
                    row = {**pick, "model": result.get("model", "Unknown")}
                    all_rows.append(row)
            if all_rows:
                combined_df = pd.DataFrame(all_rows)
                # Reorder so model is first
                cols = ["model"] + [c for c in combined_df.columns if c != "model"]
                st.dataframe(combined_df[cols], use_container_width=True, hide_index=True)


# ===== TAB 2: Graded History =============================================
with tab_graded:
    df = load_graded()

    if df.empty:
        st.info(
            "No graded data yet. Once picks are graded with `python tools/grader.py`, "
            "results will appear here with leaderboards and charts."
        )
    else:
        # Units calculation
        def calc_units(row):
            status = row.get("status", "")
            if status == "WIN":
                odds = row.get("odds")
                if odds and odds != 0:
                    if odds > 0:
                        return odds / 100.0
                    else:
                        return 100.0 / abs(odds)
                return 1.0
            elif status == "LOSS":
                return -1.1
            else:
                return 0.0

        df["units"] = df.apply(calc_units, axis=1)

        # Sidebar filters
        st.sidebar.title("Filters")
        all_models = sorted(df["model"].dropna().unique().tolist()) if "model" in df.columns else []
        selected_models = st.sidebar.multiselect("Model", all_models, default=all_models)

        filtered = df.copy()
        if selected_models:
            filtered = filtered[filtered["model"].isin(selected_models)]

        if filtered.empty:
            st.warning("No data matches the current filters.")
        else:
            # Leaderboard
            leaderboard = (
                filtered.groupby("model")
                .agg(
                    Wins=("status", lambda s: (s == "WIN").sum()),
                    Losses=("status", lambda s: (s == "LOSS").sum()),
                    Voids=("status", lambda s: (s.isin(["VOID", "PUSH"])).sum()),
                    Units_Won=("units", "sum"),
                )
                .reset_index()
                .rename(columns={"model": "Model", "Units_Won": "Units Won"})
            )
            leaderboard["Total"] = leaderboard["Wins"] + leaderboard["Losses"]
            leaderboard["Win Rate %"] = (
                (leaderboard["Wins"] / leaderboard["Total"].replace(0, 1)) * 100
            ).round(1)
            leaderboard["Units Won"] = leaderboard["Units Won"].round(2)
            leaderboard = leaderboard.sort_values("Units Won", ascending=False)

            # Top model
            top = leaderboard.iloc[0]
            c1, c2, c3 = st.columns(3)
            c1.metric("Top Model", top["Model"])
            c2.metric("Win Rate", f"{top['Win Rate %']}%")
            c3.metric("Units Won", f"{top['Units Won']:+.2f}")

            st.markdown("---")
            st.subheader("Model Leaderboard")
            display_cols = ["Model", "Wins", "Losses", "Win Rate %", "Units Won"]
            st.dataframe(
                leaderboard[display_cols],
                use_container_width=True,
                hide_index=True,
            )

            # Chart
            if "date" in filtered.columns and filtered["date"].notna().any():
                st.markdown("---")
                st.subheader("Cumulative Units Won Over Time")
                chart_df = (
                    filtered.sort_values("date")
                    .groupby(["date", "model"])["units"]
                    .sum()
                    .groupby(level="model")
                    .cumsum()
                    .reset_index()
                    .rename(columns={"units": "Cumulative Units"})
                )
                fig = px.line(
                    chart_df, x="date", y="Cumulative Units",
                    color="model", markers=True,
                )
                fig.update_layout(hovermode="x unified")
                st.plotly_chart(fig, use_container_width=True)

            # Raw data
            st.markdown("---")
            st.subheader("Raw Data")
            st.dataframe(filtered, use_container_width=True, hide_index=True)
