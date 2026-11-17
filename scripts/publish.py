#!/usr/bin/env python3
"""Publish one queued news post to Instagram @ai_num1 via the Make webhook.

Steps: duplicate check -> render JPEG -> commit & push to main (GitHub Pages)
       -> wait until the image is live -> POST {image_url, caption} to MAKE_WEBHOOK_URL
       -> record in data/posted.json and push.

Usage:
    python3 scripts/publish.py queue/<id>.json            # full publish
    python3 scripts/publish.py queue/<id>.json --dry-run  # render + caption only, no push/post
    python3 scripts/publish.py --check queue/<id>.json    # only run the duplicate check
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render import render  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PAGES_BASE = "https://phernesarta-droid.github.io/ai-num1-media/"
HISTORY = ROOT / "data" / "posted.json"
PAGES_BRANCH = "main"


def load_history():
    if HISTORY.exists():
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    return []


def norm_url(u):
    u = u.strip().lower()
    u = re.sub(r"^https?://(www\.)?", "", u)
    u = u.split("?")[0].split("#")[0].rstrip("/")
    return u


def norm_text(t):
    t = re.sub(r"[‌‏‎]", " ", t)
    t = re.sub(r"[^\w\s]", " ", t.lower())
    return set(w for w in t.split() if len(w) > 2)


def find_duplicate(post, history):
    urls = {norm_url(s["url"]) for s in post["sources"]}
    words = norm_text(post["headline"])
    for h in history:
        if h["id"] == post["id"]:
            return h, "same id"
        if urls & {norm_url(u) for u in h.get("source_urls", [])}:
            return h, "same source URL"
        if post.get("story_key") and post.get("story_key") == h.get("story_key"):
            return h, "same story_key"
        hw = norm_text(h.get("headline", ""))
        if words and hw and len(words & hw) / len(words | hw) >= 0.6:
            return h, "near-identical headline"
    return None, None


def build_caption(post):
    parts = [post["headline"], "", post["summary"], ""]
    if post.get("caption_extra"):
        parts += [post["caption_extra"], ""]
    parts.append("🔗 منبع:")
    for s in post["sources"]:
        parts.append(f"• {s['name']}: {s['url']}")
    parts += ["", "📌 برای خبرهای روزانه و معتبر هوش مصنوعی، @ai_num1 را دنبال کنید."]
    tags = post.get("hashtags") or ["هوش_مصنوعی", "AI"]
    parts += ["", " ".join("#" + t.lstrip("#").replace(" ", "_") for t in tags)]
    caption = "\n".join(parts)
    if len(caption) > 2200:
        raise ValueError(f"Caption too long ({len(caption)} > 2200)")
    return caption


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=ROOT, check=check, text=True, capture_output=True)


def push_to_pages(paths, message):
    git("add", *[str(p) for p in paths])
    if git("diff", "--cached", "--quiet", check=False).returncode != 0:
        git("commit", "-m", message)
    for attempt in range(5):
        r = git("push", "origin", f"HEAD:{PAGES_BRANCH}", check=False)
        if r.returncode == 0:
            return
        # remote moved: integrate and retry
        git("fetch", "origin", PAGES_BRANCH, check=False)
        git("merge", "--no-edit", "FETCH_HEAD", check=False)
        time.sleep(2 ** attempt)
    raise RuntimeError(f"git push to {PAGES_BRANCH} failed: {r.stderr}")


def wait_live(url, timeout=900):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status == 200 and "image/jpeg" in r.headers.get("Content-Type", ""):
                    return True
        except urllib.error.HTTPError:
            pass
        except Exception as e:  # network hiccup
            print("wait_live:", e, file=sys.stderr)
        time.sleep(15)
    return False


def post_webhook(image_url, caption):
    hook = os.environ.get("MAKE_WEBHOOK_URL")
    if not hook:
        raise RuntimeError("MAKE_WEBHOOK_URL is not set")
    body = json.dumps({"image_url": image_url, "caption": caption}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(hook, data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, r.read().decode("utf-8", "replace")[:500]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("post")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    post_path = Path(a.post)
    post = json.loads(post_path.read_text(encoding="utf-8"))
    for k in ("id", "date", "headline", "summary", "sources"):
        if not post.get(k):
            sys.exit(f"missing field: {k}")
    if not all(s.get("url", "").startswith("http") for s in post["sources"]):
        sys.exit("every source needs a full URL")

    history = load_history()
    dup, why = find_duplicate(post, history)
    if dup:
        print(f"DUPLICATE ({why}) of {dup['id']} posted {dup.get('posted_at')}")
        sys.exit(3)
    if a.check:
        print("OK: not a duplicate")
        return

    y, m = post["date"][:4], post["date"][5:7]
    rel = Path("posts") / y / m / f"{post['id']}.jpg"
    render(post, ROOT / rel)
    caption = build_caption(post)
    image_url = PAGES_BASE + rel.as_posix()
    print("image:", ROOT / rel)
    print("url:  ", image_url)
    print("----- caption -----\n" + caption + "\n-------------------")
    if a.dry_run:
        return

    push_to_pages([rel, post_path], f"post: {post['id']}")
    if not wait_live(image_url):
        sys.exit(f"image never went live: {image_url}")
    status, resp = post_webhook(image_url, caption)
    print("webhook:", status, resp)
    if status >= 300:
        sys.exit("webhook failed")

    history.append({
        "id": post["id"],
        "story_key": post.get("story_key"),
        "headline": post["headline"],
        "source_urls": [s["url"] for s in post["sources"]],
        "image_url": image_url,
        "posted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "webhook_status": status,
    })
    HISTORY.parent.mkdir(exist_ok=True)
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    push_to_pages([HISTORY], f"log: posted {post['id']}")
    print("DONE")


if __name__ == "__main__":
    main()
