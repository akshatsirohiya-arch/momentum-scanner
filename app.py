"""
INSTITUTIONAL MOMENTUM COMMAND — v4
=====================================
Upgrades over v3:
  - No CSV dependency — reads from nightly_scan.py output
  - Basing Watch tab — pre-multibagger watchlist (fund_score ≥ 7, BASING structure)
  - Speculative tab  — $100M–$500M high-momentum plays
  - App is instant — all heavy computation done nightly, not at runtime
  - Scan metadata panel (when last run, universe stats)
  - Upgraded AI prompt flags catalyst opportunities regardless of trend score
"""

import streamlit as st
import pandas as pd
import json
import os
import requests
from datetime import datetime

# ─────────────────────────────────────────────
# 1. CONFIG
# ─────────────────────────────────────────────
st.set_page_config(layout="wide", page_title="Momentum Command v4", page_icon="🏹")

RISK_PROFILE  = "Aggressive growth — position trades of 2–8 weeks, targeting 15–40% moves."
STRATEGY      = "Higher highs + higher lows trend structure required. Fundamental quality matters. AI/tech tailwinds preferred."
TODAY         = datetime.today().strftime("%B %d, %Y")
CLAUDE_MODEL  = "claude-sonnet-4-6"   # Claude Sonnet 4.6 — correct API model string

# ── GitHub raw URL base ───────────────────────
GITHUB_USER   = "akshatsirohiya-arch"
GITHUB_REPO   = "momentum-scanner"
GITHUB_BRANCH = "main"
RAW_BASE      = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/data"

FILES = {
    "main": f"{RAW_BASE}/enriched_watchlist.csv",
    "base": f"{RAW_BASE}/basing_watchlist.csv",
    "spec": f"{RAW_BASE}/speculative_watchlist.csv",
    "meta": f"{RAW_BASE}/scan_meta.json",
}


# ─────────────────────────────────────────────
# 2. CLAUDE CLIENT
# ─────────────────────────────────────────────
def call_ai(prompt: str) -> str:
    """Calls Claude API directly via HTTP — no SDK needed."""
    api_key = st.secrets.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "⚠️ ANTHROPIC_API_KEY not found in Streamlit secrets."
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      CLAUDE_MODEL,
                "max_tokens": 1024,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        # Show full error body — critical for debugging
        if not resp.ok:
            return f"⚠️ Claude API {resp.status_code}: {resp.text}"
        return resp.json()["content"][0]["text"]
    except requests.exceptions.Timeout:
        return "⚠️ Request timed out. Try again."
    except Exception as e:
        return f"⚠️ Claude API error: {str(e)}"


# ─────────────────────────────────────────────
# 3. DATA LOADERS — reads from GitHub raw URLs
# ─────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def load_csv(url: str) -> pd.DataFrame:
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 404:
            return pd.DataFrame()
        resp.raise_for_status()
        from io import StringIO
        df = pd.read_csv(StringIO(resp.text))
        if "Ticker" in df.columns:
            df["Chart"] = df["Ticker"].apply(lambda x: f"https://www.tradingview.com/symbols/{x}/")
        return df
    except Exception as e:
        st.warning(f"Could not load data: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def load_meta() -> dict:
    try:
        resp = requests.get(FILES["meta"], timeout=15)
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {}


def staleness_warning(meta: dict):
    if not meta:
        st.warning("⚠️ No scan metadata found. Run `nightly_scan.py` to generate data.")
        return
    scan_date = meta.get("scan_date", "unknown")
    scan_time = meta.get("scan_time", "")
    if scan_date != TODAY:
        st.warning(f"⚠️ Data is from **{scan_date}** — today is {TODAY}. Nightly scan may not have run yet.")
    else:
        st.success(f"✅ Data is fresh — scanned today at {scan_time}")


# ─────────────────────────────────────────────
# 4. AI PROMPTS
# ─────────────────────────────────────────────
def build_analysis_prompt(df_top: pd.DataFrame, macro_context: str = "", mode: str = "main") -> str:
    cols = [c for c in [
        "Ticker", "sector", "industry", "Velocity %", "RVOL",
        "trend_structure", "trend_score", "fund_score", "composite",
        "rev_growth_pct", "profit_margin_pct", "trailing_pe", "pct_from_6m_high"
    ] if c in df_top.columns]

    stocks_json = df_top[cols].head(10).to_json(orient="records", indent=2)

    catalyst_instruction = """
5. CATALYST SCAN — CRITICAL FOR MULTIBAGGERS:
   Regardless of current trend score, flag any stock on this list that may have:
   - An earnings inflection coming in the next 30-60 days
   - A binary event (FDA, contract, product launch, partnership)
   - A sector rotation catalyst (policy change, commodity move, rate shift)
   If you identify such a stock, flag it as ⚡ CATALYST ALERT even if its composite score is average.
"""

    basing_instruction = """
NOTE: These stocks are in BASING phase — they have NOT yet broken out.
Your job is to identify which ones are most likely to be the next breakout.
Focus on: strength of fundamentals, quality of the base (tight consolidation vs sloppy),
sector tailwinds, and any upcoming catalyst that could trigger the breakout.
Flag your top 3 as: 🔭 WATCHLIST — and give a specific trigger condition to BUY.
""" if mode == "basing" else ""

    spec_instruction = """
NOTE: These are speculative small-cap plays ($100M–$500M market cap).
Size positions at 25-50% of your normal size. Higher risk, higher reward.
Flag any that have institutional accumulation signals (high RVOL + uptrend).
""" if mode == "spec" else ""

    return f"""
You are an elite institutional equity analyst. Today is {TODAY}.

TRADER PROFILE:
- Risk: {RISK_PROFILE}
- Strategy: {STRATEGY}

MACRO CONTEXT:
{macro_context if macro_context else "US markets in AI-driven bull phase. Rate environment stable. Monitor tariff risks."}

STOCK DATA:
{stocks_json}

{basing_instruction}{spec_instruction}

YOUR TASK:
1. Pick TOP 3 stocks for a position trade (2-8 weeks).
2. For each provide:
   - WHY NOW: Specific catalyst or setup (not generic)
   - ENTRY: Precise entry zone or condition
   - TARGET: Price target with % upside and timeframe
   - STOP: Stop-loss level (invalidation point)
   - RISK: Single biggest risk
   - CONVICTION: High / Medium / Low + one-line reason

3. PORTFOLIO NOTE: Sector concentration or macro risk across the 3 picks.

4. AVOID LIST: Stocks to skip and why (1 line each).

{catalyst_instruction}

Format in clean markdown. Be specific and actionable.
"""


def build_pulse_prompt() -> str:
    return f"""
You are a macro strategist advising an aggressive US equity position trader. Today is {TODAY}.

## MARKET STANCE
FULLY INVESTED | CAUTIOUS | CASH — with 2-3 sentence justification.

## KEY MACRO FACTORS
- Fed & Rates
- Dollar  
- Volatility (VIX)
- Earnings cycle

## SECTOR ROTATION
- OWN NOW: Top 2 sectors and why
- REDUCE: 1-2 sectors losing momentum
- WATCH: 1 emerging theme

## AI TRADE HEALTH
Rate: 🔥 HOT / ⚡ ACTIVE / ❄️ COOLING

## THIS WEEK
2-3 specific events, data releases, or earnings to watch.

Be direct and opinionated. No hedging.
"""


# ─────────────────────────────────────────────
# 5. SHARED TABLE RENDERER
# ─────────────────────────────────────────────
def render_stock_table(df: pd.DataFrame):
    display_cols = [c for c in [
        "Ticker", "sector", "trend_structure", "composite",
        "trend_score", "momentum_score", "rvol_score", "fund_score",
        "Velocity %", "RVOL", "current_price", "pct_from_6m_high",
        "rev_growth_pct", "profit_margin_pct", "trailing_pe",
        "market_cap", "Chart"
    ] if c in df.columns]

    st.dataframe(
        df[display_cols],
        use_container_width=True,
        column_config={
            "Chart":             st.column_config.LinkColumn("Chart", display_text="📈 View"),
            "composite":         st.column_config.ProgressColumn("Composite", min_value=0, max_value=10, format="%.1f"),
            "trend_score":       st.column_config.NumberColumn("Trend",    format="%.1f"),
            "momentum_score":    st.column_config.NumberColumn("Mom",      format="%.1f"),
            "rvol_score":        st.column_config.NumberColumn("RVOL Sc",  format="%.1f"),
            "fund_score":        st.column_config.NumberColumn("Fund",     format="%.1f"),
            "current_price":     st.column_config.NumberColumn("Price",    format="$%.2f"),
            "Velocity %":        st.column_config.NumberColumn("Vel %",    format="%.0f%%"),
            "pct_from_6m_high":  st.column_config.NumberColumn("6m High%", format="%.1f%%"),
            "rev_growth_pct":    st.column_config.NumberColumn("Rev Grw",  format="%.0f%%"),
            "profit_margin_pct": st.column_config.NumberColumn("Margin",   format="%.1f%%"),
            "trailing_pe":       st.column_config.NumberColumn("P/E",      format="%.1f"),
            "RVOL":              st.column_config.NumberColumn("RVOL",     format="%.2fx"),
            "market_cap":        st.column_config.TextColumn("Mkt Cap"),
        },
        hide_index=False,
    )


def render_ai_section(df: pd.DataFrame, session_key: str, mode: str, macro_override: str):
    st.markdown("---")
    st.subheader("🤖 AI Deep Dive")

    if st.button(f"🧠 Run AI Analysis", key=f"ai_btn_{session_key}", type="primary"):
        with st.spinner("AI analyzing..."):
            prompt = build_analysis_prompt(df, macro_override, mode=mode)
            report = call_ai(prompt)
            st.session_state[f"ai_{session_key}"]      = report
            st.session_state[f"ai_{session_key}_time"] = datetime.now().strftime("%b %d, %Y at %H:%M")

    if st.session_state.get(f"ai_{session_key}"):
        st.caption(f"Generated: {st.session_state[f'ai_{session_key}_time']}")
        st.markdown(st.session_state[f"ai_{session_key}"])
        st.download_button(
            label="📥 Download Report",
            data=f"Generated: {st.session_state[f'ai_{session_key}_time']}\n\n{st.session_state[f'ai_{session_key}']}",
            file_name=f"Report_{session_key}_{datetime.now().strftime('%Y%m%d_%H%M')}.txt",
            mime="text/plain",
            key=f"dl_{session_key}",
        )


# ─────────────────────────────────────────────
# 6. MAIN APP
# ─────────────────────────────────────────────
meta = load_meta()

st.title("🏹 Institutional Momentum Command v4")
st.caption(f"Today: {TODAY}  |  Full market scan  |  No CSV required  |  Nightly auto-refresh")

# Scan metadata banner
if meta:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Universe Scanned",  f"{meta.get('universe_size', '?'):,}")
    c2.metric("Valid Tickers",     f"{meta.get('valid_tickers', '?'):,}")
    c3.metric("Main Uptrend",      meta.get("main_count", "?"))
    c4.metric("Basing Watch",      meta.get("basing_count", "?"))
    c5.metric("Speculative",       meta.get("spec_count", "?"))

staleness_warning(meta)

# ── Sidebar ──────────────────────────────────
st.sidebar.header("⚙️ Filters")
min_composite  = st.sidebar.slider("Min Composite Score", 1.0, 10.0, 5.0, 0.5)
min_fund_score = st.sidebar.slider("Min Fundamental Score", 1.0, 10.0, 4.0, 0.5)
sector_filter  = st.sidebar.text_input("Sector filter (partial match)", placeholder="Tech, Health, Energy...")
macro_override = st.sidebar.text_area(
    "Macro Context Override",
    placeholder="e.g. Fed paused, tariff risks elevated, AI capex still growing..."
)

st.sidebar.markdown("---")
st.sidebar.markdown("**Scoring Weights**")
st.sidebar.markdown("Trend Structure: **35%**")
st.sidebar.markdown("Momentum (Vel%): **25%**")
st.sidebar.markdown("RVOL:            **20%**")
st.sidebar.markdown("Fundamentals:    **20%**")
st.sidebar.markdown("---")
st.sidebar.markdown("**Data Pipeline**")
st.sidebar.markdown("Scanner: `nightly_scan.py`")
st.sidebar.markdown("Schedule: Weekdays 6pm ET")
st.sidebar.markdown("Via: GitHub Actions (free)")

# ── Tabs ─────────────────────────────────────
tab_main, tab_base, tab_spec, tab_pulse = st.tabs([
    "📈 Uptrend Stocks",
    "🔭 Basing Watch",
    "⚡ Speculative",
    "🌐 Market Pulse",
])


def fmt_mktcap(val) -> str:
    """Convert raw market cap number to readable string e.g. $4.2B, $820M"""
    try:
        v = float(val)
        if v >= 1e12: return f"${v/1e12:.1f}T"
        if v >= 1e9:  return f"${v/1e9:.1f}B"
        if v >= 1e6:  return f"${v/1e6:.0f}M"
        return f"${v:,.0f}"
    except:
        return "N/A"

def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    # Filter
    if "composite" in df.columns:
        df = df[pd.to_numeric(df["composite"], errors="coerce") >= min_composite]
    if "fund_score" in df.columns:
        df = df[pd.to_numeric(df["fund_score"], errors="coerce") >= min_fund_score]
    if sector_filter.strip() and "sector" in df.columns:
        df = df[df["sector"].str.contains(sector_filter.strip(), case=False, na=False)]
    # Sort best to worst by composite score
    if "composite" in df.columns:
        df = df.sort_values("composite", ascending=False)
    # Format market cap as human-readable
    if "market_cap" in df.columns:
        df["market_cap"] = df["market_cap"].apply(fmt_mktcap)
    # Reset rank index starting at 1
    df = df.reset_index(drop=True)
    df.index += 1
    return df


# ════════════════════════════════════════════
# TAB 1: MAIN UPTREND LIST
# ════════════════════════════════════════════
with tab_main:
    st.subheader("📈 Confirmed Uptrend Stocks — $500M+ Market Cap")
    st.caption("HH+HL structure confirmed. Sorted by composite score.")

    df_main = apply_filters(load_csv(FILES["main"]))

    if df_main.empty:
        st.info("No data yet. Run `nightly_scan.py` to generate the watchlist, or check your filters.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Stocks",          len(df_main))
        c2.metric("Avg Composite",   f"{df_main['composite'].mean():.1f}/10" if "composite" in df_main.columns else "—")
        c3.metric("Strong Uptrends", int((df_main.get("trend_structure", pd.Series()) == "STRONG UPTREND").sum()))
        c4.metric("Avg Rev Growth",  f"{df_main['rev_growth_pct'].mean():.0f}%" if "rev_growth_pct" in df_main.columns else "—")

        render_stock_table(df_main)
        render_ai_section(df_main, "main", "main", macro_override)


# ════════════════════════════════════════════
# TAB 2: BASING WATCH — pre-multibagger list
# ════════════════════════════════════════════
with tab_base:
    st.subheader("🔭 Basing Watch — Pre-Multibagger Candidates")
    st.caption(
        "These stocks are NOT yet in an uptrend — they are building a base. "
        "Fund score ≥ 7 required. When they break out, they move fast. "
        "**Do not buy until the breakout is confirmed.**"
    )

    df_base = apply_filters(load_csv(FILES["base"]))

    if df_base.empty:
        st.info("No basing stocks found matching current filters.")
    else:
        st.info(f"🔭 {len(df_base)} stocks in basing phase with strong fundamentals. Watch for breakout confirmation.")

        c1, c2, c3 = st.columns(3)
        c1.metric("Stocks on Watch", len(df_base))
        c2.metric("Avg Fund Score",  f"{df_base['fund_score'].mean():.1f}/10" if "fund_score" in df_base.columns else "—")
        c3.metric("Avg Rev Growth",  f"{df_base['rev_growth_pct'].mean():.0f}%" if "rev_growth_pct" in df_base.columns else "—")

        render_stock_table(df_base)

        st.warning(
            "⚠️ **Breakout Trigger Rule:** Only act when a basing stock appears on the Uptrend tab. "
            "That means the nightly scan confirmed HH+HL structure — your entry signal."
        )

        render_ai_section(df_base, "basing", "basing", macro_override)


# ════════════════════════════════════════════
# TAB 3: SPECULATIVE — $100M–$500M
# ════════════════════════════════════════════
with tab_spec:
    st.subheader("⚡ Speculative Plays — $100M–$500M Market Cap")
    st.caption(
        "Higher risk, higher reward. Size at 25–50% of normal position. "
        "Liquidity is lower — use limit orders."
    )

    df_spec = apply_filters(load_csv(FILES["spec"]))

    if df_spec.empty:
        st.info("No speculative stocks found matching current filters.")
    else:
        st.warning(f"⚡ {len(df_spec)} small-cap stocks in confirmed uptrend. Max 10% of total portfolio in this bucket.")

        c1, c2, c3 = st.columns(3)
        c1.metric("Stocks",         len(df_spec))
        c2.metric("Avg Composite",  f"{df_spec['composite'].mean():.1f}/10" if "composite" in df_spec.columns else "—")
        c3.metric("Avg Velocity %", f"{df_spec['Velocity %'].mean():.0f}%" if "Velocity %" in df_spec.columns else "—")

        render_stock_table(df_spec)
        render_ai_section(df_spec, "spec", "spec", macro_override)


# ════════════════════════════════════════════
# TAB 4: MARKET PULSE
# ════════════════════════════════════════════
with tab_pulse:
    st.subheader("🌐 Daily Market Pulse")
    st.caption("Macro stance, sector rotation, and key events — refreshed on demand.")

    if st.button("🔄 Refresh Market Pulse", type="primary"):
        with st.spinner("Reading macro conditions..."):
            pulse = call_ai(build_pulse_prompt())
            st.session_state["pulse_report"] = pulse
            st.session_state["pulse_time"]   = datetime.now().strftime("%b %d, %Y at %H:%M")

    if st.session_state.get("pulse_report"):
        st.caption(f"Last refreshed: {st.session_state['pulse_time']}")
        st.markdown(st.session_state["pulse_report"])
    else:
        st.info("Hit **Refresh Market Pulse** to get today's macro read.")
