"""
Fetches every configured series from FRED and Yahoo Finance, computes derived
series (spreads, ratios, YoY growth rates), builds a rules-based narrative
summary, and writes data/all_series.json.

Runs inside .github/workflows/update-data.yml on GitHub Actions, which has
normal unrestricted internet access.

Local test run (optional):
    pip install requests
    python fetch_data.py
"""

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

# Yahoo Finance's chart endpoint 429s any request that looks scripted (including
# requests' default "python-requests/x.x" User-Agent) -- a browser UA is required.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# ---------------------------------------------------------------------------
# SERIES -- organized by the story chapter they belong to, not a filing-cabinet
# category. Add a line here to track something new.
# ---------------------------------------------------------------------------
# src: "fred"  -> id is a FRED series ID
# src: "yahoo" -> id is a Yahoo Finance ticker

SERIES = [
    # ---- Growth & business cycle: is the economy expanding or slowing? ----
    dict(id="GDPC1",       name="Real GDP",                              cat="Growth & business cycle", src="fred", fmt="usd", unit="B", dec=0),
    dict(id="A191RL1Q225SBEA", name="Real GDP growth rate (QoQ, annualized)", cat="Growth & business cycle", src="fred", fmt="pct", dec=1),
    dict(id="INDPRO",      name="Industrial production index",          cat="Growth & business cycle", src="fred", fmt="index", dec=1),
    dict(id="TCU",         name="Capacity utilization",                  cat="Growth & business cycle", src="fred", fmt="pct", dec=1),
    dict(id="DGORDER",     name="Durable goods new orders",             cat="Growth & business cycle", src="fred", fmt="usd", unit="M", dec=0),
    dict(id="NEWORDER",    name="Core capital goods orders",            cat="Growth & business cycle", src="fred", fmt="usd", unit="M", dec=0),
    dict(id="BUSINV",      name="Business inventories",                  cat="Growth & business cycle", src="fred", fmt="usd", unit="M", dec=0),
    dict(id="USSLIND",     name="Leading economic index",               cat="Growth & business cycle", src="fred", fmt="index", dec=2),
    dict(id="USPHCI",      name="Coincident economic index",            cat="Growth & business cycle", src="fred", fmt="index", dec=1),
    dict(id="CP",          name="Corporate profits",                    cat="Growth & business cycle", src="fred", fmt="usd", unit="B", dec=0),
    dict(id="RECPROUSM156N", name="Recession probability model",        cat="Growth & business cycle", src="fred", fmt="pct", dec=1),

    # ---- Labor market: is hiring holding up or cracking? ----
    dict(id="UNRATE",      name="Unemployment rate",                    cat="Labor market", src="fred", fmt="pct", dec=1),
    dict(id="CIVPART",     name="Labor participation rate",             cat="Labor market", src="fred", fmt="pct", dec=1),
    dict(id="PAYEMS",      name="Nonfarm payrolls",                     cat="Labor market", src="fred", fmt="number", scale=0.001, unit="M", dec=1),
    dict(id="ICSA",        name="Initial jobless claims",               cat="Labor market", src="fred", fmt="number", dec=0),
    dict(id="CCSA",        name="Continuing jobless claims",            cat="Labor market", src="fred", fmt="number", dec=0),
    dict(id="JTSJOL",      name="Job openings (JOLTS)",                cat="Labor market", src="fred", fmt="number", unit="K", dec=0),
    dict(id="JTSQUR",      name="Quits rate (JOLTS)",                  cat="Labor market", src="fred", fmt="pct", dec=1),
    dict(id="CES0500000003", name="Average hourly earnings",           cat="Labor market", src="fred", fmt="usd", dec=2),
    dict(id="U6RATE",      name="Underemployment rate (U-6)",          cat="Labor market", src="fred", fmt="pct", dec=1),
    dict(id="EMRATIO",     name="Employment-to-population ratio",      cat="Labor market", src="fred", fmt="pct", dec=1),

    # ---- Consumer health: is the household sector still spending? ----
    dict(id="RSAFS",       name="Retail sales",                        cat="Consumer health", src="fred", fmt="usd", unit="M", dec=0),
    dict(id="PI",          name="Personal income",                     cat="Consumer health", src="fred", fmt="usd", unit="B", dec=0),
    dict(id="PCE",         name="Personal consumption expenditures",   cat="Consumer health", src="fred", fmt="usd", unit="B", dec=0),
    dict(id="PSAVERT",     name="Personal savings rate",               cat="Consumer health", src="fred", fmt="pct", dec=1),
    dict(id="TOTALSL",     name="Total consumer credit",               cat="Consumer health", src="fred", fmt="usd", unit="B", dec=0),
    dict(id="DRCCLACBS",   name="Credit card delinquency rate",        cat="Consumer health", src="fred", fmt="pct", dec=2),
    dict(id="UMCSENT",     name="Consumer sentiment (UMich)",          cat="Consumer health", src="fred", fmt="index", dec=1),

    # ---- Housing: the most rate-sensitive part of the economy ----
    dict(id="HOUST",       name="Housing starts",                      cat="Housing", src="fred", fmt="number", unit="K", dec=0),
    dict(id="PERMIT",      name="Building permits",                     cat="Housing", src="fred", fmt="number", unit="K", dec=0),
    dict(id="EXHOSLUSM495S", name="Existing home sales",               cat="Housing", src="fred", fmt="number", unit="K", dec=0),
    dict(id="HSN1F",       name="New single-family home sales",        cat="Housing", src="fred", fmt="number", unit="K", dec=0),
    dict(id="CSUSHPISA",   name="Case-Shiller home price index",       cat="Housing", src="fred", fmt="index", dec=1),
    dict(id="MORTGAGE30US", name="30-year mortgage rate",              cat="Housing", src="fred", fmt="pct", dec=2),
    dict(id="RHORUSQ156N", name="Homeownership rate",                  cat="Housing", src="fred", fmt="pct", dec=1),
    dict(id="MSPUS",       name="Median home sale price",               cat="Housing", src="fred", fmt="usd", dec=0),
    dict(id="MSACSR",      name="Months' supply of houses",            cat="Housing", src="fred", fmt="number", dec=1),
    dict(id="CUSR0000SAH1", name="Shelter / rent CPI",                 cat="Housing", src="fred", fmt="index", dec=1),

    # ---- Inflation: is the price problem resolved? ----
    dict(id="CPIAUCSL",    name="CPI (all urban)",                     cat="Inflation", src="fred", fmt="index", dec=1),
    dict(id="CPILFESL",    name="Core CPI (ex food & energy)",         cat="Inflation", src="fred", fmt="index", dec=1),
    dict(id="PCEPI",       name="PCE price index",                     cat="Inflation", src="fred", fmt="index", dec=1),
    dict(id="PCEPILFE",    name="Core PCE (Fed's preferred gauge)",    cat="Inflation", src="fred", fmt="index", dec=1),
    dict(id="PPIACO",      name="Producer price index",                cat="Inflation", src="fred", fmt="index", dec=1),
    dict(id="IR",          name="Import price index",                  cat="Inflation", src="fred", fmt="index", dec=1),
    dict(id="CORESTICKM159SFRBATL", name="Sticky-price CPI",           cat="Inflation", src="fred", fmt="pct", dec=1),
    dict(id="T5YIE",       name="5-year breakeven inflation",          cat="Inflation", src="fred", fmt="pct", dec=2),
    dict(id="T10YIE",      name="10-year breakeven inflation",         cat="Inflation", src="fred", fmt="pct", dec=2),

    # ---- Monetary policy & rates: what is the Fed doing, what does the curve say? ----
    dict(id="FEDFUNDS",    name="Fed funds rate",                      cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="DGS10",       name="10-year treasury yield",              cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="DGS2",        name="2-year treasury yield",               cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="DGS3MO",      name="3-month treasury yield",              cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="T10Y2Y",      name="2s10s yield curve spread",            cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="T10Y3M",      name="10Y-3M yield curve spread",           cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="DFII10",      name="Real 10-year yield (TIPS)",           cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="WALCL",       name="Fed balance sheet, total assets",     cat="Monetary policy & rates", src="fred", fmt="usd", unit="M", dec=0),
    dict(id="M2SL",        name="M2 money supply",                     cat="Monetary policy & rates", src="fred", fmt="usd", unit="B", dec=0),
    dict(id="SOFR",        name="SOFR overnight rate",                 cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="DTWEXBGS",    name="US dollar index (broad)",             cat="Monetary policy & rates", src="fred", fmt="index", dec=2),
    dict(id="DEXUSEU",     name="USD / EUR",                           cat="Monetary policy & rates", src="fred", fmt="number", dec=4),

    # ---- Credit conditions: is financing getting harder to find? ----
    dict(id="BAMLH0A0HYM2", name="High-yield credit spread",          cat="Credit conditions", src="fred", fmt="pct", dec=2),
    dict(id="BAMLC0A0CM",  name="Investment-grade credit spread",     cat="Credit conditions", src="fred", fmt="pct", dec=2),
    dict(id="DAAA",        name="Moody's Aaa corporate yield",        cat="Credit conditions", src="fred", fmt="pct", dec=2),
    dict(id="DBAA",        name="Moody's Baa corporate yield",        cat="Credit conditions", src="fred", fmt="pct", dec=2),
    dict(id="DRTSCILM",    name="Bank lending standards (net % tightening)", cat="Credit conditions", src="fred", fmt="number", dec=1),
    dict(id="BUSLOANS",    name="Commercial & industrial loans outstanding", cat="Credit conditions", src="fred", fmt="usd", unit="B", dec=0),
    dict(id="STLFSI4",     name="St. Louis Fed financial stress index", cat="Credit conditions", src="fred", fmt="index", dec=2),
    dict(id="NFCI",        name="Chicago Fed financial conditions index", cat="Credit conditions", src="fred", fmt="index", dec=2),

    # ---- Markets & risk sentiment ----
    dict(id="^GSPC",       name="S&P 500",                             cat="Markets & risk sentiment", src="yahoo", fmt="number", dec=2),
    dict(id="^DJI",        name="Dow Jones industrial average",       cat="Markets & risk sentiment", src="yahoo", fmt="number", dec=2),
    dict(id="^IXIC",       name="Nasdaq Composite",                    cat="Markets & risk sentiment", src="yahoo", fmt="number", dec=2),
    dict(id="^RUT",        name="Russell 2000",                        cat="Markets & risk sentiment", src="yahoo", fmt="number", dec=2),
    dict(id="VIXCLS",      name="VIX (volatility index)",              cat="Markets & risk sentiment", src="fred", fmt="index", dec=2),
    dict(id="GC=F",        name="Gold",                                 cat="Markets & risk sentiment", src="yahoo", fmt="usd", unit="/oz", dec=2),
    dict(id="SI=F",        name="Silver",                               cat="Markets & risk sentiment", src="yahoo", fmt="usd", unit="/oz", dec=2),
    dict(id="BTC-USD",     name="Bitcoin",                              cat="Markets & risk sentiment", src="yahoo", fmt="usd", dec=0),
    dict(id="ETH-USD",     name="Ethereum",                             cat="Markets & risk sentiment", src="yahoo", fmt="usd", dec=2),
    dict(id="KNX",         name="KNX stock price",                     cat="Markets & risk sentiment", src="yahoo", fmt="usd", dec=2),
    dict(id="VNQ",         name="REIT index proxy (Vanguard VNQ)",     cat="Markets & risk sentiment", src="yahoo", fmt="usd", dec=2),

    # ---- Trade & global ----
    dict(id="BOPGSTB",     name="Trade balance",                       cat="Trade & global", src="fred", fmt="usd", unit="M", dec=0),
    dict(id="EXPGS",       name="Exports of goods & services",         cat="Trade & global", src="fred", fmt="usd", unit="B", dec=0),
    dict(id="IMPGS",       name="Imports of goods & services",         cat="Trade & global", src="fred", fmt="usd", unit="B", dec=0),
    dict(id="DEXJPUS",     name="USD / JPY",                           cat="Trade & global", src="fred", fmt="number", dec=2),
    dict(id="DEXCHUS",     name="USD / CNY",                           cat="Trade & global", src="fred", fmt="number", dec=3),
    dict(id="USEPUINDXD",  name="US economic policy uncertainty",      cat="Trade & global", src="fred", fmt="index", dec=1),
    dict(id="GEPUCURRENT", name="Global economic policy uncertainty",  cat="Trade & global", src="fred", fmt="index", dec=1),

    # ---- Commodities & energy ----
    dict(id="DCOILWTICO",  name="Crude oil (WTI)",                     cat="Commodities & energy", src="fred", fmt="usd", unit="/bbl", dec=2),
    dict(id="DCOILBRENTEU", name="Crude oil (Brent)",                  cat="Commodities & energy", src="fred", fmt="usd", unit="/bbl", dec=2),
    dict(id="GASREGW",     name="Retail gas price",                    cat="Commodities & energy", src="fred", fmt="usd", unit="/gal", dec=2),
    dict(id="DHHNGSP",     name="Natural gas (Henry Hub)",             cat="Commodities & energy", src="fred", fmt="usd", unit="/MMBtu", dec=2),
    dict(id="PCOPPUSDM",   name="Copper",                               cat="Commodities & energy", src="fred", fmt="usd", unit="/mt", dec=0),
    dict(id="PL=F",        name="Platinum",                             cat="Commodities & energy", src="yahoo", fmt="usd", unit="/oz", dec=2),

    # ---- Demographics & structural ----
    dict(id="POPTHM",      name="US population",                       cat="Demographics & structural", src="fred", fmt="number", scale=0.001, unit="M", dec=1),
    dict(id="LFWA64TTUSM647S", name="Working-age population",          cat="Demographics & structural", src="fred", fmt="number", scale=0.001, unit="M", dec=1),
    dict(id="OPHNFB",      name="Labor productivity",                  cat="Demographics & structural", src="fred", fmt="index", dec=1),
    dict(id="ULCNFB",      name="Unit labor costs",                    cat="Demographics & structural", src="fred", fmt="index", dec=1),

    # ---- Private-markets-adjacent proxies (no direct PE/VC data is public) ----
    dict(id="IPO",         name="IPO market proxy (Renaissance IPO ETF)", cat="Private markets signals", src="yahoo", fmt="usd", dec=2),
]

# Derived series -- computed after the raw fetch, using simple math on one or two series
DERIVED = [
    dict(op="subtract", id="BAA_AAA_SPREAD", name="Baa-Aaa corporate bond spread",
         cat="Credit conditions", a="DBAA", b="DAAA", fmt="pct", dec=2),
    dict(op="subtract", id="BRENT_WTI_SPREAD", name="Brent-WTI oil spread",
         cat="Commodities & energy", a="DCOILBRENTEU", b="DCOILWTICO", fmt="usd", unit="/bbl", dec=2),
    dict(op="divide100", id="SMALLCAP_LARGECAP_RATIO", name="Small-cap / large-cap ratio (Russell 2000 vs S&P 500)",
         cat="Private markets signals", a="^RUT", b="^GSPC", fmt="number", dec=2),
    dict(op="yoy_series", id="M2_YOY_GROWTH", name="M2 money supply growth (YoY)",
         cat="Private markets signals", a="M2SL", fmt="pct", dec=1),
]

RECESSION_SERIES_ID = "USREC"


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def fetch_fred(series_id):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    resp = requests.get(url, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    reader = csv.reader(io.StringIO(resp.text))
    rows = list(reader)[1:]
    out = []
    for r in rows:
        if len(r) < 2 or r[1] in ("", "."):
            continue
        try:
            out.append([r[0], float(r[1])])
        except ValueError:
            continue
    return out


def fetch_yahoo(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
    resp = requests.get(url, params={"range": "max", "interval": "1d"}, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    out = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        out.append([date, float(close)])
    return out


# ---------------------------------------------------------------------------
# Derived series math
# ---------------------------------------------------------------------------

def _to_date(s):
    return datetime.strptime(s, "%Y-%m-%d")


def compute_derived(output, d):
    try:
        if d["op"] in ("subtract", "divide100"):
            a_entry = output["series"].get(d["a"])
            b_entry = output["series"].get(d["b"])
            if not a_entry or not b_entry or a_entry["error"] or b_entry["error"]:
                raise ValueError("component series unavailable")
            a_pts = {row[0]: row[1] for row in a_entry["points"]}
            b_pts = {row[0]: row[1] for row in b_entry["points"]}
            common = sorted(set(a_pts) & set(b_pts))
            if d["op"] == "subtract":
                pts = [[dt, round(a_pts[dt] - b_pts[dt], 4)] for dt in common]
            else:  # divide100
                pts = [[dt, round((a_pts[dt] / b_pts[dt]) * 100, 4)] for dt in common if b_pts[dt] != 0]

        elif d["op"] == "yoy_series":
            a_entry = output["series"].get(d["a"])
            if not a_entry or a_entry["error"]:
                raise ValueError("component series unavailable")
            raw = [(_to_date(row[0]), row[1]) for row in a_entry["points"]]
            raw.sort()
            pts = []
            for i, (date, val) in enumerate(raw):
                target = date - timedelta(days=365)
                best, best_diff = None, timedelta(days=999999)
                for pd, pv in raw:
                    diff = abs(pd - target)
                    if diff < best_diff:
                        best, best_diff = pv, diff
                if best is not None and best_diff <= timedelta(days=45) and best != 0:
                    pts.append([date.strftime("%Y-%m-%d"), round(((val - best) / best) * 100, 3)])
            pts = pts[-260:]  # keep it bounded
        else:
            raise ValueError(f"unknown op {d['op']}")

        output["series"][d["id"]] = {
            "name": d["name"], "category": d["cat"], "source": "derived",
            "format": {"fmt": d["fmt"], "dec": d.get("dec"), "unit": d.get("unit")},
            "points": pts, "error": None if pts else "no overlapping dates",
        }
        print(f"[ok] {d['name']} ({d['id']}): {len(pts)} points (derived)")
    except Exception as e:
        output["series"][d["id"]] = {
            "name": d["name"], "category": d["cat"], "source": "derived",
            "format": {}, "points": [], "error": str(e),
        }
        print(f"[FAILED] {d['name']} ({d['id']}): {e}")


# ---------------------------------------------------------------------------
# Narrative layer -- simple, transparent, rules-based. Not a forecast.
# ---------------------------------------------------------------------------

def trend(output, series_id, months_back=6, threshold_pct=1.0):
    """Return dict with latest value/date/direction ('rising'/'falling'/'flat'),
    comparing against ~months_back ago. Returns None if unavailable."""
    entry = output["series"].get(series_id)
    if not entry or entry["error"] or len(entry["points"]) < 2:
        return None
    pts = [(_to_date(r[0]), r[1]) for r in entry["points"]]
    pts.sort()
    latest_date, latest_val = pts[-1]
    target = latest_date - timedelta(days=30 * months_back)
    ref_val = min(pts, key=lambda p: abs(p[0] - target))[1]
    if ref_val == 0:
        return {"latest": latest_val, "date": latest_date, "direction": "flat"}
    pct_change = ((latest_val - ref_val) / abs(ref_val)) * 100
    if pct_change > threshold_pct:
        direction = "rising"
    elif pct_change < -threshold_pct:
        direction = "falling"
    else:
        direction = "flat"
    return {"latest": latest_val, "date": latest_date, "direction": direction, "pct_change": round(pct_change, 1)}


def build_narrative(output):
    chapters = {}

    unrate = trend(output, "UNRATE", 6, 2.0)
    claims = trend(output, "ICSA", 3, 3.0)
    payrolls = trend(output, "PAYEMS", 3, 0.3)
    if unrate or claims:
        parts = []
        if unrate:
            parts.append(f"the unemployment rate is {unrate['direction']} ({unrate['latest']:.1f}% latest)")
        if claims:
            parts.append(f"jobless claims are {claims['direction']}")
        if payrolls:
            parts.append(f"payroll growth is {payrolls['direction']}")
        chapters["Labor market"] = "Labor market: " + "; ".join(parts) + "."

    indpro = trend(output, "INDPRO", 6, 1.0)
    lei = trend(output, "USSLIND", 6, 0.5)
    if indpro or lei:
        parts = []
        if indpro:
            parts.append(f"industrial production is {indpro['direction']}")
        if lei:
            parts.append(f"the leading index is {lei['direction']}")
        chapters["Growth & business cycle"] = "Growth: " + "; ".join(parts) + "."

    retail = trend(output, "RSAFS", 6, 1.0)
    savings = trend(output, "PSAVERT", 6, 3.0)
    if retail or savings:
        parts = []
        if retail:
            parts.append(f"retail sales are {retail['direction']}")
        if savings:
            parts.append(f"the savings rate is {savings['direction']}")
        chapters["Consumer health"] = "Consumer: " + "; ".join(parts) + "."

    housing = trend(output, "HOUST", 6, 3.0)
    mortgage = trend(output, "MORTGAGE30US", 6, 2.0)
    if housing or mortgage:
        parts = []
        if housing:
            parts.append(f"housing starts are {housing['direction']}")
        if mortgage:
            parts.append(f"the 30-year mortgage rate is {mortgage['direction']} ({mortgage['latest']:.2f}%)")
        chapters["Housing"] = "Housing: " + "; ".join(parts) + "."

    cpi = trend(output, "CPIAUCSL", 12, 2.0)
    corepce = trend(output, "PCEPILFE", 12, 2.0)
    if cpi or corepce:
        parts = []
        if cpi:
            parts.append(f"headline CPI is {cpi['direction']} ({cpi.get('pct_change','?')}% vs a year back)")
        if corepce:
            parts.append(f"core PCE trend is {corepce['direction']}")
        chapters["Inflation"] = "Inflation: " + "; ".join(parts) + "."

    curve = trend(output, "T10Y2Y", 1, 0.01)
    fedfunds = trend(output, "FEDFUNDS", 6, 1.0)
    curve_inverted = curve is not None and curve["latest"] < 0
    if curve or fedfunds:
        parts = []
        if curve:
            parts.append(f"the 2s10s curve is {'inverted' if curve_inverted else 'positively sloped'}")
        if fedfunds:
            parts.append(f"the fed funds rate is {fedfunds['direction']}")
        chapters["Monetary policy & rates"] = "Policy: " + "; ".join(parts) + "."

    hy = trend(output, "BAMLH0A0HYM2", 3, 5.0)
    if hy:
        chapters["Credit conditions"] = (
            f"Credit: high-yield spreads are {hy['direction']} ({hy['latest']:.2f}%), "
            f"{'a tightening signal' if hy['direction']=='rising' else 'suggesting easy financing conditions'}."
        )

    vix = trend(output, "VIXCLS", 1, 10.0)
    spx = trend(output, "^GSPC", 6, 3.0)
    if vix or spx:
        parts = []
        if spx:
            parts.append(f"the S&P 500 is {spx['direction']}")
        if vix:
            parts.append(f"volatility is {'elevated' if vix['latest'] and vix['latest'] > 20 else 'subdued'} (VIX {vix['latest']:.1f})")
        chapters["Markets & risk sentiment"] = "Markets: " + "; ".join(parts) + "."

    # Headline synthesis -- simple, transparent rules. Not a forecast.
    inflation_elevated = cpi is not None and cpi.get("direction") == "rising"
    growth_slowing = indpro is not None and indpro.get("direction") == "falling"
    unemployment_rising = unrate is not None and unrate.get("direction") == "rising"

    if curve_inverted and unemployment_rising:
        headline = ("The yield curve is inverted and unemployment is rising together -- historically the "
                     "combination most associated with recession risk, though the curve alone has a long "
                     "and variable lead time.")
    elif curve_inverted and not unemployment_rising:
        headline = ("The yield curve is inverted but the labor market hasn't cracked yet -- an inversion is "
                     "historically a leading signal, not a confirmation, and has preceded downturns by "
                     "anywhere from months to over a year.")
    elif inflation_elevated and growth_slowing:
        headline = ("Inflation is running hot while growth indicators soften -- a stagflation-adjacent "
                     "combination worth watching, though a single reading rarely confirms a regime.")
    elif not inflation_elevated and not growth_slowing and not unemployment_rising:
        headline = ("Growth, labor, and inflation indicators are broadly stable -- no single chapter is "
                     "flashing a dominant warning right now.")
    else:
        headline = "Signals are mixed across chapters -- no single narrative dominates the data this cycle."

    output["narrative"] = {
        "headline": headline,
        "chapters": chapters,
        "disclaimer": ("Automated read from simple threshold rules on the data below -- a description of "
                        "current trends, not a forecast or investment recommendation."),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "series": {},
        "recession_periods": [],
        "narrative": {},
    }

    for s in SERIES:
        try:
            points = fetch_fred(s["id"]) if s["src"] == "fred" else fetch_yahoo(s["id"])
            output["series"][s["id"]] = {
                "name": s["name"], "category": s["cat"], "source": s["src"],
                "format": {k: v for k, v in s.items() if k in ("fmt", "unit", "dec", "scale")},
                "points": points, "error": None,
            }
            print(f"[ok] {s['name']} ({s['id']}): {len(points)} points")
        except Exception as e:
            output["series"][s["id"]] = {
                "name": s["name"], "category": s["cat"], "source": s["src"],
                "format": {}, "points": [], "error": str(e),
            }
            print(f"[FAILED] {s['name']} ({s['id']}): {e}")

    for d in DERIVED:
        compute_derived(output, d)

    try:
        rec_points = fetch_fred(RECESSION_SERIES_ID)
        periods, run_start, prev_date = [], None, None
        for date_str, val in rec_points:
            if val >= 0.5 and run_start is None:
                run_start = date_str
            if val < 0.5 and run_start is not None:
                periods.append([run_start, prev_date])
                run_start = None
            prev_date = date_str
        if run_start is not None:
            periods.append([run_start, prev_date])
        output["recession_periods"] = periods
        print(f"[ok] Recession periods: {len(periods)}")
    except Exception as e:
        print(f"[FAILED] Recession indicator: {e}")

    try:
        build_narrative(output)
        print("[ok] Narrative built")
    except Exception as e:
        output["narrative"] = {"headline": "", "chapters": {}, "disclaimer": ""}
        print(f"[FAILED] Narrative: {e}")

    with open("data/all_series.json", "w") as f:
        json.dump(output, f, separators=(",", ":"))

    ok_count = sum(1 for v in output["series"].values() if v["error"] is None)
    total_count = len(SERIES) + len(DERIVED)
    print(f"\n{ok_count}/{total_count} series loaded. Wrote data/all_series.json")


if __name__ == "__main__":
    main()
