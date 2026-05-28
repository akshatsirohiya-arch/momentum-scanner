"""
INSTITUTIONAL MOMENTUM COMMAND — v5
=====================================
New in v5:
  - 🎯 Daily Opportunities — 8-pillar scorecard + PARANOIA section per stock
  - 📋 Portfolio Tracker   — thesis tracking, invalidation, event risk, correlation
  - 🔬 Failure Lab         — post-mortems, root cause, criteria updates
  - Sentiment/Positioning signals (short interest, options skew)
  - Catalyst calendar column (earnings date + known events)
  - Sector rotation heatmap in Market Pulse
  - All previous v4 tabs preserved
"""

import streamlit as st
import pandas as pd
import json
import requests
from datetime import datetime, date
import yfinance as yf

# ─────────────────────────────────────────────
# 1. CONFIG
# ─────────────────────────────────────────────
st.set_page_config(layout="wide", page_title="Momentum Command v5", page_icon="🏹")

RISK_PROFILE  = "Aggressive growth — position trades of 2–8 weeks, targeting 15–40% moves."
STRATEGY      = "Higher highs + higher lows trend structure required. Fundamental quality matters. AI/tech tailwinds preferred."
TODAY         = datetime.today().strftime("%B %d, %Y")
TODAY_ISO     = datetime.today().strftime("%Y-%m-%d")
CLAUDE_MODEL  = "claude-sonnet-4-20250514"

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

# Persistent state keys
PORTFOLIO_KEY  = "portfolio_v5"
FAILURELOG_KEY = "failure_log_v5"
CRITERIA_KEY   = "scoring_criteria_v5"

# ─────────────────────────────────────────────
# 2. SESSION STATE INIT
# ─────────────────────────────────────────────
def init_state():
    if PORTFOLIO_KEY not in st.session_state:
        st.session_state[PORTFOLIO_KEY] = []
    if FAILURELOG_KEY not in st.session_state:
        st.session_state[FAILURELOG_KEY] = []
    if CRITERIA_KEY not in st.session_state:
        st.session_state[CRITERIA_KEY] = {
            "trend_weight":     35,
            "momentum_weight":  25,
            "rvol_weight":      20,
            "fund_weight":      20,
            "notes":            "Default weights. Update after failure post-mortems.",
            "last_updated":     TODAY_ISO,
        }

init_state()

# ─────────────────────────────────────────────
# 3. CLAUDE CLIENT
# ─────────────────────────────────────────────
def call_ai(prompt: str, max_tokens: int = 1500) -> str:
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
                "max_tokens": max_tokens,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=90,
        )
        if not resp.ok:
            return f"⚠️ Claude API {resp.status_code}: {resp.text}"
        return resp.json()["content"][0]["text"]
    except requests.exceptions.Timeout:
        return "⚠️ Request timed out. Try again."
    except Exception as e:
        return f"⚠️ Claude API error: {str(e)}"

# ─────────────────────────────────────────────
# 4. DATA LOADERS
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
    except:
        return {}

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_sentiment_data(ticker: str) -> dict:
    """Fetch short interest, options data from yfinance."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        short_pct = info.get("shortPercentOfFloat", None)
        short_ratio = info.get("shortRatio", None)  # days to cover
        # Options skew: compare nearest expiry put/call IV if available
        return {
            "short_pct_float": round(short_pct * 100, 1) if short_pct else None,
            "days_to_cover":   round(short_ratio, 1) if short_ratio else None,
            "put_call_note":   "N/A",  # yfinance doesn't expose IV skew directly
        }
    except:
        return {"short_pct_float": None, "days_to_cover": None, "put_call_note": "N/A"}

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_earnings_date(ticker: str) -> str:
    """Get next earnings date."""
    try:
        t = yf.Ticker(ticker)
        cal = t.calendar
        if cal is not None and not cal.empty:
            if "Earnings Date" in cal.index:
                ed = cal.loc["Earnings Date"]
                if hasattr(ed, '__iter__'):
                    return str(ed.iloc[0])[:10]
                return str(ed)[:10]
        return "Unknown"
    except:
        return "Unknown"

def staleness_warning(meta: dict):
    if not meta:
        st.warning("⚠️ No scan metadata found.")
        return
    scan_date = meta.get("scan_date", "unknown")
    scan_time = meta.get("scan_time", "")
    if scan_date != datetime.today().strftime("%Y-%m-%d") and scan_date != TODAY:
        st.warning(f"⚠️ Data is from **{scan_date}** — today is {TODAY}.")
    else:
        st.success(f"✅ Data fresh — scanned today at {scan_time}")

def fmt_mktcap(val) -> str:
    try:
        v = float(val)
        if v >= 1e12: return f"${v/1e12:.1f}T"
        if v >= 1e9:  return f"${v/1e9:.1f}B"
        if v >= 1e6:  return f"${v/1e6:.0f}M"
        return f"${v:,.0f}"
    except:
        return "N/A"

def apply_filters(df: pd.DataFrame, min_composite: float, min_fund: float, sector_filter: str) -> pd.DataFrame:
    if df.empty:
        return df
    if "composite" in df.columns:
        df = df[pd.to_numeric(df["composite"], errors="coerce") >= min_composite]
    if "fund_score" in df.columns:
        df = df[pd.to_numeric(df["fund_score"], errors="coerce") >= min_fund]
    if sector_filter.strip() and "sector" in df.columns:
        df = df[df["sector"].str.contains(sector_filter.strip(), case=False, na=False)]
    if "composite" in df.columns:
        df = df.sort_values("composite", ascending=False)
    if "market_cap" in df.columns:
        df["market_cap"] = df["market_cap"].apply(fmt_mktcap)
    df = df.reset_index(drop=True)
    df.index += 1
    return df

# ─────────────────────────────────────────────
# 5. AI PROMPTS
# ─────────────────────────────────────────────
def build_opportunity_prompt(ticker: str, stock_data: dict, macro_context: str, criteria: dict) -> str:
    return f"""
You are an elite institutional equity analyst and PARANOID risk manager. Today is {TODAY}.

TRADER PROFILE:
- Risk: {RISK_PROFILE}
- Strategy: {STRATEGY}
- Current scoring weights: Trend {criteria['trend_weight']}%, Momentum {criteria['momentum_weight']}%, RVOL {criteria['rvol_weight']}%, Fundamentals {criteria['fund_weight']}%

MACRO CONTEXT:
{macro_context if macro_context else "US markets in AI-driven bull phase. Rate environment stable. Monitor tariff risks."}

STOCK DATA FOR {ticker}:
{json.dumps(stock_data, indent=2)}

YOUR TASK — produce a COMPLETE opportunity scorecard:

## 🎯 OPPORTUNITY SCORECARD: {ticker}

### 8-PILLAR SCORE (rate each 1-10 with one-line justification):
1. **Momentum** — price trend velocity and consistency
2. **Fundamentals** — revenue growth, margins, earnings quality
3. **News/Catalysts** — recent news, upcoming catalysts, narrative strength
4. **Macro** — does the macro environment help or hurt this trade?
5. **Sentiment/Positioning** — short interest, options flow, crowdedness
6. **Sector Rotation** — is money flowing INTO this sector right now?
7. **Liquidity** — volume, spread, ease of entry/exit
8. **Catalyst Calendar** — earnings, product launches, events in next 60 days

**COMPOSITE CONVICTION: X/10**

### TRADE SETUP:
- **Entry Zone:** [specific price or condition]
- **Target:** [price] ([%] upside, [timeframe])
- **Stop Loss:** [price] — invalidation condition
- **Position Size Guidance:** [% of portfolio, given risk]
- **Ideal Entry Trigger:** [exact condition before you pull the trigger]

### ⚠️ PARANOIA SECTION — WHY THIS TRADE FAILS:
(Be brutal. Think like a short seller. At least 5 specific reasons.)
1. [Macro risk]
2. [Fundamental risk]
3. [Technical risk — what chart pattern breaks this]
4. [Sentiment/positioning risk — who else is crowded here?]
5. [Catalyst risk — what binary event could kill this]
6. [Hidden risk — what is the market NOT pricing in that could surprise?]

### VERDICT:
**ACT NOW / WAIT FOR TRIGGER / AVOID**
One paragraph — be direct and opinionated.

Format in clean markdown. Be specific, not generic.
"""

def build_analysis_prompt(df_top: pd.DataFrame, macro_context: str = "", mode: str = "main") -> str:
    cols = [c for c in [
        "Ticker", "sector", "industry", "Velocity %", "RVOL",
        "trend_structure", "trend_score", "fund_score", "composite",
        "rev_growth_pct", "profit_margin_pct", "trailing_pe", "pct_from_6m_high"
    ] if c in df_top.columns]
    stocks_json = df_top[cols].head(10).to_json(orient="records", indent=2)

    catalyst_instruction = """
5. CATALYST SCAN — CRITICAL FOR MULTIBAGGERS:
   Flag any stock with: earnings in 30-60 days, binary event (FDA/contract/launch), or sector rotation catalyst.
   Flag as ⚡ CATALYST ALERT even if composite score is average.
"""
    basing_instruction = """
NOTE: BASING phase — not yet broken out. Identify most likely breakout candidates.
Focus on: fundamentals quality, base tightness, sector tailwinds, upcoming catalyst.
Flag top 3 as: 🔭 WATCHLIST — give specific trigger condition to BUY.
""" if mode == "basing" else ""

    spec_instruction = """
NOTE: Speculative small-caps ($100M–$500M). Size at 25-50% normal. Flag institutional accumulation signals.
""" if mode == "spec" else ""

    return f"""
You are an elite institutional equity analyst. Today is {TODAY}.
TRADER PROFILE: Risk: {RISK_PROFILE} | Strategy: {STRATEGY}
MACRO: {macro_context if macro_context else "US markets AI-driven bull phase. Rate environment stable."}
STOCK DATA: {stocks_json}
{basing_instruction}{spec_instruction}
TASK:
1. TOP 3 picks for position trade (2-8 weeks): WHY NOW, ENTRY, TARGET, STOP, RISK, CONVICTION.
2. PORTFOLIO NOTE: sector concentration or macro risk across picks.
3. AVOID LIST: stocks to skip and why.
{catalyst_instruction}
Format clean markdown. Be specific and actionable.
"""

def build_pulse_prompt(criteria: dict) -> str:
    return f"""
You are a macro strategist advising an aggressive US equity position trader. Today is {TODAY}.
Current framework scoring weights: Trend {criteria['trend_weight']}%, Momentum {criteria['momentum_weight']}%, RVOL {criteria['rvol_weight']}%, Fundamentals {criteria['fund_weight']}%

## MARKET STANCE
FULLY INVESTED | CAUTIOUS | CASH — 2-3 sentence justification.

## KEY MACRO FACTORS
- Fed & Rates | Dollar | VIX | Earnings cycle

## SECTOR ROTATION HEATMAP
For each sector below, rate: 🔥 HOT | ⚡ ACTIVE | ❄️ COOLING | 🚫 AVOID
- Technology | Healthcare | Financials | Energy | Industrials | Consumer Disc | Consumer Staples | Utilities | Materials | Real Estate | Communication

## SECTOR ROTATION ACTION
- OWN NOW: Top 2 sectors and why
- REDUCE: 1-2 sectors losing momentum
- WATCH: 1 emerging theme

## AI TRADE HEALTH
Rate: 🔥 HOT / ⚡ ACTIVE / ❄️ COOLING

## SENTIMENT & POSITIONING
- Overall market sentiment (Fearful/Neutral/Greedy)
- Key crowded trades to be wary of
- Short squeeze setups (if any)

## THIS WEEK — EVENT RISK CALENDAR
Specific events, data releases, earnings to watch.

## FRAMEWORK WEIGHT RECOMMENDATION
Given current market regime, should we adjust our scoring weights? Suggest changes if needed.

Be direct and opinionated. No hedging.
"""

def build_postmortem_prompt(trade: dict, criteria: dict) -> str:
    return f"""
You are a trading coach conducting a rigorous post-mortem. Today is {TODAY}.

TRADE DETAILS:
{json.dumps(trade, indent=2)}

CURRENT SCORING CRITERIA:
{json.dumps(criteria, indent=2)}

TASK — COMPLETE POST-MORTEM:

## 📋 TRADE POST-MORTEM: {trade.get('ticker', 'Unknown')}

### WHAT HAPPENED:
Objective description of how the trade played out vs thesis.

### ROOT CAUSE ANALYSIS:
Was the failure due to:
- [ ] Wrong thesis (fundamental misread)
- [ ] Right thesis, wrong timing (macro/micro timing)
- [ ] Sentiment/positioning trap (too crowded)
- [ ] Macro override (broader market move overwhelmed stock)
- [ ] Catalyst failure (expected catalyst didn't materialize or was weaker)
- [ ] Technical breakdown (chart structure failed)
- [ ] Liquidity/execution issue
- [ ] Risk management failure (held too long, sized too big)

### WHAT THE PRE-TRADE PARANOIA SECTION MISSED:
(Be specific about what warning signs existed that were ignored or not flagged.)

### CRITERIA UPDATES RECOMMENDED:
Based on this failure, what should we change in our scoring/process?
1. [Specific change to scoring weights or rules]
2. [Specific new checklist item to add]
3. [Specific red flag to screen for going forward]

### PATTERN RECOGNITION:
Is this failure part of a pattern? Compare to previous failures if relevant.

### GRADE: A / B / C / D / F
(Grade the original analysis quality, separate from outcome.)

Be brutally honest. This is how we get better.
"""

def build_portfolio_health_prompt(portfolio: list, criteria: dict, macro_context: str) -> str:
    return f"""
You are a risk manager reviewing a live portfolio. Today is {TODAY}.

PORTFOLIO:
{json.dumps(portfolio, indent=2)}

CURRENT CRITERIA:
{json.dumps(criteria, indent=2)}

MACRO: {macro_context if macro_context else "Standard bull market conditions."}

## PORTFOLIO HEALTH CHECK

### POSITION-BY-POSITION:
For each position: Is the original thesis still intact? Any new risks? Hold/Trim/Exit recommendation.

### CORRELATION RISK:
Are multiple positions actually the same bet (same sector, same macro driver)? Flag hidden concentration.

### PORTFOLIO-LEVEL PARANOIA:
What single event could damage 50%+ of this portfolio simultaneously?

### SUGGESTED ACTIONS:
Prioritized list of what to do TODAY.

### OVERALL HEALTH: 🟢 HEALTHY / 🟡 CAUTION / 🔴 ACTION REQUIRED

Be direct. The trader needs to act on this.
"""

# ─────────────────────────────────────────────
# 6. TABLE RENDERER
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
# 7. MAIN APP
# ─────────────────────────────────────────────
meta     = load_meta()
criteria = st.session_state[CRITERIA_KEY]

st.title("🏹 Institutional Momentum Command v5")
st.caption(f"Today: {TODAY}  |  8-pillar framework  |  Paranoia-first analysis  |  Post-mortem loop")

if meta:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Universe Scanned", f"{meta.get('universe_size', '?'):,}")
    c2.metric("Valid Tickers",    f"{meta.get('valid_tickers', '?'):,}")
    c3.metric("Main Uptrend",     meta.get("main_count", "?"))
    c4.metric("Basing Watch",     meta.get("basing_count", "?"))
    c5.metric("Speculative",      meta.get("spec_count", "?"))

staleness_warning(meta)

# ── Sidebar ──────────────────────────────────
st.sidebar.header("⚙️ Filters & Config")
min_composite  = st.sidebar.slider("Min Composite Score", 1.0, 10.0, 5.0, 0.5)
min_fund_score = st.sidebar.slider("Min Fundamental Score", 1.0, 10.0, 4.0, 0.5)
sector_filter  = st.sidebar.text_input("Sector filter (partial match)", placeholder="Tech, Health, Energy...")
macro_override = st.sidebar.text_area(
    "Macro Context Override",
    placeholder="e.g. Fed paused, tariff risks elevated, AI capex still growing..."
)

st.sidebar.markdown("---")
st.sidebar.markdown("**📐 Scoring Weights**")
st.sidebar.markdown(f"Trend Structure: **{criteria['trend_weight']}%**")
st.sidebar.markdown(f"Momentum (Vel%): **{criteria['momentum_weight']}%**")
st.sidebar.markdown(f"RVOL:            **{criteria['rvol_weight']}%**")
st.sidebar.markdown(f"Fundamentals:    **{criteria['fund_weight']}%**")
if criteria.get("notes"):
    st.sidebar.caption(f"📝 {criteria['notes']}")
st.sidebar.markdown(f"*Last updated: {criteria.get('last_updated', 'N/A')}*")

st.sidebar.markdown("---")
st.sidebar.markdown("**Data Pipeline**")
st.sidebar.markdown("Scanner: `nightly_scan.py`")
st.sidebar.markdown("Schedule: Weekdays 6pm ET")

# ── Tabs ─────────────────────────────────────
tab_opps, tab_port, tab_fail, tab_main, tab_base, tab_spec, tab_pulse = st.tabs([
    "🎯 Daily Opportunities",
    "📋 Portfolio Tracker",
    "🔬 Failure Lab",
    "📈 Uptrend Stocks",
    "🔭 Basing Watch",
    "⚡ Speculative",
    "🌐 Market Pulse",
])


# ════════════════════════════════════════════
# TAB 1: DAILY OPPORTUNITIES
# ════════════════════════════════════════════
with tab_opps:
    st.subheader("🎯 Daily Opportunity Scorecard")
    st.caption("8-pillar analysis + paranoia section. Every opportunity must earn its place.")

    col_l, col_r = st.columns([2, 1])
    with col_l:
        st.markdown("##### Analyze a Specific Stock")
        opp_ticker    = st.text_input("Ticker Symbol", placeholder="NVDA, AAPL, TSLA...", key="opp_ticker").upper().strip()
        opp_macro     = st.text_area("Additional context (optional)", placeholder="Any specific news, sector moves, or thesis you want included...", key="opp_macro", height=80)
    with col_r:
        st.markdown("##### Quick-Load from Watchlist")
        df_main_quick = load_csv(FILES["main"])
        if not df_main_quick.empty and "Ticker" in df_main_quick.columns:
            top_tickers = df_main_quick.sort_values("composite", ascending=False)["Ticker"].head(15).tolist() if "composite" in df_main_quick.columns else df_main_quick["Ticker"].head(15).tolist()
            quick_pick  = st.selectbox("Pick from today's scan", ["— select —"] + top_tickers, key="quick_pick")
            if quick_pick != "— select —":
                st.session_state["opp_ticker"] = quick_pick
                opp_ticker = quick_pick
        st.markdown("")
        run_analysis = st.button("🧠 Run Full Scorecard", type="primary", key="run_opp", use_container_width=True)

    if run_analysis and opp_ticker:
        with st.spinner(f"Fetching data and building paranoia scorecard for {opp_ticker}..."):
            # Gather stock data
            stock_data = {}
            try:
                t    = yf.Ticker(opp_ticker)
                info = t.info
                hist = t.history(period="6mo")
                stock_data = {
                    "ticker":            opp_ticker,
                    "company_name":      info.get("longName", "N/A"),
                    "sector":            info.get("sector", "N/A"),
                    "industry":          info.get("industry", "N/A"),
                    "current_price":     info.get("currentPrice", info.get("regularMarketPrice", "N/A")),
                    "market_cap":        fmt_mktcap(info.get("marketCap", 0)),
                    "revenue_growth":    f"{info.get('revenueGrowth', 0)*100:.1f}%" if info.get('revenueGrowth') else "N/A",
                    "profit_margin":     f"{info.get('profitMargins', 0)*100:.1f}%" if info.get('profitMargins') else "N/A",
                    "trailing_pe":       round(info.get("trailingPE", 0), 1) if info.get("trailingPE") else "N/A",
                    "forward_pe":        round(info.get("forwardPE", 0), 1) if info.get("forwardPE") else "N/A",
                    "52w_high":          info.get("fiftyTwoWeekHigh", "N/A"),
                    "52w_low":           info.get("fiftyTwoWeekLow", "N/A"),
                    "pct_from_52w_high": f"{((info.get('currentPrice', 0) / info.get('fiftyTwoWeekHigh', 1)) - 1)*100:.1f}%" if info.get('currentPrice') and info.get('fiftyTwoWeekHigh') else "N/A",
                    "avg_volume":        f"{info.get('averageVolume', 0):,}",
                    "short_pct_float":   f"{info.get('shortPercentOfFloat', 0)*100:.1f}%" if info.get('shortPercentOfFloat') else "N/A",
                    "days_to_cover":     round(info.get('shortRatio', 0), 1) if info.get('shortRatio') else "N/A",
                    "earnings_date":     fetch_earnings_date(opp_ticker),
                    "beta":              round(info.get("beta", 0), 2) if info.get("beta") else "N/A",
                    "description":       info.get("longBusinessSummary", "")[:300] if info.get("longBusinessSummary") else "N/A",
                }
                # 20-day velocity
                if not hist.empty and len(hist) >= 20:
                    vel = ((hist["Close"].iloc[-1] / hist["Close"].iloc[-20]) - 1) * 100
                    stock_data["20d_velocity_pct"] = f"{vel:.1f}%"
                    avg_vol_20 = hist["Volume"].iloc[-20:].mean()
                    rvol = hist["Volume"].iloc[-1] / avg_vol_20 if avg_vol_20 > 0 else 1
                    stock_data["relative_volume"] = f"{rvol:.2f}x"
                # Match with scan data if available
                if not df_main_quick.empty and "Ticker" in df_main_quick.columns:
                    match = df_main_quick[df_main_quick["Ticker"] == opp_ticker]
                    if not match.empty:
                        for col in ["trend_structure", "composite", "trend_score", "momentum_score", "fund_score"]:
                            if col in match.columns:
                                stock_data[f"scan_{col}"] = match.iloc[0][col]
            except Exception as e:
                st.warning(f"Could not fully load yfinance data: {e}. Running with partial data.")
                stock_data = {"ticker": opp_ticker, "note": "Limited data available"}

            combined_macro = (macro_override or "") + "\n" + (opp_macro or "")
            prompt  = build_opportunity_prompt(opp_ticker, stock_data, combined_macro, criteria)
            report  = call_ai(prompt, max_tokens=2000)
            st.session_state[f"opp_report_{opp_ticker}"] = {
                "report":    report,
                "time":      datetime.now().strftime("%b %d, %Y at %H:%M"),
                "ticker":    opp_ticker,
                "data":      stock_data,
            }

    # Display report
    report_key = f"opp_report_{opp_ticker}" if opp_ticker else None
    if report_key and st.session_state.get(report_key):
        cached = st.session_state[report_key]
        st.markdown(f"---")

        # Data snapshot
        with st.expander(f"📊 Raw Data Used — {cached['ticker']}", expanded=False):
            st.json(cached["data"])

        st.caption(f"Scorecard generated: {cached['time']}")
        st.markdown(cached["report"])

        col_a, col_b = st.columns(2)
        with col_a:
            st.download_button(
                label="📥 Download Scorecard",
                data=f"Scorecard: {cached['ticker']}\nGenerated: {cached['time']}\n\n{cached['report']}",
                file_name=f"Scorecard_{cached['ticker']}_{datetime.now().strftime('%Y%m%d')}.txt",
                mime="text/plain",
                key=f"dl_opp_{cached['ticker']}",
            )
        with col_b:
            if st.button(f"➕ Add {cached['ticker']} to Portfolio", key=f"add_port_{cached['ticker']}"):
                st.session_state["prefill_ticker"] = cached["ticker"]
                st.session_state["prefill_price"]  = cached["data"].get("current_price", "")
                st.info(f"Go to 📋 Portfolio Tracker tab to complete the entry for {cached['ticker']}.")

    elif opp_ticker and not (report_key and st.session_state.get(report_key)):
        st.info(f"Press **Run Full Scorecard** to analyze {opp_ticker} across all 8 pillars.")

    st.markdown("---")
    st.markdown("### 📅 Today's Opportunity Log")
    st.caption("All scorecards run today in this session.")
    ran_today = [k.replace("opp_report_", "") for k in st.session_state if k.startswith("opp_report_")]
    if ran_today:
        for t in ran_today:
            r = st.session_state[f"opp_report_{t}"]
            with st.expander(f"**{t}** — analyzed at {r['time']}", expanded=False):
                st.markdown(r["report"])
    else:
        st.info("No stocks analyzed yet today. Enter a ticker above.")


# ════════════════════════════════════════════
# TAB 2: PORTFOLIO TRACKER
# ════════════════════════════════════════════
with tab_port:
    st.subheader("📋 Portfolio Tracker")
    st.caption("Track positions with thesis, invalidation conditions, and event risk.")

    portfolio = st.session_state[PORTFOLIO_KEY]

    # ── Add new position ──
    with st.expander("➕ Add New Position", expanded=len(portfolio) == 0):
        prefill_ticker = st.session_state.get("prefill_ticker", "")
        prefill_price  = st.session_state.get("prefill_price", "")

        pc1, pc2, pc3 = st.columns(3)
        with pc1:
            new_ticker    = st.text_input("Ticker", value=prefill_ticker, key="new_pos_ticker").upper().strip()
            new_entry     = st.number_input("Entry Price ($)", value=float(prefill_price) if prefill_price and str(prefill_price).replace('.','').isdigit() else 0.0, min_value=0.0, format="%.2f", key="new_pos_entry")
            new_shares    = st.number_input("Shares / Units", min_value=0.0, format="%.0f", key="new_pos_shares")
        with pc2:
            new_target    = st.number_input("Target Price ($)", min_value=0.0, format="%.2f", key="new_pos_target")
            new_stop      = st.number_input("Stop Loss ($)", min_value=0.0, format="%.2f", key="new_pos_stop")
            new_date      = st.date_input("Entry Date", value=date.today(), key="new_pos_date")
        with pc3:
            new_thesis    = st.text_area("Thesis (why you're in)", height=80, key="new_pos_thesis", placeholder="Momentum breakout from base, strong fundamentals, AI tailwind...")
            new_invalid   = st.text_area("Invalidation Condition", height=80, key="new_pos_invalid", placeholder="Close below $X, earnings miss, macro reversal...")

        pc4, pc5 = st.columns(2)
        with pc4:
            new_earnings  = st.text_input("Next Earnings Date", placeholder="YYYY-MM-DD or Unknown", key="new_pos_earnings")
        with pc5:
            new_catalyst  = st.text_input("Key Catalyst / Event", placeholder="Product launch, FDA decision...", key="new_pos_catalyst")

        if st.button("✅ Add Position", type="primary", key="add_pos_btn"):
            if new_ticker:
                position = {
                    "id":            datetime.now().strftime("%Y%m%d%H%M%S"),
                    "ticker":        new_ticker,
                    "entry_price":   new_entry,
                    "shares":        new_shares,
                    "target":        new_target,
                    "stop":          new_stop,
                    "entry_date":    str(new_date),
                    "thesis":        new_thesis,
                    "invalidation":  new_invalid,
                    "earnings_date": new_earnings,
                    "catalyst":      new_catalyst,
                    "status":        "OPEN",
                    "notes":         [],
                }
                st.session_state[PORTFOLIO_KEY].append(position)
                # Clear prefill
                st.session_state.pop("prefill_ticker", None)
                st.session_state.pop("prefill_price", None)
                st.success(f"✅ {new_ticker} added to portfolio.")
                st.rerun()
            else:
                st.error("Ticker is required.")

    # ── Portfolio table ──
    portfolio = st.session_state[PORTFOLIO_KEY]
    open_pos  = [p for p in portfolio if p.get("status") == "OPEN"]
    closed_pos = [p for p in portfolio if p.get("status") != "OPEN"]

    if open_pos:
        st.markdown(f"### Open Positions ({len(open_pos)})")

        # Fetch current prices
        port_rows = []
        for p in open_pos:
            try:
                info  = yf.Ticker(p["ticker"]).info
                curr  = info.get("currentPrice", info.get("regularMarketPrice", p["entry_price"]))
            except:
                curr = p["entry_price"]

            entry  = p.get("entry_price", 0) or 0
            pnl_pct = ((curr - entry) / entry * 100) if entry > 0 else 0
            pnl_abs = (curr - entry) * (p.get("shares", 0) or 0)
            tgt     = p.get("target", 0) or 0
            stop    = p.get("stop", 0) or 0
            upside  = ((tgt - curr) / curr * 100) if curr > 0 and tgt > 0 else 0
            downside = ((stop - curr) / curr * 100) if curr > 0 and stop > 0 else 0

            port_rows.append({
                "Ticker":      p["ticker"],
                "Entry":       entry,
                "Current":     curr,
                "P&L %":       round(pnl_pct, 1),
                "P&L $":       round(pnl_abs, 0),
                "Target":      tgt,
                "Stop":        stop,
                "Upside %":    round(upside, 1),
                "Downside %":  round(downside, 1),
                "Earnings":    p.get("earnings_date", "?"),
                "Entry Date":  p.get("entry_date", "?"),
            })

        df_port = pd.DataFrame(port_rows)
        st.dataframe(
            df_port,
            use_container_width=True,
            column_config={
                "P&L %":      st.column_config.NumberColumn("P&L %",    format="%.1f%%"),
                "P&L $":      st.column_config.NumberColumn("P&L $",    format="$%.0f"),
                "Upside %":   st.column_config.NumberColumn("Upside %", format="%.1f%%"),
                "Downside %": st.column_config.NumberColumn("Risk %",   format="%.1f%%"),
                "Entry":      st.column_config.NumberColumn("Entry",    format="$%.2f"),
                "Current":    st.column_config.NumberColumn("Current",  format="$%.2f"),
                "Target":     st.column_config.NumberColumn("Target",   format="$%.2f"),
                "Stop":       st.column_config.NumberColumn("Stop",     format="$%.2f"),
            },
            hide_index=True,
        )

        # Position-level actions
        st.markdown("#### Position Actions")
        for p in open_pos:
            with st.expander(f"**{p['ticker']}** — entered {p.get('entry_date', '?')} @ ${p.get('entry_price', 0):.2f}", expanded=False):
                col_i, col_ii = st.columns(2)
                with col_i:
                    st.markdown(f"**Thesis:** {p.get('thesis', 'N/A')}")
                    st.markdown(f"**Invalidation:** {p.get('invalidation', 'N/A')}")
                    st.markdown(f"**Next Earnings:** {p.get('earnings_date', 'N/A')}")
                    st.markdown(f"**Catalyst:** {p.get('catalyst', 'N/A')}")
                with col_ii:
                    note = st.text_input("Add note", key=f"note_{p['id']}", placeholder="Thesis update, market change...")
                    if st.button("Add Note", key=f"add_note_{p['id']}"):
                        p["notes"].append({"date": TODAY_ISO, "note": note})
                        st.success("Note added.")
                        st.rerun()
                    if p["notes"]:
                        for n in p["notes"]:
                            st.caption(f"📝 {n['date']}: {n['note']}")

                close_col1, close_col2 = st.columns(2)
                with close_col1:
                    exit_price  = st.number_input("Exit Price ($)", min_value=0.0, format="%.2f", key=f"exit_{p['id']}")
                    exit_reason = st.selectbox("Exit Reason", ["Target Hit", "Stop Hit", "Thesis Broken", "Macro Override", "Partial Trim", "Time Stop"], key=f"exit_reason_{p['id']}")
                with close_col2:
                    exit_notes = st.text_area("Exit Notes", height=80, key=f"exit_notes_{p['id']}", placeholder="What happened? What did you learn?")
                    if st.button(f"🔒 Close {p['ticker']}", key=f"close_{p['id']}", type="secondary"):
                        p["status"]      = "CLOSED"
                        p["exit_price"]  = exit_price
                        p["exit_reason"] = exit_reason
                        p["exit_date"]   = TODAY_ISO
                        p["exit_notes"]  = exit_notes
                        entry_p = p.get("entry_price", 0) or 0
                        p["pnl_pct"]     = round(((exit_price - entry_p) / entry_p * 100) if entry_p > 0 else 0, 1)
                        st.success(f"{p['ticker']} closed. Add to Failure Lab for post-mortem if needed.")
                        st.rerun()

        # AI portfolio health check
        st.markdown("---")
        st.subheader("🤖 AI Portfolio Health Check")
        if st.button("🔄 Run Portfolio Health Check", type="primary", key="port_health"):
            with st.spinner("Analyzing portfolio risk..."):
                prompt = build_portfolio_health_prompt(open_pos, criteria, macro_override)
                health = call_ai(prompt, max_tokens=1500)
                st.session_state["portfolio_health"] = {"report": health, "time": datetime.now().strftime("%b %d at %H:%M")}

        if st.session_state.get("portfolio_health"):
            st.caption(f"Last checked: {st.session_state['portfolio_health']['time']}")
            st.markdown(st.session_state["portfolio_health"]["report"])

    else:
        st.info("No open positions. Add your first position above.")

    # Closed positions
    if closed_pos:
        st.markdown(f"---\n### Closed Positions ({len(closed_pos)})")
        closed_rows = [{
            "Ticker":      p["ticker"],
            "Entry":       p.get("entry_price", 0),
            "Exit":        p.get("exit_price", 0),
            "P&L %":       p.get("pnl_pct", 0),
            "Reason":      p.get("exit_reason", "N/A"),
            "Entry Date":  p.get("entry_date", "N/A"),
            "Exit Date":   p.get("exit_date", "N/A"),
        } for p in closed_pos]
        st.dataframe(pd.DataFrame(closed_rows), use_container_width=True, hide_index=True)


# ════════════════════════════════════════════
# TAB 3: FAILURE LAB
# ════════════════════════════════════════════
with tab_fail:
    st.subheader("🔬 Failure Lab")
    st.caption("Post-mortems on closed trades + criteria evolution. This is how the framework improves.")

    failure_log = st.session_state[FAILURELOG_KEY]

    # ── Log a new post-mortem ──
    with st.expander("➕ Log New Post-Mortem", expanded=len(failure_log) == 0):
        # Pre-fill from closed positions
        closed_pos_fail = [p for p in st.session_state[PORTFOLIO_KEY] if p.get("status") == "CLOSED"]
        closed_options  = ["— manual entry —"] + [f"{p['ticker']} (closed {p.get('exit_date','?')} | {p.get('pnl_pct','?')}%)" for p in closed_pos_fail]
        selected_closed = st.selectbox("Load from closed position", closed_options, key="fail_select")

        prefill_fail = {}
        if selected_closed != "— manual entry —":
            idx = closed_options.index(selected_closed) - 1
            prefill_fail = closed_pos_fail[idx]

        fc1, fc2 = st.columns(2)
        with fc1:
            fail_ticker  = st.text_input("Ticker", value=prefill_fail.get("ticker", ""), key="fail_ticker").upper().strip()
            fail_entry   = st.number_input("Entry Price", value=float(prefill_fail.get("entry_price", 0) or 0), format="%.2f", key="fail_entry")
            fail_exit    = st.number_input("Exit Price", value=float(prefill_fail.get("exit_price", 0) or 0), format="%.2f", key="fail_exit")
            fail_pnl     = st.number_input("P&L %", value=float(prefill_fail.get("pnl_pct", 0) or 0), format="%.1f", key="fail_pnl")
        with fc2:
            fail_thesis  = st.text_area("Original Thesis", value=prefill_fail.get("thesis", ""), height=80, key="fail_thesis")
            fail_what    = st.text_area("What Actually Happened", height=80, key="fail_what", placeholder="Describe the sequence of events...")
            fail_reason  = st.multiselect(
                "Root Cause (select all that apply)",
                ["Wrong Thesis", "Wrong Timing", "Sentiment Trap", "Macro Override",
                 "Catalyst Failure", "Technical Breakdown", "Liquidity Issue", "Risk Mgmt Failure", "Other"],
                key="fail_reason"
            )
        fail_missed  = st.text_area("What did the pre-trade paranoia miss?", key="fail_missed", placeholder="What warning signs were ignored or not flagged?")
        fail_learn   = st.text_area("Key Learnings + Criteria Updates", key="fail_learn", placeholder="What rule should we add or change going forward?")

        if st.button("📝 Log Post-Mortem", type="primary", key="log_fail_btn"):
            if fail_ticker:
                entry = {
                    "id":          datetime.now().strftime("%Y%m%d%H%M%S"),
                    "date":        TODAY_ISO,
                    "ticker":      fail_ticker,
                    "entry_price": fail_entry,
                    "exit_price":  fail_exit,
                    "pnl_pct":     fail_pnl,
                    "thesis":      fail_thesis,
                    "what_happened": fail_what,
                    "root_cause":  fail_reason,
                    "missed":      fail_missed,
                    "learnings":   fail_learn,
                    "ai_analysis": None,
                }
                st.session_state[FAILURELOG_KEY].append(entry)
                st.success(f"Post-mortem for {fail_ticker} logged.")
                st.rerun()

    # ── AI post-mortem ──
    failure_log = st.session_state[FAILURELOG_KEY]
    if failure_log:
        st.markdown(f"### Post-Mortem Log ({len(failure_log)} trades)")

        for i, fm in enumerate(reversed(failure_log)):
            pnl_color = "🟢" if fm.get("pnl_pct", 0) > 0 else "🔴"
            with st.expander(f"{pnl_color} **{fm['ticker']}** — {fm['date']} | P&L: {fm.get('pnl_pct', '?')}%", expanded=(i == 0)):
                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown(f"**Thesis:** {fm.get('thesis', 'N/A')}")
                    st.markdown(f"**What Happened:** {fm.get('what_happened', 'N/A')}")
                    st.markdown(f"**Root Cause:** {', '.join(fm.get('root_cause', [])) or 'N/A'}")
                with col_b:
                    st.markdown(f"**Paranoia Missed:** {fm.get('missed', 'N/A')}")
                    st.markdown(f"**Learnings:** {fm.get('learnings', 'N/A')}")

                if fm.get("ai_analysis"):
                    st.markdown("**🤖 AI Analysis:**")
                    st.markdown(fm["ai_analysis"])
                else:
                    if st.button(f"🤖 Run AI Post-Mortem for {fm['ticker']}", key=f"ai_pm_{fm['id']}"):
                        with st.spinner("AI analyzing failure..."):
                            pm_report = call_ai(build_postmortem_prompt(fm, criteria), max_tokens=1500)
                            fm["ai_analysis"] = pm_report
                            st.rerun()

        # ── Criteria Update Panel ──
        st.markdown("---")
        st.subheader("📐 Scoring Criteria Management")
        st.caption("Update weights based on what the post-mortems are teaching us.")

        col_w1, col_w2, col_w3, col_w4 = st.columns(4)
        with col_w1:
            new_trend   = st.number_input("Trend Weight %",    value=criteria["trend_weight"],    min_value=5, max_value=60, step=5, key="new_trend_w")
        with col_w2:
            new_mom     = st.number_input("Momentum Weight %", value=criteria["momentum_weight"], min_value=5, max_value=60, step=5, key="new_mom_w")
        with col_w3:
            new_rvol    = st.number_input("RVOL Weight %",     value=criteria["rvol_weight"],     min_value=5, max_value=60, step=5, key="new_rvol_w")
        with col_w4:
            new_fund    = st.number_input("Fund Weight %",     value=criteria["fund_weight"],     min_value=5, max_value=60, step=5, key="new_fund_w")

        new_notes = st.text_area("Reason for change", key="criteria_notes", placeholder="What did the post-mortems teach us that drove this change?")

        total_w = new_trend + new_mom + new_rvol + new_fund
        if total_w != 100:
            st.warning(f"⚠️ Weights sum to {total_w}% — must equal 100%.")
        else:
            if st.button("💾 Save Updated Criteria", type="primary", key="save_criteria"):
                st.session_state[CRITERIA_KEY] = {
                    "trend_weight":     new_trend,
                    "momentum_weight":  new_mom,
                    "rvol_weight":      new_rvol,
                    "fund_weight":      new_fund,
                    "notes":            new_notes or criteria.get("notes", ""),
                    "last_updated":     TODAY_ISO,
                }
                st.success("✅ Criteria updated. All future AI analysis will use new weights.")
                st.rerun()

        # Pattern summary across all post-mortems
        if len(failure_log) >= 2:
            st.markdown("---")
            st.subheader("🔍 Pattern Analysis Across All Post-Mortems")
            if st.button("🧠 Identify Failure Patterns", key="pattern_btn", type="primary"):
                with st.spinner("Analyzing patterns across all failures..."):
                    pattern_prompt = f"""
You are a trading coach reviewing ALL post-mortems for a trader. Today is {TODAY}.

ALL POST-MORTEMS:
{json.dumps(failure_log, indent=2)}

CURRENT CRITERIA:
{json.dumps(criteria, indent=2)}

TASK:
1. Identify the TOP 3 recurring failure patterns across all trades.
2. For each pattern: how many trades affected, estimated P&L impact, specific fix.
3. FRAMEWORK VERDICT: Is the current scoring system working? What's the single most impactful change?
4. BEHAVIORAL PATTERNS: Are there trader behavior issues (holding losers, cutting winners, FOMO entries)?

Be direct. Grade the overall framework A/B/C/D/F.
"""
                    patterns = call_ai(pattern_prompt, max_tokens=1500)
                    st.session_state["pattern_analysis"] = {"report": patterns, "time": datetime.now().strftime("%b %d at %H:%M")}

            if st.session_state.get("pattern_analysis"):
                st.caption(f"Analyzed: {st.session_state['pattern_analysis']['time']}")
                st.markdown(st.session_state["pattern_analysis"]["report"])

    else:
        st.info("No post-mortems logged yet. Close a position and log what happened.")
        st.markdown("""
**How to use the Failure Lab:**
1. After closing a position (win or loss), log a post-mortem
2. Run the AI analysis to get root cause diagnosis
3. Identify what the pre-trade paranoia section missed
4. Update scoring criteria based on learnings
5. Once you have 3+ post-mortems, run Pattern Analysis to spot systemic issues
""")


# ════════════════════════════════════════════
# TAB 4: MAIN UPTREND (unchanged from v4)
# ════════════════════════════════════════════
with tab_main:
    st.subheader("📈 Confirmed Uptrend Stocks — $500M+ Market Cap")
    st.caption("HH+HL structure confirmed. Sorted by composite score.")
    df_main = apply_filters(load_csv(FILES["main"]), min_composite, min_fund_score, sector_filter)
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
# TAB 5: BASING WATCH
# ════════════════════════════════════════════
with tab_base:
    st.subheader("🔭 Basing Watch — Pre-Multibagger Candidates")
    st.caption("Not yet in uptrend. Fund score ≥ 7. Do not buy until breakout confirmed.")
    df_base = apply_filters(load_csv(FILES["base"]), min_composite, min_fund_score, sector_filter)
    if df_base.empty:
        st.info("No basing stocks found matching current filters.")
    else:
        st.info(f"🔭 {len(df_base)} stocks in basing phase with strong fundamentals.")
        c1, c2, c3 = st.columns(3)
        c1.metric("Stocks on Watch", len(df_base))
        c2.metric("Avg Fund Score",  f"{df_base['fund_score'].mean():.1f}/10" if "fund_score" in df_base.columns else "—")
        c3.metric("Avg Rev Growth",  f"{df_base['rev_growth_pct'].mean():.0f}%" if "rev_growth_pct" in df_base.columns else "—")
        render_stock_table(df_base)
        st.warning("⚠️ **Breakout Trigger Rule:** Only act when a basing stock appears on the Uptrend tab.")
        render_ai_section(df_base, "basing", "basing", macro_override)


# ════════════════════════════════════════════
# TAB 6: SPECULATIVE
# ════════════════════════════════════════════
with tab_spec:
    st.subheader("⚡ Speculative Plays — $100M–$500M Market Cap")
    st.caption("Higher risk, higher reward. Size at 25–50% of normal position. Use limit orders.")
    df_spec = apply_filters(load_csv(FILES["spec"]), min_composite, min_fund_score, sector_filter)
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
# TAB 7: MARKET PULSE (upgraded)
# ════════════════════════════════════════════
with tab_pulse:
    st.subheader("🌐 Daily Market Pulse")
    st.caption("Macro stance, sector rotation heatmap, sentiment, and event risk — refreshed on demand.")

    col_p1, col_p2 = st.columns([1, 3])
    with col_p1:
        if st.button("🔄 Refresh Market Pulse", type="primary", key="pulse_btn"):
            with st.spinner("Reading macro conditions..."):
                pulse = call_ai(build_pulse_prompt(criteria), max_tokens=1500)
                st.session_state["pulse_report"] = pulse
                st.session_state["pulse_time"]   = datetime.now().strftime("%b %d, %Y at %H:%M")

    with col_p2:
        if st.session_state.get("pulse_report"):
            st.caption(f"Last refreshed: {st.session_state['pulse_time']}")
        else:
            st.info("Hit **Refresh Market Pulse** to get today's macro read.")

    if st.session_state.get("pulse_report"):
        st.markdown(st.session_state["pulse_report"])

        # Download
        st.download_button(
            label="📥 Download Pulse Report",
            data=f"Market Pulse\n{st.session_state['pulse_time']}\n\n{st.session_state['pulse_report']}",
            file_name=f"MarketPulse_{datetime.now().strftime('%Y%m%d')}.txt",
            mime="text/plain",
            key="dl_pulse",
        )
