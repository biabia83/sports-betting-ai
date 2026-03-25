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

# ---------------------------------------------------------------------------
# Load data from Supabase
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60)
def load_picks() -> pd.DataFrame:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return pd.DataFrame()
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    resp = sb.table("ev_picks").select("*").order("created_at", desc=True).execute()
    if not resp.data:
        return pd.DataFrame()
    return pd.DataFrame(resp.data)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
st.title("🎯 EV Betting Dashboard")

df = load_picks()

if df.empty:
    st.info("No picks in the database yet. Run `python ev_bot.py` to generate +EV plays.")
    st.stop()

# Summary metrics
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Picks", len(df))
col2.metric("Avg Edge", f"+{df['edge'].mean():.1f}%")
col3.metric("Avg Sharp Prob", f"{df['sharp_prob'].mean():.1f}%")
col4.metric("Unique Players", df["player_name"].nunique())

st.markdown("---")

# Filters
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

st.subheader(f"Picks ({len(filtered)})")
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
