"""
NIGHTLY MARKET SCANNER v4 — FINAL
===================================
Universe source: yfinance screener + hardcoded SP500/NDX/growth lists
No external HTTP calls for universe — zero dependency on Wikipedia or iShares.
Guaranteed to always have a working universe.

Output files:
  data/enriched_watchlist.csv    — confirmed uptrend stocks, $500M+
  data/basing_watchlist.csv      — basing stocks, strong fundamentals
  data/speculative_watchlist.csv — $100M-$500M uptrend stocks
  data/scan_meta.json            — scan stats + timestamp
"""

import os, json, time, logging, re
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
from io import StringIO
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
os.makedirs("data", exist_ok=True)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
CHUNK_SIZE       = 200    # tickers per yf.download batch (smaller = more reliable)
MAX_FUND_WORKERS = 20     # parallel fundamentals threads
MIN_MKTCAP_MAIN  = 500e6  # $500M main list
MIN_MKTCAP_SPEC  = 100e6  # $100M speculative list
MIN_PRICE        = 5.0    # ignore penny stocks
HH_HL_WINDOW     = 20     # rolling window days
TODAY            = datetime.today().strftime("%B %d, %Y")


# ─────────────────────────────────────────────
# 1. UNIVERSE — hardcoded + yfinance screener
#    No external HTTP. Always works.
# ─────────────────────────────────────────────

# S&P 500 — full list hardcoded (as of May 2026)
# This never needs an HTTP call and never fails
SP500 = [
    "MMM","AOS","ABT","ABBV","ACN","ADBE","AMD","AES","AFL","A","APD","ABNB",
    "AKAM","ALB","ARE","ALGN","ALLE","LNT","ALL","GOOGL","GOOG","MO","AMZN",
    "AMCR","AEE","AAL","AEP","AXP","AIG","AMT","AWK","AMP","AME","AMGN","APH",
    "ADI","ANSS","AON","APA","AAPL","AMAT","APTV","ACGL","ADM","ANET","AJG",
    "AIZ","T","ATO","ADSK","ADP","AZO","AVB","AVY","AXON","BKR","BALL","BAC",
    "BK","BBWI","BAX","BDX","BRK-B","BBY","BIO","TECH","BIIB","BLK","BX",
    "BA","BKNG","BWA","BSX","BMY","AVGO","BR","BRO","BF-B","BLDR","CHRW",
    "CDNS","CZR","CPT","CPB","COF","CAH","KMX","CCL","CARR","CTLT","CAT",
    "CBOE","CBRE","CDW","CE","COR","CNC","CNX","CDAY","CF","CRL","SCHW","CHTR",
    "CVX","CMG","CB","CHD","CI","CINF","CTAS","CSCO","C","CFG","CLX","CME",
    "CMS","KO","CTSH","CL","CMCSA","CAG","COP","ED","STZ","CEG","COO","CPRT",
    "GLW","CPAY","CTVA","CSGP","COST","CTRA","CRWD","CCI","CSX","CMI","CVS",
    "DHR","DRI","DVA","DAY","DECK","DE","DELL","DAL","DVN","DXCM","FANG","DLR",
    "DFS","DG","DLTR","D","DPZ","DOV","DOW","DHI","DTE","DUK","DD","EMN","ETN",
    "EBAY","ECL","EIX","EW","EA","ELV","LLY","EMR","ENPH","ETR","EOG","EPAM",
    "EQT","EFX","EQIX","EQR","ESS","EL","ETSY","EG","EVRST","ES","EXC","EXPE",
    "EXPD","EXR","XOM","FFIV","FDS","FICO","FAST","FRT","FDX","FIS","FITB",
    "FSLR","FE","FI","FMC","F","FTNT","FTV","FOXA","FOX","BEN","FCX","GRMN",
    "IT","GE","GEHC","GEV","GEN","GNRC","GD","GIS","GM","GPC","GILD","GS","HAL",
    "HIG","HAS","HCA","DOC","HSIC","HSY","HES","HPE","HLT","HOLX","HD","HON",
    "HRL","HST","HWM","HPQ","HUBB","HUM","HBAN","HII","IBM","IEX","IDXX","ITW",
    "INCY","IR","PODD","INTC","ICE","IFF","IP","IPG","INTU","ISRG","IVZ","INVH",
    "IQV","IRM","JBHT","JBL","JKHY","J","JNJ","JCI","JPM","JNPR","K","KVUE",
    "KDP","KEY","KEYS","KMB","KIM","KMI","KLAC","KHC","KR","LHX","LH","LRCX",
    "LW","LVS","LDOS","LEN","LNC","LIN","LYV","LKQ","LMT","L","LOW","LULU",
    "LYB","MTB","MRO","MPC","MKTX","MAR","MMC","MLM","MAS","MA","MTCH","MKC",
    "MCD","MCK","MDT","MRK","META","MET","MTD","MGM","MCHP","MU","MSFT","MAA",
    "MRNA","MHK","MOH","TAP","MDLZ","MPWR","MNST","MCO","MS","MOS","MSI","MSCI",
    "NDAQ","NTAP","NFLX","NEM","NWSA","NWS","NEE","NKE","NI","NDSN","NSC","NTRS",
    "NOC","NCLH","NRG","NUE","NVDA","NVR","NXPI","ORLY","OXY","ODFL","OMC","ON",
    "OKE","ORCL","OTIS","PCAR","PKG","PLTR","PH","PAYX","PAYC","PYPL","PNR","PEP",
    "PFE","PCG","PM","PSX","PNW","PNC","POOL","PPG","PPL","PFG","PG","PGR","PRU",
    "PLD","PTC","PSA","PHM","QRVO","PWR","QCOM","DGX","RL","RJF","RTX","O","REG",
    "REGN","RF","RSG","RMD","RVTY","ROK","ROL","ROP","ROST","RCL","SPGI","CRM",
    "SBAC","SLB","STX","SRE","NOW","SHW","SPG","SWKS","SJM","SW","SNA","SOLV",
    "SO","LUV","SWK","SBUX","STT","STLD","STE","SYK","SMCI","SYF","SNPS","SYY",
    "TMUS","TROW","TTWO","TPR","TRGP","TGT","TEL","TDY","TFX","TER","TSLA","TXN",
    "TMO","TJX","TSCO","TT","TDG","TRV","TRMB","TFC","TYL","TSN","USB","UBER",
    "UDR","ULTA","UNP","UAL","UPS","URI","UNH","UHS","VLO","VTR","VLTO","VRSN",
    "VRSK","VZ","VRTX","VTRS","VICI","V","VST","WRB","GWW","WAB","WBA","WMT",
    "DIS","WBD","WM","WAT","WEC","WFC","WELL","WST","WDC","WY","WMB","WTW","WYNN",
    "XEL","XYL","YUM","ZBRA","ZBH","ZTS",
]

# Nasdaq 100 extras not in SP500
NDX_EXTRAS = [
    "ADSK","ABNB","MRVL","CRWD","DDOG","PANW","SNPS","CDNS","FTNT","FAST",
    "GEHC","IDXX","ILMN","LULU","MDLZ","MNST","ODFL","ORLY","PCAR","REGN",
    "ROST","SBUX","SGEN","SIRI","TEAM","TMUS","VRSK","VRSN","WBA","XEL",
    "ZS","ZM","DUOL","OKTA","NET","MDB","SNOW","DDOG","GTLB","HUBS","BILL",
]

# High-growth / AI / momentum stocks not always in indices
GROWTH_EXTRAS = [
    "NVDA","MRVL","AVGO","AMD","AMAT","LRCX","KLAC","ASML","TSM","ARM",
    "CRDO","SMCI","CIEN","COHR","MTSI","ONTO","FORM","ACLS","WOLF","AMBA",
    "AXON","PODD","DXCM","ISRG","IRTC","INMD","NVCR","RXRX","PCVX","ALNY",
    "CELH","MNDY","GTLB","SEMR","IONQ","RGTI","QUBT","ACHR","RKLB","ASTS",
    "LUNR","RDW","SPCE","ASTR","BWXT","CW","HEI","KTOS","LDOS","MOOG",
    "FTAI","GEV","VST","CEG","NRG","TALEN","OKLO","SMR","BWXT","NNE",
    "SOFI","AFRM","UPST","LC","OPEN","HOOD","COIN","MARA","RIOT","CLSK",
    "CAVA","SHAK","BROS","WING","TXRH","BJRI","EAT","DPZ","CMG","YUM",
    "OVV","FANG","NOG","CIVI","MTDR","SM","CHRD","MGY","VTLE","TALO",
    "WFRD","RIG","VAL","PTEN","HP","NINE","STEP","KFRC","MELI","SE",
    "NU","STNE","GLOB","ARCO","BSAC","IQ","GRAB","SEA","SHOP","ETSY",
    "W","XPOF","MODG","GOLF","BOWLERO","PLYA","SIX","TNL","VAXX",
    "IBKR","LPLA","SNSXX","PIPR","SF","COWN","JEF","LAZ","EVR","PJT",
]

# Small/mid cap momentum names
SMALLMID_EXTRAS = [
    "ALOT","CXDO","UCTT","NVEC","ACMR","AEHR","AMKR","AMBA","ATRC","AVAV",
    "AVPT","AXNX","AZEK","BANF","BCPC","BFST","BLBD","BLOOM","BMBL","BOOT",
    "BROS","CABA","CALM","CARG","CATX","CATO","CBRL","CENT","CENTA","CHUY",
    "CLAR","CLOV","CLRB","CMCO","COHU","COUR","CPRX","CRSR","CRVL","CSTM",
    "CTSO","CURV","DAKT","DBRG","DFIN","DGII","DLO","DMRC","DOCN","DOMO",
    "DXPE","EGHT","ELME","EMBC","EPAC","ERII","ETON","EVTC","EXTR","EZPW",
    "FBRT","FCNCA","FELE","FFIN","FMBH","FNKO","FORM","FOUR","FOXF","FRSH",
    "FTDR","FULT","GKOS","GLDD","GMED","GNOG","GOEV","GPRO","GRFS","GRPN",
    "GRTS","GTLS","GXII","HAYW","HCKT","HEES","HLIO","HMST","HONE","HOPE",
    "HRMY","HROW","HSTM","HTLF","HUBG","HWKN","IDCC","IHRT","IMNM","IMVT",
    "INDB","INFN","INFU","INMD","INSW","IOSP","IPAR","IPWR","IRMD","ITRI",
    "ITOS","JACK","JAMF","JBSS","JELD","JOUT","KALU","KFRC","KIDS","KLIC",
    "KNF","KNSL","KOPN","KROS","KRYS","KTOS","KVHI","LADR","LAKE","LANC",
    "LAWS","LAZR","LBAI","LCII","LCNB","LCUT","LGND","LKFN","LMAT","LMNR",
]

def build_universe() -> list:
    """
    Priority 1: Read data/tickers.csv — generated weekly by fetch_universe.py
                This gives the full US market (~8,000-10,000 stocks)
    Priority 2: Fall back to hardcoded list if CSV missing or too small
                This gives ~750 quality stocks — always works
    """
    # Try the full universe CSV first
    csv_path = "data/tickers.csv"
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
            if "ticker" in df.columns:
                tickers = df["ticker"].dropna().astype(str).str.upper().str.strip().tolist()
                if len(tickers) >= 100:
                    log.info(f"Loaded {len(tickers)} tickers from {csv_path}")
                    return tickers
        except Exception as e:
            log.warning(f"Could not read {csv_path}: {e}")

    # Fallback to hardcoded list
    log.warning("tickers.csv missing or too small — using hardcoded universe. Run fetch_universe.py to get full market.")
    all_tickers = SP500 + NDX_EXTRAS + GROWTH_EXTRAS + SMALLMID_EXTRAS

    seen, clean = set(), []
    for t in all_tickers:
        t = str(t).strip().upper()
        if not t or t in seen:
            continue
        if len(t) > 6:
            continue
        seen.add(t)
        clean.append(t)

    log.info(f"Hardcoded universe: {len(clean)} tickers")
    return clean


# ─────────────────────────────────────────────
# 2. BATCH PRICE DOWNLOAD
# ─────────────────────────────────────────────
def batch_download_chunk(tickers: list, period: str = "6mo") -> dict:
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
    all_data = {}
    chunks = [tickers[i:i+CHUNK_SIZE] for i in range(0, len(tickers), CHUNK_SIZE)]
    for i, chunk in enumerate(chunks):
        log.info(f"Price chunk {i+1}/{len(chunks)} ({len(chunk)} tickers)...")
        all_data.update(batch_download_chunk(chunk, period))
        time.sleep(0.5)
    log.info(f"Price data: {len(all_data)} tickers downloaded")
    return all_data


# ─────────────────────────────────────────────
# 3. HH/HL ENGINE
# ─────────────────────────────────────────────
def compute_hh_hl(hist: pd.DataFrame) -> dict:
    try:
        if hist is None or hist.empty or len(hist) < HH_HL_WINDOW * 2:
            return {"hh": False, "hl": False, "trend_structure": "SKIP", "trend_score": 0}

        closes = hist["Close"]
        highs  = hist["High"]
        lows   = hist["Low"]

        if float(closes.iloc[-1]) < MIN_PRICE:
            return {"hh": False, "hl": False, "trend_structure": "SKIP", "trend_score": 0}

        roll_high = highs.rolling(HH_HL_WINDOW).max()
        roll_low  = lows.rolling(HH_HL_WINDOW).min()
        n     = len(roll_high.dropna())
        third = max(n // 3, 1)

        rh = roll_high.dropna()
        rl = roll_low.dropna()

        hh = float(rh.iloc[2*third:].mean()) > float(rh.iloc[third:2*third].mean()) > float(rh.iloc[:third].mean())
        hl = float(rl.iloc[2*third:].mean()) > float(rl.iloc[third:2*third].mean()) > float(rl.iloc[:third].mean())

        current       = float(closes.iloc[-1])
        high_6m       = float(highs.max())
        low_6m        = float(lows.min())
        pct_from_high = ((current - high_6m) / high_6m) * 100

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
            "hh": hh, "hl": hl,
            "trend_structure":  structure,
            "trend_score":      score,
            "current_price":    round(current, 2),
            "high_6m":          round(high_6m, 2),
            "low_6m":           round(low_6m, 2),
            "pct_from_6m_high": round(pct_from_high, 1),
        }
    except Exception as e:
        return {"hh": False, "hl": False, "trend_structure": "ERROR", "trend_score": 0}


# ─────────────────────────────────────────────
# 4. RVOL + VELOCITY
# ─────────────────────────────────────────────
def compute_momentum(hist: pd.DataFrame) -> dict:
    try:
        if hist is None or hist.empty or len(hist) < 60:
            return {"RVOL": 1.0, "Velocity %": 0.0, "momentum_score": 1}

        vol    = hist["Volume"]
        closes = hist["Close"]

        recent_vol = float(vol.iloc[-5:].mean())
        avg_vol    = float(vol.iloc[-60:].mean())
        rvol       = recent_vol / avg_vol if avg_vol > 0 else 1.0

        log_prices = np.log(closes.iloc[-min(180, len(closes)):].astype(float))
        x          = np.arange(len(log_prices))
        slope      = np.polyfit(x, log_prices, 1)[0]
        velocity   = slope * 252 * 100

        momentum_score = min(10, max(1, velocity / 50))

        return {
            "RVOL":           round(rvol, 2),
            "Velocity %":     round(velocity, 1),
            "momentum_score": round(momentum_score, 1),
        }
    except:
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
            if rev_growth > 0.30:    fund_score += 2
            elif rev_growth > 0.15:  fund_score += 1
            elif rev_growth < 0:     fund_score -= 2
        if earnings_gr is not None:
            if earnings_gr > 0.25:   fund_score += 1
            elif earnings_gr < 0:    fund_score -= 1
        if profit_margin is not None:
            if profit_margin > 0.20: fund_score += 1
            elif profit_margin < 0:  fund_score -= 1
        if rec is not None:
            if rec < 2.0:            fund_score += 1
            elif rec > 3.5:          fund_score -= 1
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
    except:
        return ticker, {"sector": "N/A", "industry": "N/A", "market_cap": None, "fund_score": 5}


def fetch_all_fundamentals(tickers: list) -> dict:
    results = {}
    done    = 0
    with ThreadPoolExecutor(max_workers=MAX_FUND_WORKERS) as executor:
        futures = {executor.submit(fetch_fundamentals_single, t): t for t in tickers}
        for future in as_completed(futures):
            ticker, data = future.result()
            results[ticker] = data
            done += 1
            if done % 100 == 0:
                log.info(f"Fundamentals: {done}/{len(tickers)}")
    log.info(f"Fundamentals done: {len(results)} tickers")
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
# 7. MAIN SCAN
# ─────────────────────────────────────────────
def run_full_scan():
    t0 = time.time()
    log.info("=" * 60)
    log.info(f"NIGHTLY SCAN STARTED — {TODAY}")
    log.info("=" * 60)

    # Step 1: Universe (always works — hardcoded)
    universe = build_universe()

    # Step 2: Batch price download
    log.info(f"Downloading prices for {len(universe)} tickers...")
    price_data = download_all_prices(universe, period="6mo")

    # Step 3: Compute trend + momentum (no network — fast)
    log.info("Computing HH/HL + momentum...")
    valid = [t for t in universe if t in price_data and len(price_data[t]) >= 40]
    log.info(f"{len(valid)} tickers have sufficient price history")

    trend_data    = {t: compute_hh_hl(price_data[t])   for t in valid}
    momentum_data = {t: compute_momentum(price_data[t]) for t in valid}

    # Step 4: Pre-filter before fundamentals (saves API calls)
    prefilt = [
        t for t in valid
        if trend_data[t].get("trend_score", 0) >= 4
        and momentum_data[t].get("Velocity %", 0) >= 5
    ]
    log.info(f"{len(prefilt)} pass pre-filter → fetching fundamentals...")

    # Step 5: Fundamentals (parallel)
    fund_data = fetch_all_fundamentals(prefilt)

    # Step 6: Score + bucket
    main_list, basing_list, spec_list = [], [], []

    for t in prefilt:
        trend    = trend_data[t]
        momentum = momentum_data[t]
        fund     = fund_data.get(t, {"sector": "N/A", "fund_score": 5})
        scores   = compute_composite(trend, momentum, fund)
        mkt_cap  = fund.get("market_cap") or 0

        row = {
            "Ticker":  t,
            "Chart":   f"https://www.tradingview.com/symbols/{t}/",
            **trend, **momentum, **fund, **scores,
        }

        structure  = trend.get("trend_structure", "")
        fund_score = fund.get("fund_score", 5)

        if mkt_cap >= MIN_MKTCAP_MAIN and structure in ("STRONG UPTREND", "UPTREND"):
            main_list.append(row)
        elif structure == "BASING" and fund_score >= 7:
            basing_list.append(row)
        elif MIN_MKTCAP_SPEC <= mkt_cap < MIN_MKTCAP_MAIN and structure in ("STRONG UPTREND", "UPTREND"):
            spec_list.append(row)

    # Step 7: Save
    def save(rows, fname, label):
        if not rows:
            log.warning(f"No stocks in {label}")
            return
        df = pd.DataFrame(rows).sort_values("composite", ascending=False).reset_index(drop=True)
        df.index += 1
        df.to_csv(f"data/{fname}", index_label="Rank")
        log.info(f"Saved {label}: {len(df)} stocks → data/{fname}")

    save(main_list,   "enriched_watchlist.csv",    "Main Uptrend")
    save(basing_list, "basing_watchlist.csv",      "Basing Watch")
    save(spec_list,   "speculative_watchlist.csv", "Speculative")

    elapsed = round(time.time() - t0, 1)
    meta = {
        "scan_date":       TODAY,
        "scan_time":       datetime.now().strftime("%H:%M"),
        "universe_size":   len(universe),
        "valid_tickers":   len(valid),
        "prefiltered":     len(prefilt),
        "main_count":      len(main_list),
        "basing_count":    len(basing_list),
        "spec_count":      len(spec_list),
        "elapsed_seconds": elapsed,
    }
    with open("data/scan_meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    log.info("=" * 60)
    log.info(f"DONE in {elapsed}s | Main: {len(main_list)} | Basing: {len(basing_list)} | Spec: {len(spec_list)}")
    log.info("=" * 60)


if __name__ == "__main__":
    run_full_scan()
