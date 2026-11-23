#!/usr/bin/env python3
"""Weekly Instagram stats for @ai_num1 (run on Sundays, see AUTOMATION.md "Weekly review").

Sends {"type": "stats"} to MAKE_WEBHOOK_URL. The Make scenario's "stats" route lists recent posts with their
insights and answers through a Webhook Response, one post per line:

    id|timestamp|media_type|likes|comments|reach|saved|shares|views

(a JSON array of objects with those keys also works). Posts are matched to data/posted.json by type and
posting time, then grouped by slot and format. The report is printed as Markdown and saved to
data/stats/<date>.json.

Exit codes: 0 ok, 5 the stats route is not set up in Make (the webhook only answered "Accepted").

Usage:
    python3 scripts/stats.py [--days 7] [--from-file response.txt]
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "posted.json"
OUT = ROOT / "data" / "stats"
FIELDS = ["id", "timestamp", "media_type", "likes", "comments", "reach", "saved", "shares", "views"]
IG_TYPE = {"IMAGE": "photo", "CAROUSEL_ALBUM": "carousel", "VIDEO": "reel", "REELS": "reel"}
DUBAI = dt.timezone(dt.timedelta(hours=4))


def fetch():
    hook = os.environ.get("MAKE_WEBHOOK_URL")
    if not hook:
        sys.exit("MAKE_WEBHOOK_URL is not set")
    req = urllib.request.Request(hook, data=b'{"type": "stats"}', method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read().decode("utf-8", "replace")


def num(v):
    try:
        return int(float(str(v).strip() or 0))
    except ValueError:
        return 0


def parse(body):
    body = body.strip()
    if not body or body.lower() == "accepted":
        return None
    if body[0] in "[{":
        data = json.loads(body)
        rows = data if isinstance(data, list) else data.get("data") or data.get("posts") or []
    else:
        rows = [dict(zip(FIELDS, ln.split("|"))) for ln in body.splitlines() if ln.count("|") >= 2]
    out = []
    for r in rows:
        ts = str(r.get("timestamp") or "").replace("+0000", "+00:00")
        try:
            when = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        out.append({"ig_id": str(r.get("id") or ""), "time": when, "type": IG_TYPE.get(str(r.get("media_type")).upper()),
                    **{k: num(r.get(k)) for k in FIELDS[3:]}})
    return out


def score(p):
    eng = p["likes"] + 2 * p["comments"] + 3 * p["saved"] + 3 * p["shares"]
    return eng, (round(100 * eng / p["reach"], 1) if p["reach"] else None)


def slot_of(h):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from publish import entry_slot
    return entry_slot(h)


def match(posts, history):
    """Attach each of our history entries to the Instagram post of the same type closest in time (< 30 min)."""
    used = set()
    rows = []
    for h in history:
        t = dt.datetime.fromisoformat(h["posted_at"])
        best = min((p for p in posts if p["type"] == h.get("type", "photo") and p["ig_id"] not in used),
                   key=lambda p: abs((p["time"] - t).total_seconds()), default=None)
        if best and abs((best["time"] - t).total_seconds()) < 1800:
            used.add(best["ig_id"])
            eng, rate = score(best)
            rows.append({"id": h["id"], "slot": slot_of(h), "type": h.get("type", "photo"), "kind": h.get("kind", "news"),
                         "format": h.get("format") or h.get("kind", "news"), "headline": h["headline"],
                         **{k: best[k] for k in FIELDS[3:]}, "engagement": eng, "eng_rate": rate})
    return rows


def group(rows, key):
    out = {}
    for r in rows:
        g = out.setdefault(r[key] or "?", {"posts": 0, "reach": 0, "engagement": 0, "saved": 0, "shares": 0})
        g["posts"] += 1
        for k in ("reach", "engagement", "saved", "shares"):
            g[k] += r[k]
    for g in out.values():
        g["avg_reach"] = round(g["reach"] / g["posts"])
        g["eng_rate"] = round(100 * g["engagement"] / g["reach"], 1) if g["reach"] else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--from-file")
    a = ap.parse_args()
    body = Path(a.from_file).read_text(encoding="utf-8") if a.from_file else fetch()
    posts = parse(body)
    if posts is None:
        print("STATS NOT CONFIGURED: the Make webhook has no 'stats' route with a Webhook Response yet")
        sys.exit(5)
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=a.days)
    history = [h for h in json.loads(HISTORY.read_text(encoding="utf-8"))
               if h.get("posted_at") and dt.datetime.fromisoformat(h["posted_at"]) >= since]
    rows = sorted(match(posts, history), key=lambda r: r["engagement"], reverse=True)
    by_slot, by_format = group(rows, "slot"), group(rows, "format")

    today = dt.datetime.now(DUBAI).date().isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{today}.json").write_text(json.dumps({"date": today, "days": a.days, "posts": rows,
                                                   "by_slot": by_slot, "by_format": by_format},
                                                  ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"## Weekly stats ({today}, last {a.days} days): {len(rows)} of {len(history)} posts matched\n")
    print("| post | slot | format | reach | likes | comments | saved | shares | eng. rate |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['id']} | {r['slot']} | {r['format']} | {r['reach']} | {r['likes']} | {r['comments']} | "
              f"{r['saved']} | {r['shares']} | {r['eng_rate']}% |")
    for title, g in (("slot", by_slot), ("format", by_format)):
        print(f"\n**By {title}:** " + "; ".join(
            f"{k}: {v['posts']} posts, avg reach {v['avg_reach']}, eng. rate {v['eng_rate']}%"
            for k, v in sorted(g.items(), key=lambda kv: -(kv[1]['eng_rate'] or 0))))
    print(f"\nsaved: data/stats/{today}.json")


if __name__ == "__main__":
    main()
