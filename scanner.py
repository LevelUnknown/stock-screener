"""
Reddit Momentum Scanner
Pulls ticker mention data from ApeWisdom across multiple subreddits,
computes a composite momentum score (velocity + sentiment + breadth),
and writes a daily markdown digest.

Composite score = 0.40 * velocity + 0.35 * sentiment + 0.25 * breadth
"""

import json
import os
import re
import sys
import time
import statistics
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------- config

SUBREDDIT_FILTERS = {
    "wallstreetbets": "wallstreetbets",
    "stocks": "stocks",
    "pennystocks": "pennystocks",
    "options": "options",
    "all": "all-stocks",  # aggregate across everything ApeWisdom tracks
}

HISTORY_FILE = Path("data/history.json")
REPORTS_DIR = Path("reports")
LATEST_REPORT = Path("REPORT.md")

HISTORY_DAYS = 45          # rolling window kept on disk
BASELINE_MIN_DAYS = 5      # need this much history before z-scores kick in
TOP_CANDIDATES = 15        # tickers sent to sentiment analysis
DIGEST_SIZE = 10           # tickers in the final digest
ALERT_ZSCORE = 5.0         # extreme spike threshold

WEIGHTS = {"velocity": 0.40, "sentiment": 0.35, "breadth": 0.25}

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-6"

REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")

USER_AGENT = "windows:momentum-scanner:v1.1 (personal research tool)"

MIN_MENTIONS = 15          # ignore tickers below this — single-digit counts are noise

# Common words / dead tickers that ApeWisdom misreads as stock symbols
TICKER_BLACKLIST = {
    "DD", "CEO", "IPO", "YOLO", "ATH", "EPS", "PT", "IT", "ALL", "ON", "A", "I",
    "RE", "VC", "CIA", "EOD", "USA", "IRS", "ETF", "AI", "EV", "GDP", "CPI",
    "FOMO", "OTM", "ITM", "IV", "PE", "APE", "BE", "GO", "SO", "OR", "AN", "BY",
}

# ETFs, funds, and defensive mega-caps — mentioned constantly in portfolio talk,
# never "momentum spike" material. Remove entries here if you want them back.
EXCLUDED_TICKERS = {
    "SPY", "QQQ", "QQQM", "TQQQ", "SQQQ", "VOO", "VTI", "VT", "IWM", "DIA",
    "SGOV", "JEPI", "JEPQ", "SCHD", "SCHG", "VYM", "VUG", "VGT", "SPLG", "IVV",
    "SOXL", "SOXS", "UPRO", "TLT", "GLD", "SLV", "BND", "ARKK", "SMH", "XLK",
    "KO", "PG", "JNJ", "WMT", "PEP", "MCD", "V", "MA", "BRK.B", "BRK.A",
}


# ---------------------------------------------------------------- data pull

def fetch_apewisdom(filter_name: str) -> list[dict]:
    """Fetch page 1 of ApeWisdom rankings for a given subreddit filter."""
    url = f"https://apewisdom.io/api/v1.0/filter/{filter_name}/page/1"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] ApeWisdom fetch failed for {filter_name}: {exc}")
        return []


def collect_mentions() -> dict:
    """Return {ticker: {mentions, upvotes, rank, mentions_24h_ago, subs: {...}}}."""
    tickers: dict[str, dict] = {}
    for sub, filter_name in SUBREDDIT_FILTERS.items():
        rows = fetch_apewisdom(filter_name)
        time.sleep(1)  # be polite
        for row in rows:
            sym = row.get("ticker", "").upper().strip()
            if (not sym or sym in TICKER_BLACKLIST or sym in EXCLUDED_TICKERS
                    or not re.fullmatch(r"[A-Z.]{1,6}", sym)):
                continue
            entry = tickers.setdefault(sym, {
                "name": row.get("name", ""),
                "mentions": 0, "upvotes": 0,
                "mentions_24h_ago": 0, "rank_all": None,
                "subs": {},
            })
            m = int(row.get("mentions") or 0)
            if sub == "all":
                entry["mentions"] = m
                entry["upvotes"] = int(row.get("upvotes") or 0)
                entry["mentions_24h_ago"] = int(row.get("mentions_24h_ago") or 0)
                entry["rank_all"] = int(row.get("rank") or 0)
            else:
                entry["subs"][sub] = {
                    "mentions": m,
                    "rank": int(row.get("rank") or 0),
                }
    # If a ticker never appeared in the 'all' filter, sum sub mentions as fallback
    for sym, e in tickers.items():
        if e["mentions"] == 0 and e["subs"]:
            e["mentions"] = sum(s["mentions"] for s in e["subs"].values())
    return tickers


_reddit_token: str | None = None


def get_reddit_token() -> str | None:
    """App-only OAuth token (client_credentials). Returns None if creds missing."""
    global _reddit_token
    if _reddit_token or not (REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET):
        return _reddit_token
    try:
        resp = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        _reddit_token = resp.json()["access_token"]
        print("[info] Reddit OAuth authenticated.")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Reddit OAuth failed, falling back to public API: {exc}")
    return _reddit_token


def fetch_reddit_posts(ticker: str, limit: int = 3) -> list[dict]:
    """Pull today's top posts mentioning the ticker. Uses OAuth if configured."""
    token = get_reddit_token()
    base = "https://oauth.reddit.com" if token else "https://www.reddit.com"
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = (
        f"{base}/r/wallstreetbets+stocks+pennystocks+options+smallstreetbets"
        f"/search.json?q={ticker}&restrict_sr=1&sort=top&t=day&limit={limit}"
    )
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        posts = []
        for child in resp.json().get("data", {}).get("children", []):
            d = child.get("data", {})
            posts.append({
                "title": d.get("title", ""),
                "text": (d.get("selftext") or "")[:1500],
                "score": d.get("score", 0),
                "num_comments": d.get("num_comments", 0),
                "subreddit": d.get("subreddit", ""),
                "url": "https://www.reddit.com" + d.get("permalink", ""),
            })
        return posts
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Reddit fetch failed for {ticker}: {exc}")
        return []


def fetch_price_change(ticker: str) -> float | None:
    """5-day % price change from Yahoo Finance chart API (context, not a signal)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
        closes = resp.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        if len(closes) >= 2 and closes[0]:
            return round((closes[-1] - closes[0]) / closes[0] * 100, 1)
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------- scoring

def load_history() -> dict:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text())
    return {}


def save_history(history: dict, today: str, tickers: dict) -> None:
    history[today] = {sym: e["mentions"] for sym, e in tickers.items()}
    dates = sorted(history.keys())[-HISTORY_DAYS:]
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps({d: history[d] for d in dates}, indent=0))


def velocity_score(sym: str, today_mentions: int, e: dict, history: dict) -> tuple[float, str, float]:
    """Z-score of today's mentions vs the ticker's own baseline, squashed to 0-1."""
    series = [day.get(sym, 0) for day in history.values()]
    if len(series) >= BASELINE_MIN_DAYS:
        mean = statistics.mean(series)
        stdev = max(statistics.pstdev(series), mean * 0.25, 1.0)
        z = (today_mentions - mean) / stdev
        pct = ((today_mentions - mean) / mean * 100) if mean else 0
        note = f"{z:+.1f}σ vs {len(series)}d baseline ({pct:+.0f}%)"
    else:
        # Bootstrap mode: compare vs 24h ago until we have enough history.
        # Capped at 0.6 so warm-up scores can't dominate the composite.
        prev = e.get("mentions_24h_ago", 0)
        ratio = today_mentions / prev if prev else 1.0
        z = min((ratio - 1) * 2, 8.0)
        note = f"{ratio:.1f}x vs 24h ago (baseline building)"
        score = max(0.0, min(0.6, z / 6.0))
        return score, note, 0.0
    score = max(0.0, min(1.0, z / 6.0))  # z of 6+ saturates
    return score, note, z if len(series) >= BASELINE_MIN_DAYS else 0.0


def breadth_score(e: dict) -> tuple[float, str]:
    """Presence across subs, with extra credit for small-sub traction."""
    subs = e.get("subs", {})
    n = len(subs)
    small_subs = {"pennystocks", "smallstreetbets"}
    small_bonus = 0.25 if any(s in small_subs for s in subs) else 0.0
    top_rank_bonus = 0.15 if any(v["rank"] <= 10 for v in subs.values()) else 0.0
    score = min(1.0, n / 4 * 0.6 + small_bonus + top_rank_bonus)
    return score, f"in {n} subs" + (" incl. small caps" if small_bonus else "")


def sentiment_score(ticker: str, posts: list[dict]) -> tuple[float, str]:
    """Claude scores bullishness + substance of top posts. Neutral if unavailable."""
    if not posts:
        return 0.4, "no substantive posts found"
    if not ANTHROPIC_API_KEY:
        return 0.5, "sentiment skipped (no API key)"

    digest = "\n---\n".join(
        f"[r/{p['subreddit']}, {p['score']} upvotes] {p['title']}\n{p['text']}" for p in posts
    )
    prompt = (
        f"You are scoring Reddit chatter about the stock ${ticker} for a momentum screener.\n"
        f"Posts from the last 24h:\n{digest}\n\n"
        "Respond ONLY with JSON, no markdown: "
        '{"bullishness": 0.0-1.0, "substance": 0.0-1.0, "summary": "<12 words max>"}. '
        "bullishness = how positive the crowd is. substance = quality of reasoning "
        "(real DD/catalysts score high; pure emoji hype or pump language scores low)."
    )
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"]
        parsed = json.loads(re.sub(r"```(json)?", "", text).strip())
        bull, subst = float(parsed["bullishness"]), float(parsed["substance"])
        score = bull * 0.5 + subst * 0.5  # hype without substance gets dragged down
        return round(score, 2), parsed.get("summary", "")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] sentiment failed for {ticker}: {exc}")
        return 0.5, "sentiment unavailable"


# ---------------------------------------------------------------- report

def build_report(today: str, ranked: list[dict], alerts: list[dict], watchlist: list[dict]) -> str:
    lines = [f"# Reddit momentum digest — {today}", ""]
    lines.append("*Idea-surfacing only — not financial advice. Verify everything before trading.*")
    lines.append("")

    if alerts:
        lines.append("## Extreme spikes (5σ+)")
        for r in alerts:
            lines.append(f"- **${r['ticker']}** — {r['velocity_note']}, composite {r['composite']:.2f}")
        lines.append("")

    lines.append("## Top tickers by composite score")
    lines.append("")
    lines.append("| # | Ticker | Score | Mentions | Velocity | Sentiment | Breadth | 5d price |")
    lines.append("|---|--------|-------|----------|----------|-----------|---------|----------|")
    for i, r in enumerate(ranked, 1):
        price = f"{r['price_5d']:+.1f}%" if r["price_5d"] is not None else "–"
        lines.append(
            f"| {i} | **${r['ticker']}** | {r['composite']:.2f} | {r['mentions']} "
            f"| {r['velocity']:.2f} | {r['sentiment']:.2f} | {r['breadth']:.2f} | {price} |"
        )
    lines.append("")

    lines.append("## Why they're here")
    for r in ranked:
        lines.append(f"### ${r['ticker']} — {r.get('name', '')}")
        lines.append(f"- Velocity: {r['velocity_note']}")
        lines.append(f"- Breadth: {r['breadth_note']}")
        lines.append(f"- Sentiment: {r['sentiment_note']}")
        if r["price_5d"] is not None:
            crowd = "price already moved — crowd may be late" if abs(r["price_5d"]) > 25 \
                else "chatter building, price relatively quiet"
            lines.append(f"- 5-day price: {r['price_5d']:+.1f}% ({crowd})")
        for p in r.get("posts", [])[:2]:
            lines.append(f"- Top post: [{p['title'][:80]}]({p['url']}) ({p['score']} upvotes)")
        lines.append("")

    if watchlist:
        lines.append("## Watchlist — how yesterday's names are trending")
        for w in watchlist:
            lines.append(f"- **${w['ticker']}**: mentions {w['prev']} → {w['now']} ({w['trend']})")
        lines.append("")

    return "\n".join(lines)


def build_watchlist(history: dict, tickers: dict, today: str) -> list[dict]:
    dates = sorted(d for d in history.keys() if d < today)
    if not dates:
        return []
    yesterday = history[dates[-1]]
    top_yesterday = sorted(yesterday.items(), key=lambda kv: kv[1], reverse=True)[:10]
    out = []
    for sym, prev in top_yesterday:
        now = tickers.get(sym, {}).get("mentions", 0)
        trend = "rising" if now > prev * 1.2 else "fading" if now < prev * 0.8 else "steady"
        out.append({"ticker": sym, "prev": prev, "now": now, "trend": trend})
    return out


# ---------------------------------------------------------------- main

def main() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"Scanning for {today}...")

    tickers = collect_mentions()
    if not tickers:
        print("[error] No data from ApeWisdom — aborting without writing history.")
        return 1
    print(f"Collected {len(tickers)} tickers.")

    history = load_history()

    # Score everything on velocity + breadth first (cheap), then sentiment on top N
    scored = []
    for sym, e in tickers.items():
        if e["mentions"] < MIN_MENTIONS:
            continue
        vel, vel_note, z = velocity_score(sym, e["mentions"], e, history)
        brd, brd_note = breadth_score(e)
        scored.append({
            "ticker": sym, "name": e.get("name", ""), "mentions": e["mentions"],
            "velocity": vel, "velocity_note": vel_note, "zscore": z,
            "breadth": brd, "breadth_note": brd_note,
        })

    scored.sort(key=lambda r: r["velocity"] * WEIGHTS["velocity"] + r["breadth"] * WEIGHTS["breadth"],
                reverse=True)
    candidates = scored[:TOP_CANDIDATES]

    for r in candidates:
        r["posts"] = fetch_reddit_posts(r["ticker"])
        time.sleep(2)  # respect Reddit's unauthenticated rate limits
        r["sentiment"], r["sentiment_note"] = sentiment_score(r["ticker"], r["posts"])
        r["price_5d"] = fetch_price_change(r["ticker"])
        r["composite"] = round(
            r["velocity"] * WEIGHTS["velocity"]
            + r["sentiment"] * WEIGHTS["sentiment"]
            + r["breadth"] * WEIGHTS["breadth"], 3)

    candidates.sort(key=lambda r: r["composite"], reverse=True)
    ranked = candidates[:DIGEST_SIZE]
    alerts = [r for r in ranked if r["zscore"] >= ALERT_ZSCORE]
    watchlist = build_watchlist(history, tickers, today)

    report = build_report(today, ranked, alerts, watchlist)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / f"{today}.md").write_text(report)
    LATEST_REPORT.write_text(report)

    save_history(history, today, tickers)
    print(f"Report written: reports/{today}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
