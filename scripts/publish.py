#!/usr/bin/env python3
"""Publish one queued news post (photo or reel) to Instagram @ai_num1 via the Make webhook.

Photo ("type": "photo", the default):
    duplicate check -> validate caption fields -> FLUX concept background (Cloudflare Workers AI)
    -> render JPEG (falls back to the classic template if image generation fails)
    -> commit & push to main (GitHub Pages) -> wait until live
    -> POST {"type": "photo", image_url, caption} to MAKE_WEBHOOK_URL -> record in data/posted.json.
Reel ("type": "reel"):
    duplicate check -> validate -> render MP4 with scripts/reel.py (reuses the reviewed dry-run render)
    -> push to main -> wait until live -> POST {"type": "reel", video_url, caption}
    -> record -> remove the MP4 from the repo again (Pages space), once Instagram has had time to fetch it.

Usage:
    python3 scripts/publish.py queue/<id>.json            # full publish
    python3 scripts/publish.py queue/<id>.json --dry-run  # render + caption only, no push/post
    python3 scripts/publish.py --check queue/<id>.json    # only run the duplicate check
    python3 scripts/publish.py --cleanup                  # remove published reel videos still in the repo
    --regen-bg       photo: discard the cached background and generate a new one
    --no-ai          photo: force the classic template (no image generation)
    --regen-images   reel: generate new FLUX images
    --no-voice       reel: no narration (music only)
    --rerender       reel: render again even if the reviewed render is still valid
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from imagegen import ImageGenError, check_prompt, generate  # noqa: E402
from render import render, render_ai  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PAGES_BASE = "https://phernesarta-droid.github.io/ai-num1-media/"
HISTORY = ROOT / "data" / "posted.json"
PAGES_BRANCH = "main"
BG_CACHE = ROOT / "cache" / "bg"   # git-ignored; keeps the reviewed background between dry-run and publish
MAX_HASHTAGS = 5
REEL_CACHE = ROOT / "cache" / "reel"
# Seconds to keep a reel video online after the webhook call when Make does not confirm the publish
# in its response (Make answers "Accepted" and creates the reel asynchronously; Instagram fetches the
# file during that time).
VIDEO_GRACE = 15 * 60


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


def clean_tags(tags):
    out, seen = [], set()
    for t in tags:
        t = t.strip().lstrip("#").replace(" ", "_")
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def post_type(post):
    t = post.get("type") or "photo"
    if t not in ("photo", "reel"):
        sys.exit(f"type must be 'photo' or 'reel', got {t!r}")
    return t


def validate(post):
    """Caption rules: hook first, question last, 1-5 specific hashtags; image_prompt (photo) or reel fields."""
    errors = []
    hook = (post.get("hook") or "").strip()
    if not hook:
        errors.append("missing field: hook (one-sentence opener)")
    elif "\n" in hook or len(hook) > 150 or len(re.findall(r"[.!?؟…]\s+\S", hook)) > 0:
        errors.append("hook must be a single sentence, at most 150 characters")
    q = (post.get("question") or "").strip()
    if not q:
        errors.append("missing field: question (closing question for comments)")
    elif not q.endswith(("؟", "?")) or "\n" in q or len(q) > 150:
        errors.append("question must be one line, at most 150 characters, ending in ؟")
    tags = clean_tags(post.get("hashtags") or [])
    if not 1 <= len(tags) <= MAX_HASHTAGS:
        errors.append(f"hashtags: need 1-{MAX_HASHTAGS} unique tags, got {len(tags)}")
    if post_type(post) == "reel":
        from reel import ReelError, reel_spec
        try:
            reel_spec(post)
        except ReelError as e:
            errors.append(str(e))
    elif not (post.get("image_prompt") or "").strip():
        errors.append("missing field: image_prompt (English concept description)")
    else:
        bad = check_prompt(post["image_prompt"])
        if bad:
            errors.append(f"image_prompt must not name companies/products/people: {', '.join(bad)}")
    return errors


def music_credit(track):
    return (f"🎵 موسیقی: «{track['title']}» از {track['artist']} ({track['site']})، "
            f"مجوز {track['license']}: {track['license_url']}")


def build_caption(post, music=None):
    parts = [post["hook"].strip(), "", post["summary"], ""]
    if post.get("caption_extra"):
        parts += [post["caption_extra"], ""]
    parts.append("🔗 منبع:")
    for s in post["sources"]:
        parts.append(f"• {s['name']}: {s['url']}")
    if music:
        parts += ["", music_credit(music)]
    parts += ["", "📌 خبرهای روزانه و معتبر هوش مصنوعی در @ai_num1"]
    parts += ["", "💬 " + post["question"].strip()]
    parts += ["", " ".join("#" + t for t in clean_tags(post["hashtags"]))]
    caption = "\n".join(parts)
    if len(caption) > 2200:
        raise ValueError(f"Caption too long ({len(caption)} > 2200)")
    return caption


def make_image(post, out, regen=False, no_ai=False):
    """Render with a FLUX background; on any image-generation failure use the classic template."""
    if no_ai:
        render(post, out)
        return "classic", None
    bg = BG_CACHE / f"{post['id']}.jpg"
    meta_file = bg.with_suffix(".json")
    cached = bg.exists() and meta_file.exists() and not regen and \
        json.loads(meta_file.read_text(encoding="utf-8")).get("prompt") == post["image_prompt"]
    try:
        if not cached:
            generate(post["image_prompt"], bg)
        render_ai(post, bg, out)
        return "ai", json.loads(meta_file.read_text(encoding="utf-8"))
    except (ImageGenError, OSError, ValueError) as e:
        print(f"WARNING: AI image failed ({e}); falling back to classic template", file=sys.stderr)
        render(post, out)
        return "classic", {"error": str(e)[:300]}


def make_reel(post, rerender=False, regen_images=False, voice=True):
    """Render the reel into cache/reel/<id>/ (git-ignored), or reuse the reviewed dry-run render if the
    post's reel fields are unchanged. Returns (info, path)."""
    from reel import post_hash, probe, render_reel
    out = REEL_CACHE / post["id"] / f"{post['id']}.mp4"
    meta_file = REEL_CACHE / post["id"] / "render.json"
    h = post_hash(post)
    if out.exists() and meta_file.exists() and not (rerender or regen_images):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        if meta.get("hash") == h and meta.get("voice_requested") == voice and meta.get("size") == out.stat().st_size:
            print("reel: reusing the reviewed render", file=sys.stderr)
            return meta["info"], out
    kf = REEL_CACHE / post["id"] / "keyframes"
    if kf.exists():
        for f in kf.glob("*.jpg"):
            f.unlink()
    info = render_reel(post, out, voice=voice, regen_images=regen_images, keyframes=kf,
                       log=lambda m: print(m, file=sys.stderr))
    pr = probe(out)
    v = next(s for s in pr["streams"] if s["codec_name"] == "h264")
    if (v["width"], v["height"]) != (1080, 1920) or not 20 <= float(pr["format"]["duration"]) <= 30.5:
        raise RuntimeError(f"bad reel output: {pr}")
    info["keyframes"] = str(kf / "sheet.jpg")
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.write_text(json.dumps({"hash": h, "voice_requested": voice, "size": out.stat().st_size, "info": info},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    return info, out


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=ROOT, check=check, text=True, capture_output=True)


def push_to_pages(paths, message, remove=()):
    if paths:
        git("add", *[str(p) for p in paths])
    if remove:
        git("rm", "-q", "--ignore-unmatch", *[str(p) for p in remove])
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


def wait_live(url, timeout=900, ctype="image/jpeg", size=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status == 200 and ctype in r.headers.get("Content-Type", "") and \
                        (size is None or r.headers.get("Content-Length") in (None, str(size))):
                    return True
        except urllib.error.HTTPError:
            pass
        except Exception as e:  # network hiccup
            print("wait_live:", e, file=sys.stderr)
        time.sleep(15)
    return False


def post_webhook(payload):
    """payload: {"type": "photo", "image_url", "caption"} or {"type": "reel", "video_url", "caption"}.
    The Make scenario's Router sends type == "reel" to "Create a Reel Post", everything else to
    "Create a Photo Post"."""
    hook = os.environ.get("MAKE_WEBHOOK_URL")
    if not hook:
        raise RuntimeError("MAKE_WEBHOOK_URL is not set")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(hook, data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.status, r.read().decode("utf-8", "replace")[:500]


def confirmed_by_make(resp):
    """True if the webhook response carries an Instagram media id (needs a Webhook Response module in
    Make after "Create a Reel Post"); a bare "Accepted" means the scenario is still running."""
    try:
        data = json.loads(resp)
    except ValueError:
        return False
    return isinstance(data, dict) and bool(data.get("id") or data.get("media_id"))


def save_history(history):
    HISTORY.parent.mkdir(exist_ok=True)
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cleanup_videos(history, grace=VIDEO_GRACE):
    """Remove reel videos that are still in the repo once their post is older than `grace` seconds.
    Returns the ids cleaned. Safe to run at the start of every scheduled run."""
    now = dt.datetime.now(dt.timezone.utc)
    done = []
    for h in history:
        if h.get("type") != "reel" or h.get("video_removed_at") or not h.get("video_path"):
            continue
        age = (now - dt.datetime.fromisoformat(h["posted_at"])).total_seconds()
        path = ROOT / h["video_path"]
        if age < grace:
            continue
        if path.exists() or git("ls-files", "--error-unmatch", h["video_path"], check=False).returncode == 0:
            git("rm", "-q", "--ignore-unmatch", h["video_path"])
            path.unlink(missing_ok=True)
        h["video_removed_at"] = now.isoformat(timespec="seconds")
        done.append(h["id"])
    # orphans: videos pushed by a run that failed before recording the post (kept for at least 2 h)
    known = {h.get("video_path") for h in history}
    for f in git("ls-files", "posts/*.mp4", "posts/**/*.mp4", check=False).stdout.split():
        if f in known:
            continue
        ts = git("log", "-1", "--format=%ct", "--", f, check=False).stdout.strip()
        if ts and now.timestamp() - int(ts) > max(grace, 2 * 3600):
            git("rm", "-q", f)
            done.append(Path(f).stem)
    if done:
        save_history(history)
        push_to_pages([HISTORY], f"cleanup: remove published reel videos ({', '.join(done)})")
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("post", nargs="?")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--cleanup", action="store_true")
    ap.add_argument("--regen-bg", action="store_true")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--regen-images", action="store_true")
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("--rerender", action="store_true")
    ap.add_argument("--grace", type=int, default=VIDEO_GRACE, help="seconds before a published video is removed")
    a = ap.parse_args()

    if a.cleanup:
        done = cleanup_videos(load_history(), grace=a.grace)
        print("cleanup:", ", ".join(done) if done else "nothing to remove")
        return
    if not a.post:
        ap.error("post JSON path is required")

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

    errors = validate(post)
    if errors:
        sys.exit("invalid post:\n  - " + "\n  - ".join(errors))

    kind = post_type(post)
    y, m = post["date"][:4], post["date"][5:7]
    if kind == "reel":
        publish_reel(post, post_path, history, a, Path("posts") / y / m / f"{post['id']}.mp4")
        return

    rel = Path("posts") / y / m / f"{post['id']}.jpg"
    image_mode, bg_meta = make_image(post, ROOT / rel, regen=a.regen_bg, no_ai=a.no_ai)
    caption = build_caption(post)
    print("mode: ", image_mode, bg_meta or "")
    image_url = PAGES_BASE + rel.as_posix()
    print("image:", ROOT / rel)
    print("url:  ", image_url)
    print("----- caption -----\n" + caption + "\n-------------------")
    if a.dry_run:
        return

    push_to_pages([rel, post_path], f"post: {post['id']}")
    if not wait_live(image_url):
        sys.exit(f"image never went live: {image_url}")
    status, resp = post_webhook({"type": "photo", "image_url": image_url, "caption": caption})
    print("webhook:", status, resp)
    if status >= 300:
        sys.exit("webhook failed")

    history.append({
        "id": post["id"],
        "type": "photo",
        "story_key": post.get("story_key"),
        "headline": post["headline"],
        "source_urls": [s["url"] for s in post["sources"]],
        "image_url": image_url,
        "image_mode": image_mode,
        "posted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "webhook_status": status,
    })
    save_history(history)
    push_to_pages([HISTORY], f"log: posted {post['id']}")
    print("DONE")


def publish_reel(post, post_path, history, a, rel):
    from reel import ReelError
    try:
        info, rendered = make_reel(post, rerender=a.rerender, regen_images=a.regen_images, voice=not a.no_voice)
    except (ReelError, ImageGenError) as e:
        sys.exit(f"reel failed: {e}")
    caption = build_caption(post, music=info["music"])
    video_url = PAGES_BASE + rel.as_posix()
    print("reel: ", json.dumps({k: info[k] for k in ("duration", "size_mb", "voice", "images")}, ensure_ascii=False))
    print("music:", info["music"]["title"])
    print("video:", rendered)
    print("frames:", info.get("keyframes"))
    print("url:  ", video_url)
    print("----- caption -----\n" + caption + "\n-------------------")
    if a.dry_run:
        return

    (ROOT / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(rendered, ROOT / rel)
    size = (ROOT / rel).stat().st_size
    push_to_pages([rel, post_path], f"reel: {post['id']}")
    if not wait_live(video_url, ctype="video/mp4", size=size):
        # take the video down again so it does not linger on Pages
        push_to_pages([], f"remove unpublished reel {post['id']}", remove=[rel])
        sys.exit(f"video never went live: {video_url}")
    try:
        status, resp = post_webhook({"type": "reel", "video_url": video_url, "caption": caption})
    except urllib.error.HTTPError as e:
        status, resp = e.code, e.read().decode("utf-8", "replace")[:500]
    except Exception as e:  # timeout etc.: Make may still be working on it, so the video stays online
        sys.exit(f"webhook error: {e} (video left online; `publish.py --cleanup` removes it after 2 h)")
    print("webhook:", status, resp)
    if status >= 300:
        push_to_pages([], f"remove unpublished reel {post['id']}", remove=[rel])
        sys.exit("webhook failed; video removed from the repo")

    entry = {
        "id": post["id"],
        "type": "reel",
        "story_key": post.get("story_key"),
        "headline": post["headline"],
        "source_urls": [s["url"] for s in post["sources"]],
        "video_url": video_url,
        "video_path": rel.as_posix(),
        "duration": info["duration"],
        "voice": bool(info.get("voice")),
        "music": info["music"]["title"],
        "posted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "webhook_status": status,
    }
    history.append(entry)
    save_history(history)
    push_to_pages([HISTORY], f"log: posted {post['id']}")

    # free the Pages space: right away if Make confirmed the reel, otherwise after Instagram had time to fetch it
    wait = 0 if confirmed_by_make(resp) else a.grace
    if wait:
        print(f"waiting {wait // 60} min before removing the video (Make did not confirm the publish)")
        time.sleep(wait)
    done = cleanup_videos(history, grace=0)
    print("video removed from repo:", ", ".join(done) or "no")
    print("DONE")


if __name__ == "__main__":
    main()
