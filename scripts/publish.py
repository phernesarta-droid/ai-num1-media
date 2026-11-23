#!/usr/bin/env python3
"""Publish one queued post (photo, carousel or reel) to Instagram @ai_num1 via the Make webhook.

Daily slots (Dubai time; see AUTOMATION.md):
    morning   09:45  news carousel (exactly 6 slides)  "type": "carousel", "kind": "news"
    noon      13:45  photo: second news / tool of day  "type": "photo",    "kind": "news" | "tool"
    afternoon 17:45  educational carousel              "type": "carousel", "kind": "edu"
    evening   20:45  reel (no narrator)                "type": "reel",     "kind": "news"
Stories are OFF (STORIES_ENABLED): Make has no story module. scripts/story.py is kept for when a story route exists.

Flow: duplicate + slot check -> caption checks (hook, SEO keywords, closing question, <=5 hashtags)
    -> render (FLUX concept image, classic fallback) -> push to main (GitHub Pages) -> wait until live
    -> POST to MAKE_WEBHOOK_URL -> record in data/posted.json.
Webhook payloads:
    {"type": "photo", "image_url", "caption"}
    {"type": "carousel", "image_urls": [...], "caption"}
    {"type": "reel", "video_url", "caption"}
    {"type": "story", "image_url"}            (only when STORIES_ENABLED)

Usage:
    python3 scripts/publish.py --slot                     # which slot is it now, and is it already done?
    python3 scripts/publish.py queue/<id>.json            # full publish
    python3 scripts/publish.py queue/<id>.json --dry-run  # render post and caption only
    python3 scripts/publish.py --check queue/<id>.json    # only run the duplicate/slot check
    python3 scripts/publish.py --story-only queue/<id>.json   # (stories enabled only) retry a post's story
    python3 scripts/publish.py --cleanup                  # remove published reel videos still in the repo
    --no-story       publish the post without its story
    --regen-bg       photo/carousel: discard the cached background and generate a new one
    --no-ai          photo/carousel: no image generation (classic template / gradient)
    --regen-images   reel: generate new FLUX images
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

DUBAI = dt.timezone(dt.timedelta(hours=4))
# slot -> (time, allowed types, allowed kinds, run window start/end in Dubai time)
SLOTS = {
    "morning":   ("09:45", ("carousel",), ("news",), "08:30", "12:00"),
    "noon":      ("13:45", ("photo",), ("news", "tool"), "12:00", "15:45"),
    "afternoon": ("17:45", ("carousel",), ("edu",), "15:45", "19:15"),
    "evening":   ("20:45", ("reel", "photo"), ("news",), "19:15", "23:59"),   # photo = fallback without images
}
KEYWORDS = (2, 4)
# Make has no module to publish stories, so stories are off; set True once a Make 'story' route exists.
STORIES_ENABLED = False
EDU_FORMATS = ("tools", "prompts", "list")   # 17:45: tool roundup / prompt pack / checklist-style list

ROOT = Path(__file__).resolve().parent.parent
PAGES_BASE = "https://phernesarta-droid.github.io/ai-num1-media/"
HISTORY = ROOT / "data" / "posted.json"
PAGES_BRANCH = "main"
BG_CACHE = ROOT / "cache" / "bg"   # git-ignored; keeps the reviewed background between dry-run and publish
MAX_HASHTAGS = 5
REEL_CACHE = ROOT / "cache" / "reel"
REVIEW = ROOT / "cache" / "review"   # contact sheets for dry-run review
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


def norm_item(t):
    return re.sub(r"[\s_\-]+", " ", t.strip().lower())


def find_duplicate(post, history):
    kind = post_kind(post)
    # edu posts link tool homepages, which legitimately repeat; only news/tool posts are matched by URL
    urls = {norm_url(s["url"]) for s in post.get("sources") or []} if kind != "edu" else set()
    words = norm_text(post["headline"])
    items = {norm_item(i) for i in post.get("items") or []}
    for h in history:
        if h["id"] == post["id"]:
            return h, "same id"
        if urls and h.get("kind") != "edu" and urls & {norm_url(u) for u in h.get("source_urls", [])}:
            return h, "same source URL"
        if items and h.get("kind") == kind and h.get("items"):
            shared = items & {norm_item(i) for i in h["items"]}
            if len(shared) / len(items) >= 0.5:
                return h, f"repeats items already covered: {', '.join(sorted(shared))}"
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
    if t not in ("photo", "reel", "carousel"):
        sys.exit(f"type must be 'photo', 'carousel' or 'reel', got {t!r}")
    return t


def post_kind(post):
    return post.get("kind") or "news"


def dubai_now():
    return dt.datetime.now(DUBAI)


def slot_now(now=None):
    """The slot whose run window contains the current Dubai time, or None."""
    hm = (now or dubai_now()).strftime("%H:%M")
    for name, (_, _, _, start, end) in SLOTS.items():
        if start <= hm < end or (end == "23:59" and hm >= start):
            return name
    return None


def entry_slot(h):
    """Slot of a history entry; older entries (before slots existed) by their Dubai posting time."""
    if h.get("slot"):
        return h["slot"]
    return slot_now(dt.datetime.fromisoformat(h["posted_at"]).astimezone(DUBAI)) if h.get("posted_at") else None


def slot_done(history, date, slot):
    return next((h for h in history if entry_slot(h) == slot and (h.get("date") or h["id"][:10]) == date), None)


def check_slot(post, history):
    """The post must name a slot that fits its type/kind, and that slot must not be done yet for its date."""
    slot = post.get("slot")
    if slot not in SLOTS:
        return f"slot must be one of {', '.join(SLOTS)}"
    _, types, kinds, _, _ = SLOTS[slot]
    if post_type(post) not in types or post_kind(post) not in kinds:
        return f"slot {slot} takes type {'/'.join(types)} with kind {'/'.join(kinds)}"
    done = slot_done(history, post["date"], slot)
    if done and done["id"] != post["id"]:
        return f"SLOT DONE: {slot} on {post['date']} already published ({done['id']})"
    return None


def validate(post):
    """Caption rules: hook first, SEO keywords, question last, 1-5 specific hashtags; format-specific fields."""
    errors = []
    kw = [k.strip() for k in post.get("keywords") or [] if k.strip()]
    body = f"{post.get('hook', '')} {post.get('summary', '')}"
    if not KEYWORDS[0] <= len(kw) <= KEYWORDS[1]:
        errors.append(f"keywords: {KEYWORDS[0]}-{KEYWORDS[1]} Persian search phrases, got {len(kw)}")
    else:
        missing = [k for k in kw if k not in body]
        if missing:
            errors.append(f"keywords must appear word for word in hook or summary: {', '.join(missing)}")
        if not any(k in (post.get("hook") or "") for k in kw):
            errors.append("the hook must contain at least one of the keywords (Instagram search reads the start)")
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
    if post_kind(post) != "edu" and not post.get("sources"):
        errors.append("sources: required for news and tool posts")
    if post_kind(post) in ("edu", "tool") and not post.get("items"):
        errors.append("items: list the tools/prompts/topics covered (used to avoid repeats)")
    if post_type(post) == "carousel":
        from carousel import CarouselError, spec
        try:
            spec(post)
        except CarouselError as e:
            errors.append(str(e))
    if post_kind(post) == "edu" and post.get("format") not in EDU_FORMATS:
        errors.append(f"format: edu posts need one of {', '.join(EDU_FORMATS)}")
    if STORIES_ENABLED and post_kind(post) == "edu":
        sq = post.get("story_question") or {}
        if not (sq.get("question") or "").strip().endswith(("؟", "?")):
            errors.append("story_question.question: the 'question of the day' story text, ending in ؟")
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
    if post_type(post) == "carousel":
        parts += ["🔖 ذخیره کن تا بعداً دوباره ببینی، و برای کسی که لازمش دارد بفرست." if post_kind(post) == "edu"
                  else "👈 ورق بزن تا همهٔ جزئیات را ببینی.", ""]
    if post.get("sources"):
        parts.append("🔗 منبع:")
        for s in post["sources"]:
            parts.append(f"• {s['name']}: {s['url']}")
    if music:
        parts += ["", music_credit(music)]
    follow = {"edu": "📌 هر روز یک ترفند کاربردی هوش مصنوعی در @ai_num1",
              "tool": "📌 هر روز یک ابزار کاربردی هوش مصنوعی در @ai_num1"}
    parts += ["", follow.get(post_kind(post), "📌 خبرهای روزانه و معتبر هوش مصنوعی در @ai_num1")]
    parts += ["", "💬 " + post["question"].strip()]
    parts += ["", " ".join("#" + t for t in clean_tags(post["hashtags"]))]
    caption = "\n".join(parts).replace("\n\n\n", "\n\n")
    if len(caption) > 2200:
        raise ValueError(f"Caption too long ({len(caption)} > 2200)")
    return caption


def concept_bg(post, regen=False):
    """The FLUX background for image_prompt, cached in cache/bg/<id>.jpg (reused while the prompt is unchanged).
    Returns (path, metadata) or (None, {"error": ...}) if generation fails."""
    bg = BG_CACHE / f"{post['id']}.jpg"
    meta_file = bg.with_suffix(".json")
    cached = bg.exists() and meta_file.exists() and not regen and \
        json.loads(meta_file.read_text(encoding="utf-8")).get("prompt") == post["image_prompt"]
    try:
        if not cached:
            generate(post["image_prompt"], bg)
        return bg, json.loads(meta_file.read_text(encoding="utf-8"))
    except (ImageGenError, OSError, ValueError) as e:
        print(f"WARNING: AI image failed ({e}); falling back to the classic look", file=sys.stderr)
        return None, {"error": str(e)[:300]}


def make_image(post, out, regen=False, no_ai=False):
    """Render with a FLUX background; on any image-generation failure use the classic template."""
    if no_ai:
        render(post, out)
        return "classic", None, None
    bg, meta = concept_bg(post, regen)
    if bg:
        try:
            render_ai(post, bg, out)
            return "ai", meta, bg
        except (OSError, ValueError) as e:
            print(f"WARNING: AI card failed ({e}); falling back to classic template", file=sys.stderr)
            meta = {"error": str(e)[:300]}
    render(post, out)
    return "classic", meta, None


def make_carousel(post, out_dir, regen=False, no_ai=False):
    from carousel import contact_sheet, render_carousel
    bg, meta = (None, None) if no_ai else concept_bg(post, regen)
    paths = render_carousel(post, out_dir, bg)
    sheet = REVIEW / post["id"] / "sheet.jpg"
    sheet.parent.mkdir(parents=True, exist_ok=True)
    contact_sheet(paths, sheet)
    return ("ai" if bg else "classic"), meta, bg, paths, sheet


def make_story(post, out, card=None, show_headline=True):
    """Question-of-the-day card for edu posts, otherwise the "پست جدید 👆" intro."""
    from story import render_intro, render_question
    if post_kind(post) == "edu":
        return render_question(post, out, card), "question"
    return render_intro(post, out, card, kind=post_type(post), show_headline=show_headline), "intro"


def make_reel(post, rerender=False, regen_images=False):
    """Render the reel into cache/reel/<id>/ (git-ignored), or reuse the reviewed dry-run render if the
    post's reel fields are unchanged. Returns (info, path)."""
    from reel import post_hash, probe, render_reel
    out = REEL_CACHE / post["id"] / f"{post['id']}.mp4"
    meta_file = REEL_CACHE / post["id"] / "render.json"
    h = post_hash(post)
    if out.exists() and meta_file.exists() and not (rerender or regen_images):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        if meta.get("hash") == h and meta.get("size") == out.stat().st_size:
            print("reel: reusing the reviewed render", file=sys.stderr)
            return meta["info"], out
    kf = REEL_CACHE / post["id"] / "keyframes"
    if kf.exists():
        for f in kf.glob("*.jpg"):
            f.unlink()
    info = render_reel(post, out, regen_images=regen_images, keyframes=kf,
                       log=lambda m: print(m, file=sys.stderr))
    pr = probe(out)
    v = next(s for s in pr["streams"] if s["codec_name"] == "h264")
    if (v["width"], v["height"]) != (1080, 1920) or not 20 <= float(pr["format"]["duration"]) <= 30.5:
        raise RuntimeError(f"bad reel output: {pr}")
    info["keyframes"] = str(kf / "sheet.jpg")
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.write_text(json.dumps({"hash": h, "size": out.stat().st_size, "info": info},
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
    """payload: see the module docstring. The Make scenario's Router has one route per "type"."""
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
    ap.add_argument("--slot", action="store_true", help="print the current Dubai slot and whether it is done")
    ap.add_argument("--cleanup", action="store_true")
    ap.add_argument("--story-only", action="store_true")
    ap.add_argument("--no-story", action="store_true")
    ap.add_argument("--regen-bg", action="store_true")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--regen-images", action="store_true")
    ap.add_argument("--rerender", action="store_true")
    ap.add_argument("--grace", type=int, default=VIDEO_GRACE, help="seconds before a published video is removed")
    a = ap.parse_args()

    if a.slot:
        now = dubai_now()
        name = slot_now(now)
        info = {"dubai_time": now.strftime("%Y-%m-%d %H:%M"), "weekday": now.strftime("%A"), "slot": name}
        if name:
            t, types, kinds, _, _ = SLOTS[name]
            done = slot_done(load_history(), now.strftime("%Y-%m-%d"), name)
            info |= {"time": t, "type": types[0], "kind": "|".join(kinds), "done": bool(done),
                     "done_id": done and done["id"]}
        print(json.dumps(info, ensure_ascii=False))
        return
    if a.cleanup:
        done = cleanup_videos(load_history(), grace=a.grace)
        print("cleanup:", ", ".join(done) if done else "nothing to remove")
        return
    if not a.post:
        ap.error("post JSON path is required")

    post_path = Path(a.post)
    post = json.loads(post_path.read_text(encoding="utf-8"))
    for k in ("id", "date", "headline", "summary"):
        if not post.get(k):
            sys.exit(f"missing field: {k}")
    if not all(s.get("url", "").startswith("http") for s in post.get("sources") or []):
        sys.exit("every source needs a full URL")

    history = load_history()
    if a.story_only:
        if not STORIES_ENABLED:
            sys.exit("stories are disabled (STORIES_ENABLED = False in publish.py)")
        entry = next((h for h in history if h["id"] == post["id"]), None)
        if not entry:
            sys.exit(f"{post['id']} is not published yet")
        if entry.get("story_status") and entry["story_status"] < 300:
            sys.exit(f"story already published for {post['id']}")
        publish_story(post, entry, history, a)
        return

    dup, why = find_duplicate(post, history)
    if dup:
        print(f"DUPLICATE ({why}) of {dup['id']} posted {dup.get('posted_at')}")
        sys.exit(3)
    slot_err = check_slot(post, history)
    if slot_err and slot_err.startswith("SLOT DONE"):
        print(slot_err)
        sys.exit(4)
    if a.check:
        print("OK: not a duplicate" + (f" (but: {slot_err})" if slot_err else ""))
        return

    errors = validate(post) + ([slot_err] if slot_err else [])
    if errors:
        sys.exit("invalid post:\n  - " + "\n  - ".join(errors))

    kind = post_type(post)
    y, m = post["date"][:4], post["date"][5:7]
    folder = Path("posts") / y / m
    if kind == "reel":
        publish_reel(post, post_path, history, a, folder / f"{post['id']}.mp4")
    elif kind == "carousel":
        publish_carousel(post, post_path, history, a, folder)
    else:
        publish_photo(post, post_path, history, a, folder / f"{post['id']}.jpg")


def base_entry(post, kind):
    return {
        "id": post["id"],
        "type": kind,
        "kind": post_kind(post),
        "slot": post.get("slot"),
        "date": post["date"],
        "format": post.get("format") or post_kind(post),
        "story_key": post.get("story_key"),
        "headline": post["headline"],
        "items": post.get("items") or [],
        "source_urls": [s["url"] for s in post.get("sources") or []],
        "posted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }


def show(caption, story):
    if STORIES_ENABLED:
        print("story:", story)
    print("----- caption -----\n" + caption + "\n-------------------")


def story_card(post, a, bg=None, rendered=None):
    """Render the story for review/publish; returns (local path, mode), or (None, "off") when disabled."""
    if not STORIES_ENABLED or a.no_story:
        return None, "off"
    out = REVIEW / post["id"] / "story.jpg"
    if bg:
        return make_story(post, out, bg)
    return make_story(post, out, rendered, show_headline=False)


def publish_photo(post, post_path, history, a, rel):
    image_mode, bg_meta, bg = make_image(post, ROOT / rel, regen=a.regen_bg, no_ai=a.no_ai)
    caption = build_caption(post)
    story, smode = story_card(post, a, bg, ROOT / rel)
    print("mode: ", image_mode, bg_meta or "")
    image_url = PAGES_BASE + rel.as_posix()
    print("image:", ROOT / rel)
    print("url:  ", image_url)
    show(caption, f"{story} ({smode})")
    if a.dry_run:
        return

    push_to_pages([rel, post_path], f"post: {post['id']}")
    if not wait_live(image_url):
        sys.exit(f"image never went live: {image_url}")
    status, resp = post_webhook({"type": "photo", "image_url": image_url, "caption": caption})
    print("webhook:", status, resp)
    if status >= 300:
        sys.exit("webhook failed")
    entry = base_entry(post, "photo") | {"image_url": image_url, "image_mode": image_mode, "webhook_status": status}
    history.append(entry)
    save_history(history)
    push_to_pages([HISTORY], f"log: posted {post['id']}")
    if story:
        publish_story(post, entry, history, a, story)
    print("DONE")


def publish_carousel(post, post_path, history, a, folder):
    image_mode, bg_meta, bg, paths, sheet = make_carousel(post, ROOT / folder, regen=a.regen_bg, no_ai=a.no_ai)
    rels = [folder / p.name for p in paths]
    urls = [PAGES_BASE + r.as_posix() for r in rels]
    caption = build_caption(post)
    story, smode = story_card(post, a, bg, paths[0])
    print("mode: ", image_mode, bg_meta or "")
    print("slides:", len(paths), "sheet:", sheet)
    print("urls: ", json.dumps(urls))
    show(caption, f"{story} ({smode})")
    if a.dry_run:
        return

    push_to_pages([*rels, post_path], f"carousel: {post['id']}")
    for u in urls:
        if not wait_live(u):
            sys.exit(f"slide never went live: {u}")
    status, resp = post_webhook({"type": "carousel", "image_urls": urls, "caption": caption})
    print("webhook:", status, resp)
    if status >= 300:
        sys.exit("webhook failed")
    entry = base_entry(post, "carousel") | {"image_urls": urls, "image_mode": image_mode, "webhook_status": status}
    history.append(entry)
    save_history(history)
    push_to_pages([HISTORY], f"log: posted {post['id']}")
    if story:
        publish_story(post, entry, history, a, story)
    print("DONE")


def publish_story(post, entry, history, a, story=None):
    """Push the story image, send {"type": "story", "image_url"} and record the result on the post's entry.
    A failed story never undoes the post: it prints WARNING and can be retried with --story-only."""
    if story is None:
        bg = BG_CACHE / f"{post['id']}.jpg"
        if post_type(post) == "reel":
            bg = REEL_CACHE / post["id"] / "img1.jpg"
        story, _ = story_card(post, a, bg if bg.exists() else None)
    rel = Path("posts") / post["date"][:4] / post["date"][5:7] / f"{post['id']}-story.jpg"
    shutil.copyfile(story, ROOT / rel)
    url = PAGES_BASE + rel.as_posix()
    try:
        push_to_pages([rel], f"story: {post['id']}")
        if not wait_live(url):
            raise RuntimeError(f"story image never went live: {url}")
        status, resp = post_webhook({"type": "story", "image_url": url})
    except urllib.error.HTTPError as e:
        status, resp = e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        status, resp = 0, str(e)[:300]
    print("story webhook:", status, resp)
    if not status or status >= 300:
        print(f"WARNING: story failed ({resp}); retry with: python3 scripts/publish.py --story-only <queue json>")
    entry |= {"story_url": url, "story_status": status}
    save_history(history)
    push_to_pages([HISTORY], f"log: story {post['id']}")


def publish_reel(post, post_path, history, a, rel):
    from reel import ReelError
    try:
        info, rendered = make_reel(post, rerender=a.rerender, regen_images=a.regen_images)
    except (ReelError, ImageGenError) as e:
        sys.exit(f"reel failed: {e}")
    caption = build_caption(post, music=info["music"])
    video_url = PAGES_BASE + rel.as_posix()
    img1 = REEL_CACHE / post["id"] / "img1.jpg"
    story, smode = story_card(post, a, img1)
    print("reel: ", json.dumps({k: info.get(k) for k in ("duration", "size_mb", "words_per_sec", "images")},
                               ensure_ascii=False))
    print("music:", info["music"]["title"])
    print("video:", rendered)
    print("frames:", info.get("keyframes"))
    print("url:  ", video_url)
    show(caption, f"{story} ({smode})")
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

    entry = base_entry(post, "reel") | {
        "video_url": video_url,
        "video_path": rel.as_posix(),
        "duration": info["duration"],
        "voice": False,
        "music": info["music"]["title"],
        "webhook_status": status,
    }
    history.append(entry)
    save_history(history)
    push_to_pages([HISTORY], f"log: posted {post['id']}")
    if story:
        publish_story(post, entry, history, a, story)

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
