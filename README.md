# Knight Group Economic Data Pack

A self-updating economic dashboard. A scheduled GitHub Actions workflow pulls
~130 series from FRED and Yahoo Finance daily, computes a handful of derived series
(credit spreads, a small-cap/large-cap ratio, M2 growth), and writes a
rules-based narrative summary. GitHub Pages serves an interactive dashboard
reading that data — recession-shaded charts, an indexed compare tool, a YoY
change table, and plain-English chapter summaries above each section. No
server to maintain, no local machine that needs to stay on.

Indicators are organized by the story they tell — growth, labor, the
consumer, housing, inflation, policy, credit, markets, trade, commodities,
demographics, and private-markets-adjacent proxies — rather than an
arbitrary filing-cabinet structure. The narrative layer is a transparent,
rules-based read of recent trends (e.g. "yield curve inverted + unemployment
rising"), not a forecast — see the disclaimer in the banner itself.

## One-time setup (about 5 minutes)

1. **Create a new GitHub repository** (public or private both work; if
   private, GitHub Pages requires a paid plan — public is simplest).
2. **Upload this folder's contents** to that repository (drag-and-drop on
   github.com works, or `git push` if you're comfortable with git).
3. **Enable Actions**: repo → Settings → Actions → General → allow all
   actions.
4. **Run the workflow once manually**: repo → Actions tab → "Update economic
   data" → Run workflow. Wait ~30 seconds, then check that `data/all_series.json`
   was committed.
5. **Enable Pages**: repo → Settings → Pages → Source: "Deploy from a
   branch" → Branch: `main`, folder `/ (root)` → Save.
6. Wait a minute, then visit `https://<your-username>.github.io/<repo-name>/`.

That's it. The workflow re-runs automatically every day at 12:00 UTC
(edit the `cron` line in `.github/workflows/update-data.yml` to change the
time), and the site always reflects the latest commit to `data/all_series.json`.

## Adding a series

Add one line to the `SERIES` list in `fetch_data.py`:

```python
dict(id="XXXXX", name="Display name", cat="Category name", src="fred", fmt="index"),
```

`src` is `"fred"` (id = FRED series ID) or `"yahoo"` (id = Yahoo Finance ticker).
`cat` must match one of the six section names already used, or it becomes a
new section automatically. Push the change — the next scheduled run (or a
manual "Run workflow") picks it up.

## Files

| File | Purpose |
|---|---|
| `fetch_data.py` | Pulls every series, writes `data/all_series.json` |
| `.github/workflows/update-data.yml` | Runs `fetch_data.py` daily and commits the result |
| `index.html` | The dashboard itself — reads `data/all_series.json` at load time |
| `requirements.txt` | Python dependency for the Actions runner (`requests`) |

## Notes

- FRED data lags by its normal release cycle (daily/weekly/monthly per
  series); market data from Yahoo Finance is end-of-day.
- Gray bands on charts mark NBER-designated recessions, pulled from FRED's
  own `USREC` indicator.
- If a series shows "unavailable," check the Actions run log — it prints
  `[ok]` or `[FAILED]` with the error for every series.
