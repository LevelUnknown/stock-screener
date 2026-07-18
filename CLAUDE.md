# Reddit Momentum Scanner

Personal project: a daily agent that scans Reddit stock chatter, scores tickers on a
composite momentum signal, and commits a markdown digest to this repo. Built July 2026.
Owner is a retail investor, not a professional developer — explain changes plainly,
keep deploys simple, and never assume unstated git knowledge.

## Architecture

- `scanner.py` — the entire agent. Runs once daily via GitHub Actions.
- `.github/workflows/daily-scan.yml` — schedule: weekdays 11:30 UTC (~7:30am ET). The
  bot commits results back to the repo, so ALWAYS `git pull --no-rebase` before pushing.
- `data/history.json` — rolling 45-day mention history (the baseline). Do not hand-edit.
- `data/picks.json` — daily top-10 picks with entry prices, for hit-rate grading.
- `reports/YYYY-MM-DD.md` + `REPORT.md` — the daily digest (REPORT.md = latest).

Pipeline: ApeWisdom API (mention counts across r/wallstreetbets, r/stocks,
r/pennystocks, r/options) → min-mention + blacklist/exclusion filters → velocity
(z-score vs own baseline) + breadth scoring → top 15 get Reddit post fetch + Claude
sentiment (claude-sonnet-4-6 via API) + Yahoo price data → composite score
(velocity 40% / sentiment 35% / breadth 25%) → setup classification → report.

## Current status (as of 2026-07-18, v1.4)

- WORKING: ApeWisdom collection, scoring, setup labels, price context, hit-rate
  tracker, personal watchlist, daily Actions run, aligned tables.
- BLOCKED: Reddit post fetching 403s from GitHub's datacenter IPs. Reddit API
  registration submitted (form: support.reddithelp.com ticket, account justlostmypizza),
  awaiting approval. Until then sentiment defaults to 0.40 ("no substantive posts") and
  EVENT GAMBLE labels cannot fire. When approved: create script-type app at
  reddit.com/prefs/apps, add repo secrets REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET —
  zero code changes needed, OAuth support is already in fetch_reddit_posts/get_reddit_token.
  Plan B if rejected: run post-fetching from owner's home PC (residential IP not blocked).
- WARM-UP: baselines need ~5 days of history (started 2026-07-18). Until then velocity
  uses 24h-ago comparison capped at 0.6, z-scores read 0, and no 5σ alerts fire.
  Scoreboard fills in from ~2026-07-21 (picks must age 3 days).

## Key design decisions (don't undo without asking the owner)

- NO buy/sell recommendations. Deliberately replaced with setup-type hypotheses
  (EARLY / CROWD REACTION / EVENT GAMBLE / FADING / NOISE) + confidence. The scanner
  sees chatter and price only — it cannot know valuation/catalysts/dilution, so an
  action label would be false precision. The legend line in the report stating this
  is intentional; keep it.
- Blacklist protocol: ambiguous tickers get blacklisted only after a human reads the
  actual Reddit posts and confirms misparse (owner does this). Each entry carries a
  comment saying what it collides with and that it's reversible. Verified so far:
  DTE (0DTE options slang), HBM (High Bandwidth Memory jargon), OS (operating system),
  RE/VC/CIA (common acronyms). Lesson learned: SPCX looked fake but is real (SpaceX
  IPO'd June 2026) — always verify with a web search or post reading before blacklisting.
- EXCLUDED_TICKERS removes ETFs and defensive mega-caps (SGOV, KO, PG...) because they
  can't "momentum spike" meaningfully. Different list from the blacklist; keep separate.
- MIN_MENTIONS = 15: below this, velocity ratios are statistical noise.
- Bootstrap velocity capped at 0.6 so warm-up scores can't dominate the composite.
- price_5d is endpoint-to-endpoint (close 5 trading days ago vs latest), NOT an average.
  Known limitation: can't distinguish "fell all week" from "crashed yesterday". A 1-day
  price column is a candidate future fix.
- Hit-rate tracker exists to answer the only question that matters: do flagged tickers
  actually move? Per-setup-label return breakdown appears once ≥3 picks per label are
  evaluated. Don't break picks.json schema; history can't be recovered.
- "3d return" is approximate (weekday-only runs mean 3-5 calendar days). Fine for a
  scoreboard, not for backtesting claims.

## Known data-source blind spots

- ApeWisdom's ticker dictionary lags new IPOs (missed SKHY / SK Hynix for days after
  its July 2026 debut). Mitigation: MY_WATCHLIST searches Reddit by ticker OR company
  name directly, bypassing ApeWisdom — once Reddit access works.
- Mention counters can't see discussion that uses company names without cashtags.
- Reddit momentum often LAGS price (crowd reacting), hence the price-context labels.

## Owner's watchlist

MY_WATCHLIST in scanner.py, currently: SKHY (SK Hynix). Add as "TICKER": "Company Name".
Keep it small — each entry costs a Reddit search + price lookup per run.

## Conventions

- Python 3.12, stdlib + requests only. Keep it single-file unless it genuinely hurts.
- Secrets live in GitHub Actions secrets (ANTHROPIC_API_KEY, later REDDIT_CLIENT_ID/
  REDDIT_CLIENT_SECRET). NEVER put keys in code or commit them.
- Test scoring/report changes with mocked data before pushing (no network needed);
  the live APIs (ApeWisdom, Reddit, Yahoo) are NOT reachable from all environments.
- Reports must stay readable as raw text: use format_table() for any new tables.
- Every report keeps the "not financial advice" line at top.

## Roadmap (owner-approved ideas, not yet built)

1. When Reddit approved: verify sentiment + EVENT GAMBLE + watchlist post links work.
2. Claude relevance check in sentiment ("are these posts about this company?") to
   auto-discount misparsed tickers instead of relying on the manual blacklist.
3. 1-day price change column alongside 5d.
4. Telegram/email push for 5σ alerts (only worth it once alerts actually fire).
5. Tune MIN_MENTIONS / mega-cap dampening based on 2+ weeks of real reports, not theory.
