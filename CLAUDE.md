# Reddit Momentum Scanner

Personal project: a daily agent that scans Reddit stock chatter, scores tickers on a
composite momentum signal, and commits a markdown digest to this repo. Built July 2026.
Owner is a retail investor, not a professional developer — explain changes plainly,
keep deploys simple, and never assume unstated git knowledge.

## Architecture

- `scanner.py` — the entire agent. Runs once daily via GitHub Actions.
- `.github/workflows/daily-scan.yml` — schedule: weekdays 10:47 UTC (~6:47am ET). The
  bot commits results back to the repo, so ALWAYS `git pull --no-rebase` before pushing.
  Schedule changed 2026-07-23 from 11:30 UTC: GitHub's scheduler was running the job
  1.5-2h late, pushing the scan past US market open. Moved earlier and off the congested
  :30 minute (many jobs queue on the half-hour) to restore the pre-open buffer.
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
- BLOCKED-ish: Reddit search API 403s from GitHub's datacenter IPs, AND (2026 update)
  Reddit closed self-serve API registration — approval now via manual support ticket
  (submitted, account justlostmypizza) with high rejection rates for solo devs.
  Treat OAuth as "bonus if approved", not a dependency.
- NEW (2026-07-26): RSS fallback layer in scanner.py. fetch_rss_entries() pulls each
  sub's top-of-day Atom feed (reddit.com/r/SUB/top.rss?t=day) ONCE per run — one request
  per sub — deduped by post URL and cached. match_rss_posts() matches tickers locally
  (conservative, UNCHANGED: "$SYM" always, bare "SYM" only for 3+ char symbols to avoid
  EU/MU-style false hits, company name case-insensitive at 4+ chars). fetch_reddit_posts()
  tries OAuth → public search → RSS fallback, in that order.
  RSS_SUBREDDITS includes TheRaceTo10Million — note it feeds ONLY the RSS/sentiment leg;
  it is not an ApeWisdom filter, so it never affects mention counts or breadth.
  Dedicated watchlist subs: WATCHLIST_SUBREDDITS (near MY_WATCHLIST) maps a ticker to
  its own subreddit; build_my_watchlist() fetches that sub's top-of-day feed via
  fetch_watchlist_sub_posts() (cached per sub) and treats every post as auto-relevant —
  NO ticker/name text match, because dedicated-sub posts rarely spell out the cashtag —
  then merges with the normal fetch_reddit_posts() results, deduped by URL, capped at 3.
  Currently WATCHLIST_SUBREDDITS = {"SKHY": "SKHynix"}.
  RSS-sourced posts carry score=0 (Atom has no upvote counts) and a via_rss=True flag;
  the report omits the "(N upvotes)" text for such posts rather than printing "0 upvotes".
  RATE LIMITING (fixed 2026-07-26 after a live run): unauthenticated GitHub-runner IPs
  have a tight Reddit budget. An earlier version fetched top+hot feeds (12 requests, 2s
  apart) and drew HTTP 429s after the first couple of feeds — rate-limited, not IP-blocked
  (two feeds had succeeded). Current guards: (a) top.rss only, one request per sub; (b)
  RSS_REQUEST_GAP = 10s between requests (was 2s); (c) _rss_get() retries ONLY on 429, up
  to 2 times, honoring Retry-After (capped 60s) else 15s/30s backoff — any other error
  status raises immediately and the feed is skipped, unchanged. Same _rss_get retry path
  covers the watchlist-sub fetch. No limit= param on .rss URLs — Reddit ignores it and
  returns ~25 entries regardless, so there's no per-sub count knob.
  Worst-case runtime: ~5.5 min if every one of the 6 feeds 429s through both backoff
  retries (the common 429 shape); a pathological Retry-After=60-every-time would reach
  ~13 min but requires Reddit to keep demanding 60s waits. Normal runs are seconds.
  STILL possible from Actions: if Reddit escalates to a hard 403/IP-block on RSS too,
  sentiment gracefully degrades to the 0.40 default. Plan B: run post-fetching from
  owner's home PC (residential IP not blocked).
  If OAuth ever approved: add repo secrets REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET —
  OAuth takes priority automatically, zero code changes.
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
