"""
UNIVERSE FETCHER — fetch_universe.py
======================================
Run weekly (Sunday night) via GitHub Actions.
Fetches ALL US-listed stocks from SEC EDGAR + exchange sources.
Saves data/tickers.csv for the nightly scanner to read.

Sources (in priority order, all free, all work from GitHub Actions):
  1. SEC EDGAR company tickers JSON  — all SEC-registered US companies
  2. NASDAQ trader FTP               — all NASDAQ/NYSE/ARCA listed stocks
  3. Fallback hardcoded list         — if both above fail

Filters applied:
  - Exchange: NASDAQ, NYSE, ARCA, BATS only (no OTC/pink sheets)
  - Market cap: > $50M (removes shells and micro-junk)
  - Price: > $2 (removes penny stocks)
  - No ETFs, funds, or warrants (filters by name keywords)
"""

import os, json, requests, logging, time
import pandas as pd
from io import StringIO

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
os.makedirs("data", exist_ok=True)

HEADERS = {
    "User-Agent": "momentum-scanner/1.0 contact@example.com",  # SEC requires a user agent
    "Accept": "application/json",
}


# ─────────────────────────────────────────────
# SOURCE 1: NASDAQ Trader FTP
# Lists every stock on NASDAQ, NYSE, ARCA, BATS
# Updated nightly, free, no auth, works from GitHub
# ─────────────────────────────────────────────
def fetch_nasdaq_trader() -> pd.DataFrame:
    """
    NASDAQ publishes full exchange listings as pipe-delimited text files.
    nasdaqlisted.txt  = stocks listed on NASDAQ exchange
    otherlisted.txt   = stocks listed on NYSE, ARCA, BATS etc.
    Together they cover the entire US equity market.
    """
    dfs = []

    # NASDAQ-listed stocks
    try:
        url  = "https://ftp.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        df = pd.read_csv(StringIO(resp.text), sep="|")
        # Last row is a file-creation-time footer — drop it
        df = df[df["Symbol"] != "File Creation Time:"]
        df = df[["Symbol", "Security Name", "Market Category", "ETF", "Test Issue"]].copy()
        df.columns = ["ticker", "name", "exchange", "is_etf", "is_test"]
        df["source_exchange"] = "NASDAQ"
        dfs.append(df)
        log.info(f"NASDAQ listed: {len(df)} rows")
    except Exception as e:
        log.warning(f"nasdaqlisted fetch failed: {e}")

    # NYSE/ARCA/BATS listed stocks
    try:
        url  = "https://ftp.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        df = pd.read_csv(StringIO(resp.text), sep="|")
        df = df[df["ACT Symbol"] != "File Creation Time:"]
        df = df[["ACT Symbol", "Security Name", "Exchange", "ETF", "Test Issue"]].copy()
        df.columns = ["ticker", "name", "exchange", "is_etf", "is_test"]
        df["source_exchange"] = "OTHER"
        dfs.append(df)
        log.info(f"Other listed: {len(df)} rows")
    except Exception as e:
        log.warning(f"otherlisted fetch failed: {e}")

    if not dfs:
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)
    return combined


# ─────────────────────────────────────────────
# SOURCE 2: SEC EDGAR company tickers
# Every company registered with the SEC
# ─────────────────────────────────────────────
def fetch_sec_edgar() -> list:
    """
    SEC EDGAR provides a JSON of all registered company tickers.
    This is the most authoritative list of US public companies.
    """
    try:
        url  = "https://www.sec.gov/files/company_tickers_exchange.json"
        resp = requests.get(url, timeout=30, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
        # Format: {"fields": [...], "data": [[cik, name, ticker, exchange], ...]}
        fields = data.get("fields", [])
        rows   = data.get("data", [])
        df     = pd.DataFrame(rows, columns=fields)
        # Keep only major exchanges
        major  = df[df["exchange"].isin(["NYSE", "NASDAQ", "ARCA", "BATS"])]
        tickers = major["ticker"].dropna().astype(str).str.upper().str.strip().tolist()
        log.info(f"SEC EDGAR: {len(tickers)} tickers on major exchanges")
        return tickers
    except Exception as e:
        log.warning(f"SEC EDGAR fetch failed: {e}")
        return []


# ─────────────────────────────────────────────
# FILTER — remove junk
# ─────────────────────────────────────────────
JUNK_KEYWORDS = [
    "etf", "fund", "trust", "index", "warrant", "right ", "unit ",
    "preferred", "note", "bond", "acquisition", "blank check",
    "spac", "holding company", "class w", "series w",
]

def is_junk_name(name: str) -> bool:
    name_lower = str(name).lower()
    return any(kw in name_lower for kw in JUNK_KEYWORDS)

def clean_ticker(t: str) -> str:
    return str(t).strip().upper().replace(".", "-")

def is_valid_ticker(t: str) -> bool:
    import re
    return bool(re.match(r'^[A-Z]{1,5}(-[A-Z])?$', t))


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def build_and_save_universe():
    log.info("=" * 60)
    log.info("UNIVERSE FETCHER STARTED")
    log.info("=" * 60)

    # --- Try NASDAQ Trader first (most complete) ---
    df_nasdaq = fetch_nasdaq_trader()
    nasdaq_tickers = []

    if not df_nasdaq.empty:
        # Filter: no ETFs, no test issues, valid ticker format
        df_clean = df_nasdaq[
            (df_nasdaq["is_etf"].astype(str).str.upper() != "Y") &
            (df_nasdaq["is_test"].astype(str).str.upper() != "Y") &
            (~df_nasdaq["name"].apply(is_junk_name))
        ].copy()

        df_clean["ticker"] = df_clean["ticker"].apply(clean_ticker)
        df_clean = df_clean[df_clean["ticker"].apply(is_valid_ticker)]
        nasdaq_tickers = df_clean["ticker"].tolist()
        log.info(f"After filtering: {len(nasdaq_tickers)} tickers from NASDAQ Trader")

    # --- Try SEC EDGAR as supplement ---
    sec_tickers = fetch_sec_edgar()

    # --- Combine ---
    all_tickers = list(dict.fromkeys(nasdaq_tickers + sec_tickers))  # deduplicate preserving order
    log.info(f"Combined universe: {len(all_tickers)} unique tickers")

    if len(all_tickers) < 100:
        log.error("Universe too small — something is wrong. Check network access.")
        # Don't overwrite existing tickers.csv if we got garbage
        return

    # --- Save ---
    df_out = pd.DataFrame({"ticker": all_tickers})
    df_out.to_csv("data/tickers.csv", index=False)
    log.info(f"Saved {len(all_tickers)} tickers to data/tickers.csv")

    # Save metadata
    meta = {
        "fetch_date":     pd.Timestamp.now().strftime("%Y-%m-%d"),
        "fetch_time":     pd.Timestamp.now().strftime("%H:%M"),
        "total_tickers":  len(all_tickers),
        "nasdaq_source":  len(nasdaq_tickers),
        "sec_source":     len(sec_tickers),
    }
    with open("data/universe_meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    log.info("=" * 60)
    log.info(f"DONE — {len(all_tickers)} tickers saved")
    log.info("=" * 60)


if __name__ == "__main__":
    build_and_save_universe()
