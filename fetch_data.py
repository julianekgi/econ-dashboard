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

import bisect
import csv
import io
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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
    dict(id="GDP",         name="Nominal GDP",                          cat="Growth & business cycle", src="fred", fmt="usd", unit="B", dec=0),
    dict(id="A191RL1Q225SBEA", name="Real GDP growth rate (QoQ, annualized)", cat="Growth & business cycle", src="fred", fmt="pct", dec=1),
    dict(id="INDPRO",      name="Industrial production index",          cat="Growth & business cycle", src="fred", fmt="index", dec=1),
    dict(id="TCU",         name="Capacity utilization",                  cat="Growth & business cycle", src="fred", fmt="pct", dec=1),
    dict(id="DGORDER",     name="Durable goods new orders",             cat="Growth & business cycle", src="fred", fmt="usd", unit="M", dec=0),
    dict(id="NEWORDER",    name="Core capital goods orders",            cat="Growth & business cycle", src="fred", fmt="usd", unit="M", dec=0),
    dict(id="BUSINV",      name="Business inventories",                  cat="Growth & business cycle", src="fred", fmt="usd", unit="M", dec=0),
    dict(id="USALOLITOAASTSAM", name="Leading economic index (OECD)",   cat="Growth & business cycle", src="fred", fmt="index", dec=2),
    dict(id="USPHCI",      name="Coincident economic index",            cat="Growth & business cycle", src="fred", fmt="index", dec=1),
    dict(id="CP",          name="Corporate profits",                    cat="Growth & business cycle", src="fred", fmt="usd", unit="B", dec=0),
    dict(id="RECPROUSM156N", name="Recession probability model",        cat="Growth & business cycle", src="fred", fmt="pct", dec=1),
    dict(id="SAHMREALTIME", name="Sahm Rule recession indicator",       cat="Growth & business cycle", src="fred", fmt="pct", dec=2),
    dict(id="JHDUSRGDPBR", name="GDP-based recession indicator",        cat="Growth & business cycle", src="fred", fmt="number", dec=0),
    dict(id="CFNAI",       name="Chicago Fed national activity index", cat="Growth & business cycle", src="fred", fmt="number", dec=2),
    dict(id="W875RX1",     name="Real personal income (ex. transfers)", cat="Growth & business cycle", src="fred", fmt="usd", unit="B", dec=0),
    dict(id="TOTALSA",     name="Total vehicle sales",                  cat="Growth & business cycle", src="fred", fmt="number", unit="M", dec=1),
    dict(id="AWHMAN",      name="Average weekly hours (manufacturing)", cat="Growth & business cycle", src="fred", fmt="number", unit=" hrs", dec=1),
    dict(id="GACDFSA066MSFRBPHI", name="Philly Fed manufacturing index", cat="Growth & business cycle", src="fred", fmt="number", dec=1),
    dict(id="GACDISA066MSFRBNY", name="NY Fed Empire State manufacturing index", cat="Growth & business cycle", src="fred", fmt="number", dec=1),
    dict(id="USSLIND",     name="Leading index (St. Louis Fed model)",  cat="Growth & business cycle", src="fred", fmt="number", dec=2),
    dict(id="ISRATIO",     name="Business inventories / sales ratio",  cat="Growth & business cycle", src="fred", fmt="number", dec=2),

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
    dict(id="JTSLDR",      name="Layoffs & discharges rate (JOLTS)",   cat="Labor market", src="fred", fmt="pct", dec=1),
    dict(id="JTSHIR",      name="Hires rate (JOLTS)",                  cat="Labor market", src="fred", fmt="pct", dec=1),
    dict(id="TEMPHELPS",   name="Temporary help services employment",  cat="Labor market", src="fred", fmt="number", unit="K", dec=0),
    dict(id="IURSA",       name="Insured unemployment rate",           cat="Labor market", src="fred", fmt="pct", dec=1),
    dict(id="UEMPMEAN",    name="Average duration of unemployment",    cat="Labor market", src="fred", fmt="number", unit=" wks", dec=1),
    dict(id="UNEMPLOY",    name="Number of unemployed",                cat="Labor market", src="fred", fmt="number", unit="K", dec=0),
    dict(id="AWHI",        name="Aggregate weekly hours index (total private)", cat="Labor market", src="fred", fmt="index", dec=1),
    dict(id="LNS12500000", name="Employed, usually full time",         cat="Labor market", src="fred", fmt="number", scale=0.001, unit="M", dec=1),
    dict(id="LNS12032194", name="Part-time for economic reasons",      cat="Labor market", src="fred", fmt="number", unit="K", dec=0),

    # ---- Consumer health: is the household sector still spending? ----
    dict(id="RSAFS",       name="Retail sales",                        cat="Consumer health", src="fred", fmt="usd", unit="M", dec=0),
    dict(id="PI",          name="Personal income",                     cat="Consumer health", src="fred", fmt="usd", unit="B", dec=0),
    dict(id="PCE",         name="Personal consumption expenditures",   cat="Consumer health", src="fred", fmt="usd", unit="B", dec=0),
    dict(id="PSAVERT",     name="Personal savings rate",               cat="Consumer health", src="fred", fmt="pct", dec=1),
    dict(id="TOTALSL",     name="Total consumer credit",               cat="Consumer health", src="fred", fmt="usd", scale=0.001, unit="B", dec=0),
    dict(id="DRCCLACBS",   name="Credit card delinquency rate",        cat="Consumer health", src="fred", fmt="pct", dec=2),
    dict(id="UMCSENT",     name="Consumer sentiment (UMich)",          cat="Consumer health", src="fred", fmt="index", dec=1),
    dict(id="REVOLSL",     name="Revolving consumer credit",           cat="Consumer health", src="fred", fmt="usd", scale=0.001, unit="B", dec=0),
    dict(id="DRCLACBS",    name="Consumer loan delinquency rate",      cat="Consumer health", src="fred", fmt="pct", dec=2),
    dict(id="DRSFRMACBS",  name="Mortgage delinquency rate (single-family)", cat="Consumer health", src="fred", fmt="pct", dec=2),
    dict(id="BOGZ1FL192090005Q", name="Household net worth",           cat="Consumer health", src="fred", fmt="usd", unit="M", dec=0),
    dict(id="TDSP",        name="Household debt service ratio",        cat="Consumer health", src="fred", fmt="pct", dec=1),

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
    dict(id="CUSR0000SEHC", name="Owners' equivalent rent CPI",        cat="Housing", src="fred", fmt="index", dec=1),

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
    dict(id="MICH",        name="Consumer inflation expectations (UMich)", cat="Inflation", src="fred", fmt="pct", dec=1),
    dict(id="EXPINF1YR",   name="1-year expected inflation (Cleveland Fed)", cat="Inflation", src="fred", fmt="pct", dec=2),

    # ---- Monetary policy & rates: what is the Fed doing, what does the curve say? ----
    dict(id="FEDFUNDS",    name="Fed funds rate",                      cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="DGS10",       name="10-year treasury yield",              cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="DGS2",        name="2-year treasury yield",               cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="DGS3MO",      name="3-month treasury yield",              cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="T10Y2Y",      name="2s10s yield curve spread",            cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="T10Y3M",      name="10Y-3M yield curve spread",           cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="WALCL",       name="Fed balance sheet, total assets",     cat="Monetary policy & rates", src="fred", fmt="usd", unit="M", dec=0),
    dict(id="M2SL",        name="M2 money supply",                     cat="Monetary policy & rates", src="fred", fmt="usd", unit="B", dec=0),
    dict(id="SOFR",        name="SOFR overnight rate",                 cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="DTWEXBGS",    name="US dollar index (broad)",             cat="Monetary policy & rates", src="fred", fmt="index", dec=2),
    dict(id="DEXUSEU",     name="USD / EUR",                           cat="Monetary policy & rates", src="fred", fmt="number", dec=4),
    dict(id="DGS1",        name="1-year treasury yield",               cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="DGS30",       name="30-year treasury yield",              cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="M1SL",        name="M1 money supply",                     cat="Monetary policy & rates", src="fred", fmt="usd", unit="B", dec=0),
    dict(id="M2V",         name="M2 velocity",                         cat="Monetary policy & rates", src="fred", fmt="number", dec=2),
    dict(id="THREEFYTP10", name="10-year term premium",                cat="Monetary policy & rates", src="fred", fmt="pct", dec=2),
    dict(id="WRESBAL",     name="Bank reserve balances",               cat="Monetary policy & rates", src="fred", fmt="usd", unit="M", dec=0),

    # ---- Distress & credit access: is financing getting harder to find? ----
    dict(id="BAMLH0A0HYM2", name="High-yield credit spread",          cat="Distress & credit access", src="fred", fmt="pct", dec=2),
    dict(id="BAMLC0A0CM",  name="Investment-grade credit spread",     cat="Distress & credit access", src="fred", fmt="pct", dec=2),
    dict(id="DAAA",        name="Moody's Aaa corporate yield",        cat="Distress & credit access", src="fred", fmt="pct", dec=2),
    dict(id="DBAA",        name="Moody's Baa corporate yield",        cat="Distress & credit access", src="fred", fmt="pct", dec=2),
    dict(id="DRTSCILM",    name="Bank lending standards (net % tightening)", cat="Distress & credit access", src="fred", fmt="number", dec=1),
    dict(id="BUSLOANS",    name="Commercial & industrial loans outstanding", cat="Distress & credit access", src="fred", fmt="usd", unit="B", dec=0),
    dict(id="STLFSI4",     name="St. Louis Fed financial stress index", cat="Distress & credit access", src="fred", fmt="index", dec=2),
    dict(id="NFCI",        name="Chicago Fed financial conditions index", cat="Distress & credit access", src="fred", fmt="index", dec=2),
    dict(id="DRTSCLCC",    name="Bank lending standards (credit cards, net % tightening)", cat="Distress & credit access", src="fred", fmt="number", dec=1),
    dict(id="DFII10",      name="Real 10-year yield (TIPS)",           cat="Distress & credit access", src="fred", fmt="pct", dec=2),
    dict(id="DRTSCIS",     name="Bank lending standards, small firms (net % tightening)", cat="Distress & credit access", src="fred", fmt="number", dec=1),
    dict(id="DRBLACBS",    name="Business loan delinquency rate",     cat="Distress & credit access", src="fred", fmt="pct", dec=2),
    dict(id="CORBLACBS",   name="Business loan charge-off rate",      cat="Distress & credit access", src="fred", fmt="pct", dec=2),

    # ---- Markets & risk sentiment ----
    dict(id="^GSPC",       name="S&P 500",                             cat="Markets & risk sentiment", src="yahoo", fmt="number", dec=2),
    dict(id="^DJI",        name="Dow Jones industrial average",       cat="Markets & risk sentiment", src="yahoo", fmt="number", dec=2),
    dict(id="^IXIC",       name="Nasdaq Composite",                    cat="Markets & risk sentiment", src="yahoo", fmt="number", dec=2),
    dict(id="^RUT",        name="Russell 2000",                        cat="Markets & risk sentiment", src="yahoo", fmt="number", dec=2),
    dict(id="VIXCLS",      name="VIX (volatility index)",              cat="Markets & risk sentiment", src="fred", fmt="index", dec=2),
    dict(id="MMMFFAQ027S", name="Money market fund assets",            cat="Markets & risk sentiment", src="fred", fmt="usd", unit="M", dec=0),
    dict(id="GC=F",        name="Gold",                                 cat="Markets & risk sentiment", src="yahoo", fmt="usd", unit="/oz", dec=2),
    dict(id="SI=F",        name="Silver",                               cat="Markets & risk sentiment", src="yahoo", fmt="usd", unit="/oz", dec=2),
    dict(id="BTC-USD",     name="Bitcoin",                              cat="Markets & risk sentiment", src="yahoo", fmt="usd", dec=0),
    dict(id="ETH-USD",     name="Ethereum",                             cat="Markets & risk sentiment", src="yahoo", fmt="usd", dec=2),
    dict(id="KNX",         name="KNX stock price",                     cat="Markets & risk sentiment", src="yahoo", fmt="usd", dec=2),
    dict(id="VNQ",         name="REIT index proxy (Vanguard VNQ)",     cat="Markets & risk sentiment", src="yahoo", fmt="usd", dec=2),
    dict(id="^W5000",      name="Wilshire 5000 total market index",    cat="Markets & risk sentiment", src="yahoo", fmt="number", dec=2),
    dict(id="^DJT",        name="Dow Jones Transportation Average",    cat="Markets & risk sentiment", src="yahoo", fmt="number", dec=2),
    dict(id="XLY",         name="Consumer discretionary sector (XLY)", cat="Markets & risk sentiment", src="yahoo", fmt="usd", dec=2),
    dict(id="XLP",         name="Consumer staples sector (XLP)",       cat="Markets & risk sentiment", src="yahoo", fmt="usd", dec=2),

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
    dict(id="LBR=F",       name="Lumber",                               cat="Commodities & energy", src="yahoo", fmt="usd", unit="/1000 bd ft", dec=2),
    dict(id="ZC=F",        name="Corn",                                 cat="Commodities & energy", src="yahoo", fmt="usd", scale=0.01, unit="/bu", dec=2),
    dict(id="ZW=F",        name="Wheat",                                cat="Commodities & energy", src="yahoo", fmt="usd", scale=0.01, unit="/bu", dec=2),
    dict(id="ZS=F",        name="Soybeans",                             cat="Commodities & energy", src="yahoo", fmt="usd", scale=0.01, unit="/bu", dec=2),

    # ---- Demographics & structural ----
    dict(id="POPTHM",      name="US population",                       cat="Demographics & structural", src="fred", fmt="number", scale=0.001, unit="M", dec=1),
    dict(id="LFWA64TTUSM647S", name="Working-age population",          cat="Demographics & structural", src="fred", fmt="number", scale=0.001, unit="M", dec=1),
    dict(id="OPHNFB",      name="Labor productivity",                  cat="Demographics & structural", src="fred", fmt="index", dec=1),
    dict(id="ULCNFB",      name="Unit labor costs",                    cat="Demographics & structural", src="fred", fmt="index", dec=1),

    # ---- Private-markets-adjacent proxies (no direct PE/VC data is public) ----
    dict(id="IPO",         name="IPO market proxy (Renaissance IPO ETF)", cat="Private markets signals", src="yahoo", fmt="usd", dec=2),

    # ---- Freight & trucking: KNX-relevant demand and cost indicators ----
    dict(id="TRUCKD11",    name="ATA truck tonnage index",             cat="Freight & trucking", src="fred", fmt="index", dec=1),
    dict(id="TSIFRGHT",    name="Freight transportation services index", cat="Freight & trucking", src="fred", fmt="index", dec=1),
    dict(id="FRGSHPUSM649NCIS", name="Cass freight shipments index",   cat="Freight & trucking", src="fred", fmt="index", dec=3),
    dict(id="CES4348400001", name="Truck transportation employment",   cat="Freight & trucking", src="fred", fmt="number", unit="K", dec=1),
    dict(id="PCU484121484121", name="Truckload freight pricing (PPI)", cat="Freight & trucking", src="fred", fmt="index", dec=1),
    dict(id="GASDESW",     name="Diesel price",                        cat="Freight & trucking", src="fred", fmt="usd", unit="/gal", dec=3),
    dict(id="ECOMSA",      name="E-commerce retail sales",             cat="Freight & trucking", src="fred", fmt="usd", unit="M", dec=0),
    dict(id="BDRY",        name="Baltic Dry Index proxy (ETF)",        cat="Freight & trucking", src="yahoo", fmt="usd", dec=2),
    dict(id="PCU484122484122", name="LTL freight trucking pricing (PPI)", cat="Freight & trucking", src="fred", fmt="index", dec=1),
    dict(id="RAILFRTCARLOADSD11", name="Rail freight carloads",        cat="Freight & trucking", src="fred", fmt="number", scale=0.000001, unit="M", dec=2),
    dict(id="RAILFRTINTERMODAL", name="Rail freight intermodal traffic", cat="Freight & trucking", src="fred", fmt="number", scale=0.000001, unit="M", dec=2),
]

# Derived series -- computed after the raw fetch, using simple math on one or two series
DERIVED = [
    dict(op="subtract", id="BAA_AAA_SPREAD", name="Baa-Aaa corporate bond spread",
         cat="Distress & credit access", a="DBAA", b="DAAA", fmt="pct", dec=2),
    dict(op="subtract", id="BRENT_WTI_SPREAD", name="Brent-WTI oil spread",
         cat="Commodities & energy", a="DCOILBRENTEU", b="DCOILWTICO", fmt="usd", unit="/bbl", dec=2),
    dict(op="divide100", id="SMALLCAP_LARGECAP_RATIO", name="Small-cap / large-cap ratio (Russell 2000 vs S&P 500)",
         cat="Private markets signals", a="^RUT", b="^GSPC", fmt="number", dec=2),
    dict(op="yoy_series", id="M2_YOY_GROWTH", name="M2 money supply growth (YoY)",
         cat="Private markets signals", a="M2SL", fmt="pct", dec=1),

    # ---- Named/attributed recession signals ----
    dict(op="divide100", id="BUFFETT_INDICATOR", name="Buffett Indicator (market cap / GDP)",
         cat="Markets & risk sentiment", a="^W5000", b="GDP", fmt="pct", dec=1),
    dict(op="divide100", id="DOW_THEORY_RATIO", name="Dow Theory ratio (Transports / Industrials)",
         cat="Markets & risk sentiment", a="^DJT", b="^DJI", fmt="number", dec=2),
    dict(op="divide100", id="COPPER_GOLD_RATIO", name="Copper/Gold ratio (\"Dr. Copper\")",
         cat="Commodities & energy", a="PCOPPUSDM", b="GC=F", fmt="number", dec=2),
    dict(op="divide", id="BEVERIDGE_RATIO", name="Job openings per unemployed (Beveridge ratio)",
         cat="Labor market", a="JTSJOL", b="UNEMPLOY", fmt="number", dec=2),
    dict(op="divide", id="CONSUMER_DISC_STAPLES_RATIO", name="Consumer discretionary / staples ratio",
         cat="Markets & risk sentiment", a="XLY", b="XLP", fmt="number", dec=2),
    dict(op="divide100", id="HOME_PRICE_RENT_RATIO", name="Home price-to-rent ratio (proxy)",
         cat="Housing", a="CSUSHPISA", b="CUSR0000SAH1", fmt="number", dec=1),
    dict(op="divide100", id="REAL_RETAIL_SALES", name="Real retail sales (CPI-adjusted)",
         cat="Consumer health", a="RSAFS", b="CPIAUCSL", fmt="number", dec=1),
    dict(op="divide100", id="REAL_AVG_HOURLY_EARNINGS", name="Real average hourly earnings (CPI-adjusted)",
         cat="Labor market", a="CES0500000003", b="CPIAUCSL", fmt="number", dec=2),
    dict(op="divide", id="GOLD_SPX_RATIO", name="Gold / S&P 500 ratio",
         cat="Markets & risk sentiment", a="GC=F", b="^GSPC", fmt="number", dec=3),
]

RECESSION_SERIES_ID = "USREC"


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def fetch_fred(series_id):
    # FRED's server hangs to a read timeout when sent a browser-style User-Agent
    # (unclear why) but responds instantly to requests' default UA -- leave it off.
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    resp = requests.get(url, timeout=25)
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
    # range="max" silently downgrades to quarterly bars on Yahoo's end (still
    # honors interval="1d" up through "10y"), so cap at 10y for daily data.
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
    resp = requests.get(url, params={"range": "10y", "interval": "1d"}, headers=HEADERS, timeout=25)
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


HEADLINE_FEEDS = [
    ("CNBC", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
]


def _parse_pubdate(raw):
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_headlines():
    items = []
    for source, url in HEADLINE_FEEDS:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:15]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not title or not link:
                    continue
                dt = _parse_pubdate((item.findtext("pubDate") or "").strip())
                items.append({
                    "title": title, "link": link, "source": source,
                    "published": dt.astimezone(timezone.utc).isoformat() if dt else None,
                })
            print(f"[ok] Headlines from {source}: {len(items)} so far", flush=True)
        except Exception as e:
            print(f"[FAILED] Headlines from {source}: {e}", flush=True)
    items.sort(key=lambda x: x["published"] or "", reverse=True)
    return items[:20]


# ---------------------------------------------------------------------------
# Derived series math
# ---------------------------------------------------------------------------

def _to_date(s):
    return datetime.strptime(s, "%Y-%m-%d")


def compute_derived(output, d):
    try:
        if d["op"] in ("subtract", "divide100", "divide"):
            a_entry = output["series"].get(d["a"])
            b_entry = output["series"].get(d["b"])
            if not a_entry or not b_entry or a_entry["error"] or b_entry["error"]:
                raise ValueError("component series unavailable")
            # Forward-fill b onto a's dates rather than requiring exact matches --
            # needed when the two series update at different cadences (e.g. daily
            # market data vs. quarterly GDP), and harmless when they match anyway.
            a_pts = sorted(a_entry["points"])
            b_sorted = sorted(b_entry["points"])
            b_dates = [_to_date(r[0]) for r in b_sorted]
            pts = []
            for dt_str, a_val in a_pts:
                idx = bisect.bisect_right(b_dates, _to_date(dt_str)) - 1
                if idx < 0:
                    continue
                b_val = b_sorted[idx][1]
                if d["op"] == "subtract":
                    pts.append([dt_str, round(a_val - b_val, 4)])
                elif b_val != 0:
                    ratio = (a_val / b_val) * 100 if d["op"] == "divide100" else a_val / b_val
                    pts.append([dt_str, round(ratio, 4)])

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
    lei = trend(output, "USALOLITOAASTSAM", 6, 0.5)
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
    small_firm_standards = trend(output, "DRTSCIS", 6, 5.0)
    if hy or small_firm_standards:
        if (small_firm_standards and small_firm_standards["direction"] == "rising"
                and hy and hy["direction"] in ("flat", "falling")):
            chapters["Distress & credit access"] = (
                f"Distress & credit access: small-firm bank lending standards are tightening "
                f"({small_firm_standards['latest']:.1f}) while the high-yield spread is {hy['direction']} "
                f"({hy['latest']:.2f}%) -- private and bank-financed companies may be feeling credit "
                f"stress before public markets show it."
            )
        else:
            parts = []
            if small_firm_standards:
                parts.append(f"small-firm bank lending standards are {small_firm_standards['direction']} "
                             f"({small_firm_standards['latest']:.1f})")
            if hy:
                parts.append(f"the high-yield spread is {hy['direction']} ({hy['latest']:.2f}%)")
            chapters["Distress & credit access"] = "Distress & credit access: " + "; ".join(parts) + "."

    vix = trend(output, "VIXCLS", 1, 10.0)
    spx = trend(output, "^GSPC", 6, 3.0)
    if vix or spx:
        parts = []
        if spx:
            parts.append(f"the S&P 500 is {spx['direction']}")
        if vix:
            parts.append(f"volatility is {'elevated' if vix['latest'] and vix['latest'] > 20 else 'subdued'} (VIX {vix['latest']:.1f})")
        chapters["Markets & risk sentiment"] = "Markets: " + "; ".join(parts) + "."

    tonnage = trend(output, "TRUCKD11", 6, 2.0)
    diesel = trend(output, "GASDESW", 6, 5.0)
    if tonnage or diesel:
        parts = []
        if tonnage:
            parts.append(f"truck tonnage is {tonnage['direction']}")
        if diesel:
            parts.append(f"diesel prices are {diesel['direction']} (${diesel['latest']:.2f}/gal)")
        chapters["Freight & trucking"] = "Freight: " + "; ".join(parts) + "."

    # ---- Signal scoreboard: one red/yellow/green read per theme, reusing the
    # trends already computed above. "Good"/"bad" direction is theme-specific
    # (e.g. rising unemployment is bad, rising industrial production is good).
    def _status(direction, good_direction):
        if direction is None:
            return "gray"
        if direction == good_direction:
            return "green"
        if direction == "flat":
            return "yellow"
        return "red"

    def _signal(label, cat, status):
        return {"label": label, "cat": cat, "status": status,
                "detail": chapters.get(cat, "Not enough data to read this signal yet.")}

    if curve_inverted:
        policy_status = "red"
    elif fedfunds and fedfunds["direction"] == "rising":
        policy_status = "yellow"
    elif fedfunds and fedfunds["direction"] == "falling":
        policy_status = "green"
    else:
        policy_status = "yellow" if curve is not None else "gray"

    if vix and vix.get("latest") is not None:
        markets_status = "red" if vix["latest"] > 25 else ("yellow" if vix["latest"] > 15 else "green")
    else:
        markets_status = "gray"

    scoreboard = [
        _signal("Growth", "Growth & business cycle", _status(indpro["direction"] if indpro else None, "rising")),
        _signal("Labor market", "Labor market", _status(unrate["direction"] if unrate else None, "falling")),
        _signal("Consumer", "Consumer health", _status(retail["direction"] if retail else None, "rising")),
        _signal("Housing", "Housing", _status(housing["direction"] if housing else None, "rising")),
        _signal("Inflation", "Inflation", _status(cpi["direction"] if cpi else None, "falling")),
        _signal("Monetary policy", "Monetary policy & rates", policy_status),
        _signal("Distress & credit access", "Distress & credit access", _status(hy["direction"] if hy else None, "falling")),
        _signal("Markets", "Markets & risk sentiment", markets_status),
        _signal("Freight & trucking", "Freight & trucking", _status(tonnage["direction"] if tonnage else None, "rising")),
    ]

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
        "scoreboard": scoreboard,
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
        "headlines": [],
        "narrative": {},
    }

    for s in SERIES:
        t0 = time.monotonic()
        try:
            points = fetch_fred(s["id"]) if s["src"] == "fred" else fetch_yahoo(s["id"])
            output["series"][s["id"]] = {
                "name": s["name"], "category": s["cat"], "source": s["src"],
                "format": {k: v for k, v in s.items() if k in ("fmt", "unit", "dec", "scale")},
                "points": points, "error": None,
            }
            print(f"[ok] {s['name']} ({s['id']}): {len(points)} points ({time.monotonic()-t0:.1f}s)", flush=True)
        except Exception as e:
            output["series"][s["id"]] = {
                "name": s["name"], "category": s["cat"], "source": s["src"],
                "format": {}, "points": [], "error": str(e),
            }
            print(f"[FAILED] {s['name']} ({s['id']}): {e} ({time.monotonic()-t0:.1f}s)", flush=True)

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
        output["headlines"] = fetch_headlines()
        print(f"[ok] Headlines: {len(output['headlines'])}")
    except Exception as e:
        output["headlines"] = []
        print(f"[FAILED] Headlines: {e}")

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
