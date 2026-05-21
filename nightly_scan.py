
"""
NIGHTLY MARKET SCANNER
======================
Run this script once daily (after market close, e.g. 6pm ET).
Scans the entire US market — no CSV input required.

Output files (read by the Streamlit app instantly):
  data/enriched_watchlist.csv   — confirmed uptrend stocks, ranked by composite score
  data/basing_watchlist.csv     — basing stocks with strong fundamentals (future multibaggers)
  data/speculative_watchlist.csv — sub-$500M high-momentum plays
  data/scan_meta.json           — timestamp + universe stats

Run locally:   python nightly_scan.py
Run on GitHub Actions: see .github/workflows/nightly_scan.yml

Requirements:
  pip install yfinance pandas requests lxml html5lib beautifulsoup4
"""

import os
import json
import time
import logging
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

os.makedirs("data", exist_ok=True)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
CHUNK_SIZE      = 500     # tickers per yf.download batch
MAX_FUND_WORKERS = 30     # parallel fundamentals threads
MIN_MKTCAP_MAIN  = 500e6  # $500M — main list
MIN_MKTCAP_SPEC  = 100e6  # $100M — speculative list
MIN_PRICE        = 5.0    # ignore penny stocks
HH_HL_WINDOW    = 20      # rolling window for trend structure
TODAY            = datetime.today().strftime("%B %d, %Y")


# ─────────────────────────────────────────────
# 1. UNIVERSE BUILDER — no CSV needed
# ─────────────────────────────────────────────
def get_sp500() -> list:
    """S&P 500 from Wikipedia."""
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        return tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
    except Exception as e:
        log.warning(f"S&P 500 fetch failed: {e}")
        return []


def get_nasdaq100() -> list:
    """Nasdaq 100 from Wikipedia."""
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100")
        # Try multiple table indices — Wikipedia layout can shift
        for t in tables:
            if "Ticker" in t.columns:
                return t["Ticker"].tolist()
            if "Symbol" in t.columns:
                return t["Symbol"].tolist()
        return []
    except Exception as e:
        log.warning(f"Nasdaq 100 fetch failed: {e}")
        return []


def get_russell2000() -> list:
    """
    Russell 2000 from iShares IWM ETF holdings (free, no auth).
    Falls back to a curated small-cap list if unavailable.
    """
    try:
        url = "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund"
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        lines = resp.text.splitlines()
        # Find the header row
        start = next(i for i, l in enumerate(lines) if l.startswith("Ticker"))
        df = pd.read_csv(pd.io.common.StringIO("\n".join(lines[start:])))
        tickers = df["Ticker"].dropna().astype(str).str.strip()
        tickers = tickers[tickers.str.match(r'^[A-Z]{1,5}$')]
        return tickers.tolist()
    except Exception as e:
        log.warning(f"Russell 2000 fetch failed: {e}. Using fallback list.")
        return []


def get_sp400() -> list:
    """S&P 400 Mid-cap from Wikipedia."""
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies")
        for t in tables:
            if "Ticker symbol" in t.columns:
                return t["Ticker symbol"].tolist()
            if "Symbol" in t.columns:
                return t["Symbol"].tolist()
        return []
    except Exception as e:
        log.warning(f"S&P 400 fetch failed: {e}")
        return []


def build_universe() -> list:
    """
    Combines all sources into a deduplicated universe.
    Typical size: 2,800–3,500 unique tickers.
    """
    log.info("Building market universe...")

    sp500   = get_sp500()
    ndx100  = get_nasdaq100()
    sp400   = get_sp400()
    russ2k  = get_russell2000()

    all_tickers = sp500 + ndx100 + sp400 + russ2k

    # Deduplicate, clean, remove obvious bad symbols
    seen = set()
    clean = []
    for t in all_tickers:
        t = str(t).strip().upper()
        if not t or t in seen:
            continue
        if not t.replace("-", "").isalpha():   # skip symbols with numbers
            continue
        if len(t) > 6:                          # skip overly long symbols
            continue
        seen.add(t)
        clean.append(t)

    log.info(f"Universe: {len(sp500)} SP500 + {len(ndx100)} NDX100 + {len(sp400)} SP400 + {len(russ2k)} Russell2000 = {len(clean)} unique tickers")
    return clean


# ─────────────────────────────────────────────
# 2. BATCH PRICE DOWNLOAD
#    Chunks of 500 to avoid memory issues
# ─────────────────────────────────────────────
def batch_download_chunk(tickers: list, period: str = "6mo") -> dict:
    """Download one chunk of up to 500 tickers."""
    try:
        raw = yf.download(
            tickers=" ".join(tickers),
            period=period,
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
        result = {}
        if len(tickers) == 1:
            if not raw.empty:
                result[tickers[0]] = raw
        else:
            for t in tickers:
                try:
                    df_t = raw[t].dropna(how="all")
                    if not df_t.empty and len(df_t) > 20:
                        result[t] = df_t
                except KeyError:
                    pass
        return result
    except Exception as e:
        log.warning(f"Chunk download error: {e}")
        return {}


def download_all_prices(tickers: list, period: str = "6mo") -> dict:
    """Download prices for entire universe in chunks of CHUNK_SIZE."""
    all_data = {}
    chunks = [tickers[i:i+CHUNK_SIZE] for i in range(0, len(tickers), CHUNK_SIZE)]

    for i, chunk in enumerate(chunks):
        log.info(f"Downloading price chunk {i+1}/{len(chunks)} ({len(chunk)} tickers)...")
        chunk_data = batch_download_chunk(chunk, period)
        all_data.update(chunk_data)
        time.sleep(1)  # be polite to Yahoo Finance

    log.info(f"Price data downloaded for {len(all_data)} tickers.")
    return all_data


# ─────────────────────────────────────────────
# 3. HH/HL ENGINE
# ─────────────────────────────────────────────
def compute_hh_hl(hist: pd.DataFrame, window: int = HH_HL_WINDOW) -> dict:
    try:
        if hist is None or hist.empty or len(hist) < window * 2:
            return {"hh": False, "hl": False, "trend_structure": "SKIP", "trend_score": 0}

        closes = hist["Close"]
        highs  = hist["High"]
        lows   = hist["Low"]

        # Price filter — ignore penny stocks at scoring stage
        if float(closes.iloc[-1]) < MIN_PRICE:
            return {"hh": False, "hl": False, "trend_structure": "SKIP", "trend_score": 0}

        roll_high = highs.rolling(window).max()
        roll_low  = lows.rolling(window).min()

        n     = len(roll_high.dropna())
        third = max(n // 3, 1)

        rh = roll_high.dropna()
        rl = roll_low.dropna()

        hh = (rh.iloc[2*third:].mean() > rh.iloc[third:2*third].mean() > rh.iloc[:third].mean())
        hl = (rl.iloc[2*third:].mean() > rl.iloc[third:2*third].mean() > rl.iloc[:third].mean())

        current       = float(closes.iloc[-1])
        high_6m       = float(highs.max())
        low_6m        = float(lows.min())
        pct_from_high = ((current - high_6m) / high_6m) * 100
        range_6m_pct  = ((high_6m - low_6m) / low_6m) * 100  # volatility proxy

        if hh and hl:
            structure = "STRONG UPTREND"
            score     = 10 if pct_from_high > -5 else 8
        elif hh and not hl:
            structure = "UPTREND"
            score     = 6
        elif not hh and hl:
            structure = "BASING"
            score     = 4
        else:
            structure = "DOWNTREND"
            score     = 1

        return {
            "hh":               hh,
            "hl":               hl,
            "trend_structure":  structure,
            "trend_score":      score,
            "current_price":    round(current, 2),
            "high_6m":          round(high_6m, 2),
            "low_6m":           round(low_6m, 2),
            "pct_from_6m_high": round(pct_from_high, 1),
            "range_6m_pct":     round(range_6m_pct, 1),
        }
    except Exception as e:
        return {"hh": False, "hl": False, "trend_structure": "ERROR", "trend_score": 0}


# ─────────────────────────────────────────────
# 4. RVOL + VELOCITY COMPUTATION
#    Now computed from price data directly
#    (no scanner CSV needed)
# ─────────────────────────────────────────────
def compute_momentum(hist: pd.DataFrame) -> dict:
    """
    Computes RVOL and Velocity % directly from price history.
    RVOL: last 5 days avg volume vs 60-day avg volume
    Velocity: linear regression slope annualized as % of price
    """
    try:
        if hist is None or hist.empty or len(hist) < 60:
            return {"RVOL": 1.0, "Velocity %": 0.0, "momentum_score": 1}

        vol    = hist["Volume"]
        closes = hist["Close"]

        # RVOL
        recent_vol = vol.iloc[-5:].mean()
        avg_vol    = vol.iloc[-60:].mean()
        rvol       = recent_vol / avg_vol if avg_vol > 0 else 1.0

        # Velocity — slope of log prices over 180 days, annualized
        import numpy as np
        log_prices = np.log(closes.iloc[-min(180, len(closes)):])
        x          = np.arange(len(log_prices))
        slope      = np.polyfit(x, log_prices, 1)[0]  # log-return per day
        velocity   = slope * 252 * 100                  # annualized %

        momentum_score = min(10, max(1, velocity / 50))

        return {
            "RVOL":           round(rvol, 2),
            "Velocity %":     round(velocity, 1),
            "momentum_score": round(momentum_score, 1),
        }
    except Exception as e:
        return {"RVOL": 1.0, "Velocity %": 0.0, "momentum_score": 1}


# ─────────────────────────────────────────────
# 5. FUNDAMENTALS (parallel)
# ─────────────────────────────────────────────
def fetch_fundamentals_single(ticker: str) -> tuple:
    try:
        info          = yf.Ticker(ticker).info
        rev_growth    = info.get("revenueGrowth")
        earnings_gr   = info.get("earningsGrowth")
        profit_margin = info.get("profitMargins")
        pe            = info.get("trailingPE")
        fwd_pe        = info.get("forwardPE")
        mkt_cap       = info.get("marketCap")
        sector        = info.get("sector", "N/A")
        industry      = info.get("industry", "N/A")
        rec           = info.get("recommendationMean")
        short_ratio   = info.get("shortRatio")

        fund_score = 5
        if rev_growth is not None:
            if rev_growth > 0.30:   fund_score += 2
            elif rev_growth > 0.15: fund_score += 1
            elif rev_growth < 0:    fund_score -= 2
        if earnings_gr is not None:
            if earnings_gr > 0.25:  fund_score += 1
            elif earnings_gr < 0:   fund_score -= 1
        if profit_margin is not None:
            if profit_margin > 0.20: fund_score += 1
            elif profit_margin < 0:  fund_score -= 1
        if rec is not None:
            if rec < 2.0:   fund_score += 1
            elif rec > 3.5: fund_score -= 1

        fund_score = max(1, min(10, fund_score))

        return ticker, {
            "sector":              sector,
            "industry":            industry,
            "market_cap":          mkt_cap,
            "rev_growth_pct":      round(rev_growth * 100, 1)    if rev_growth    else None,
            "earnings_growth_pct": round(earnings_gr * 100, 1)   if earnings_gr   else None,
            "profit_margin_pct":   round(profit_margin * 100, 1) if profit_margin else None,
            "trailing_pe":         round(pe, 1)                  if pe            else None,
            "forward_pe":          round(fwd_pe, 1)              if fwd_pe        else None,
            "analyst_rec":         round(rec, 2)                 if rec           else None,
            "short_ratio":         round(short_ratio, 1)         if short_ratio   else None,
            "fund_score":          fund_score,
        }
    except Exception:
        return ticker, {"sector": "N/A", "industry": "N/A", "market_cap": None, "fund_score": 5}


def fetch_all_fundamentals(tickers: list) -> dict:
    results = {}
    total   = len(tickers)
    done    = 0

    with ThreadPoolExecutor(max_workers=MAX_FUND_WORKERS) as executor:
        futures = {executor.submit(fetch_fundamentals_single, t): t for t in tickers}
        for future in as_completed(futures):
            ticker, data = future.result()
            results[ticker] = data
            done += 1
            if done % 100 == 0:
                log.info(f"Fundamentals: {done}/{total}")

    log.info(f"Fundamentals fetched for {len(results)} tickers.")
    return results


# ─────────────────────────────────────────────
# 6. COMPOSITE SCORER
# ─────────────────────────────────────────────
def compute_composite(trend: dict, momentum: dict, fund: dict) -> dict:
    trend_score    = trend.get("trend_score", 1)
    momentum_score = momentum.get("momentum_score", 1)
    rvol           = momentum.get("RVOL", 1.0)
    rvol_score     = min(10, max(1, rvol * 2.5))
    fund_score     = fund.get("fund_score", 5)

    composite = (
        trend_score    * 0.35 +
        momentum_score * 0.25 +
        rvol_score     * 0.20 +
        fund_score     * 0.20
    )

    return {
        "trend_score":    round(trend_score, 1),
        "momentum_score": round(momentum_score, 1),
        "rvol_score":     round(rvol_score, 1),
        "fund_score":     round(fund_score, 1),
        "composite":      round(composite, 1),
    }


# ─────────────────────────────────────────────
# 7. MAIN SCAN PIPELINE
# ─────────────────────────────────────────────
def run_full_scan():
    scan_start = time.time()
    log.info("=" * 60)
    log.info(f"NIGHTLY SCAN STARTED — {TODAY}")
    log.info("=" * 60)

    # Step 1: Build universe
    universe = build_universe()
    if not universe:
        log.error("Failed to build universe. Aborting.")
        return

    # Step 2: Download all prices in chunks
    log.info(f"Downloading prices for {len(universe)} tickers in chunks of {CHUNK_SIZE}...")
    price_data = download_all_prices(universe, period="6mo")

    # Step 3: Quick filter — only process tickers with enough price data
    valid_tickers = [t for t in universe if t in price_data and len(price_data[t]) >= 40]
    log.info(f"{len(valid_tickers)} tickers have sufficient price history.")

    # Step 4: Compute trend + momentum for all (fast — no network)
    log.info("Computing HH/HL + RVOL + Velocity for all tickers...")
    trend_data    = {t: compute_hh_hl(price_data[t])   for t in valid_tickers}
    momentum_data = {t: compute_momentum(price_data[t]) for t in valid_tickers}

    # Step 5: Pre-filter before fetching fundamentals
    # Only fetch fundamentals for stocks that pass trend + basic momentum
    # This cuts the fundamental API calls from 3,500 → ~500-800
    prefilt = [
        t for t in valid_tickers
        if trend_data[t].get("trend_score", 0) >= 4        # at least BASING
        and momentum_data[t].get("Velocity %", 0) >= 10   # some positive momentum
    ]
    log.info(f"{len(prefilt)} tickers pass pre-filter → fetching fundamentals...")

    # Step 6: Fetch fundamentals (parallelised, only for pre-filtered stocks)
    fund_data = fetch_all_fundamentals(prefilt)

    # Step 7: Score + separate into buckets
    main_list   = []   # confirmed uptrend, $500M+
    basing_list = []   # basing + strong fundamentals — future multibaggers
    spec_list   = []   # $100M–$500M speculative

    for t in prefilt:
        trend    = trend_data[t]
        momentum = momentum_data[t]
        fund     = fund_data.get(t, {"sector": "N/A", "fund_score": 5})
        scores   = compute_composite(trend, momentum, fund)
        mkt_cap  = fund.get("market_cap") or 0

        row = {
            "Ticker":  t,
            "Chart":   f"https://www.tradingview.com/symbols/{t}/",
            **trend,
            **momentum,
            **fund,
            **scores,
        }

        structure  = trend.get("trend_structure", "")
        fund_score = fund.get("fund_score", 5)

        # ── Main list: confirmed uptrend, $500M+
        if mkt_cap >= MIN_MKTCAP_MAIN and structure in ("STRONG UPTREND", "UPTREND"):
            main_list.append(row)

        # ── Basing list: not yet uptrend but fundamentally strong
        #    These are your pre-multibagger watchlist
        elif structure == "BASING" and fund_score >= 7:
            basing_list.append(row)

        # ── Speculative: $100M–$500M, any uptrend
        elif MIN_MKTCAP_SPEC <= mkt_cap < MIN_MKTCAP_MAIN and structure in ("STRONG UPTREND", "UPTREND"):
            spec_list.append(row)

    # Step 8: Sort and save
    def save_list(rows, filename, label):
        if not rows:
            log.warning(f"No stocks in {label} list.")
            return
        df = pd.DataFrame(rows).sort_values("composite", ascending=False).reset_index(drop=True)
        df.index += 1
        path = f"data/{filename}"
        df.to_csv(path, index_label="Rank")
        log.info(f"Saved {label}: {len(df)} stocks → {path}")

    save_list(main_list,   "enriched_watchlist.csv",   "Main Uptrend")
    save_list(basing_list, "basing_watchlist.csv",     "Basing Watch")
    save_list(spec_list,   "speculative_watchlist.csv","Speculative")

    # Step 9: Save scan metadata
    elapsed = round(time.time() - scan_start, 1)
    meta = {
        "scan_date":       TODAY,
        "scan_time":       datetime.now().strftime("%H:%M"),
        "universe_size":   len(universe),
        "valid_tickers":   len(valid_tickers),
        "prefiltered":     len(prefilt),
        "main_count":      len(main_list),
        "basing_count":    len(basing_list),
        "spec_count":      len(spec_list),
        "elapsed_seconds": elapsed,
    }
    with open("data/scan_meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    log.info("=" * 60)
    log.info(f"SCAN COMPLETE in {elapsed}s")
    log.info(f"  Main uptrend list : {len(main_list)} stocks")
    log.info(f"  Basing watch list : {len(basing_list)} stocks")
    log.info(f"  Speculative list  : {len(spec_list)} stocks")
    log.info("=" * 60)


if __name__ == "__main__":
    run_full_scan()
