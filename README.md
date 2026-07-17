# Reddit momentum scanner

A daily agent that scans Reddit ticker chatter (via ApeWisdom + Reddit's public API), scores tickers on a composite of **mention velocity** (40%), **sentiment quality via Claude** (35%), and **cross-subreddit breadth** (25%), and commits a markdown digest to this repo every weekday morning before US market open.

> Idea-surfacing tool only — not financial advice. Reddit momentum names reverse violently. Always do your own research.

## Setup (5 minutes)

1. **Create a new GitHub repo** (private is fine) and push these files to it, keeping the folder structure:
   ```
   scanner.py
   requirements.txt
   .github/workflows/daily-scan.yml
   data/            (empty, with .gitkeep)
   reports/         (empty, with .gitkeep)
   ```
2. **Add your Claude API key**: repo → Settings → Secrets and variables → Actions → New repository secret. Name it `ANTHROPIC_API_KEY`. Get a key at https://console.anthropic.com. Without it the scanner still runs, but sentiment scores default to neutral (0.5).
3. **Enable the workflow**: go to the Actions tab, enable workflows if prompted, then run **Daily Reddit momentum scan → Run workflow** to test it manually.
4. Done. Each weekday at ~7:30am ET a fresh digest lands in `REPORT.md` (latest) and `reports/YYYY-MM-DD.md` (archive).

## Reading the digest

- **Extreme spikes (5σ+)** — tickers whose mentions today are wildly above their own baseline. Rare by design.
- **Top 10 table** — composite score with the three sub-scores broken out.
- **Why they're here** — per-ticker notes plus links to the top Reddit posts so you can read the actual theses.
- **5-day price context** — flags whether the crowd looks early ("chatter building, price quiet") or late ("price already moved").
- **Watchlist** — how yesterday's top names are trending today (rising / steady / fading).

## Important notes

- **The first ~5 runs are warm-up.** Velocity needs a baseline; until then it compares vs. 24h-ago data from ApeWisdom and won't fire 5σ alerts. It gets sharper every day it runs.
- **Costs**: GitHub Actions is free at this usage. Claude API cost is roughly a cent or two per day (15 small calls to Sonnet).
- **Tuning**: weights, thresholds, and subreddits are all constants at the top of `scanner.py`.
- Reddit's unauthenticated API is rate-limited; the script sleeps between calls. If you see frequent fetch failures in the Actions logs, create a free Reddit app and I can switch it to authenticated OAuth.

## Ideas for v2

- Telegram/email push for 5σ alerts (intraday runs)
- Filter out mega-caps so small-cap movers surface more
- Track hit rate: did flagged tickers actually move in the following days?
