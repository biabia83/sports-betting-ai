"""
dashboard.py — Streamlit dashboard for the EV Betting Bot.

Usage:
  streamlit run dashboard.py
"""

import os

import streamlit as st
import pandas as pd
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

st.set_page_config(
    page_title="EV Betting Dashboard",
    page_icon="🎯",
    layout="wide",
)

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("SUPABASE_URL and SUPABASE_KEY must be set in .env")
    st.stop()

sb = create_client(SUPABASE_URL, SUPABASE_KEY)


# ---------------------------------------------------------------------------
# Load data from Supabase
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60)
def load_picks() -> pd.DataFrame:
    resp = sb.table("ev_picks").select("*").order("created_at", desc=True).execute()
    if not resp.data:
        return pd.DataFrame()
    return pd.DataFrame(resp.data)


def grade_pick(pick_id: int, result: str):
    """Update a pick's result in Supabase."""
    sb.table("ev_picks").update({"result": result}).eq("id", pick_id).execute()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
st.title("🎯 EV Betting Dashboard")

df = load_picks()

if df.empty:
    st.info("No picks in the database yet. Run `python ev_bot.py` to generate +EV plays.")
    st.stop()

# ---------------------------------------------------------------------------
# Summary metrics (including Win Percentage)
# ---------------------------------------------------------------------------
graded = df[df["result"].isin(["Win", "Loss"])]
wins = (graded["result"] == "Win").sum()
losses = (graded["result"] == "Loss").sum()
total_graded = wins + losses
win_pct = (wins / total_graded * 100) if total_graded > 0 else 0.0

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Total Picks", len(df))
col2.metric("Win %", f"{win_pct:.1f}%" if total_graded > 0 else "N/A")
col3.metric("Record", f"{wins}W - {losses}L")
col4.metric("Avg Edge", f"+{df['edge'].mean():.1f}%")
col5.metric("Avg Sharp Prob", f"{df['sharp_prob'].mean():.1f}%")

st.markdown("---")

# ---------------------------------------------------------------------------
# Manual grading section — picks where result IS NULL
# ---------------------------------------------------------------------------
ungraded = df[df["result"].isna() | (df["result"] == "")]

st.subheader(f"Ungraded Picks ({len(ungraded)})")

if ungraded.empty:
    st.success("All picks have been graded!")
else:
    for _, pick in ungraded.iterrows():
        pick_id = pick["id"]
        player = pick.get("player_name", "?")
        stat = pick.get("stat_type", "?")
        line = pick.get("line", "?")
        direction = pick.get("direction", "Over")
        edge = pick.get("edge", 0)
        team = pick.get("team", "")
        created = str(pick.get("created_at", ""))[:10]

        with st.container():
            cols = st.columns([4, 1, 1, 1])
            cols[0].markdown(
                f"**{player}** ({team}) — {stat} {direction} {line} "
                f"| Edge: +{edge:.1f}% | {created}"
            )
            if cols[1].button("Win", key=f"win_{pick_id}"):
                grade_pick(pick_id, "Win")
                st.cache_data.clear()
                st.rerun()
            if cols[2].button("Loss", key=f"loss_{pick_id}"):
                grade_pick(pick_id, "Loss")
                st.cache_data.clear()
                st.rerun()
            if cols[3].button("Push", key=f"push_{pick_id}"):
                grade_pick(pick_id, "Push")
                st.cache_data.clear()
                st.rerun()

st.markdown("---")

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Filters")

    teams = sorted(df["team"].dropna().unique().tolist())
    selected_teams = st.multiselect("Team", teams, default=teams)

    stat_types = sorted(df["stat_type"].dropna().unique().tolist())
    selected_stats = st.multiselect("Stat Type", stat_types, default=stat_types)

    min_edge = st.slider("Min Edge %", 0.0, float(df["edge"].max()), 0.0, step=0.5)

filtered = df.copy()
if selected_teams:
    filtered = filtered[filtered["team"].isin(selected_teams)]
if selected_stats:
    filtered = filtered[filtered["stat_type"].isin(selected_stats)]
filtered = filtered[filtered["edge"] >= min_edge]

st.subheader(f"All Picks ({len(filtered)})")
st.dataframe(
    filtered,
    use_container_width=True,
    hide_index=True,
    column_config={
        "sharp_prob": st.column_config.NumberColumn("Sharp Prob %", format="%.1f"),
        "edge": st.column_config.NumberColumn("Edge %", format="+%.1f"),
        "line": st.column_config.NumberColumn("Line", format="%.1f"),
    },
)
