#!/usr/bin/env python3
"""Reel renderer for @ai_num1: 1080x1920, 30 fps, H.264 + AAC MP4, 20-30 s, under 50 MB.

No narrator. Every beat is shown as big Persian subtitles that appear word by word (each word pops in, the
newest word is highlighted), over 4-5 vertical FLUX concept images with Ken Burns motion, a small zoom
punch at every beat, and a music bed from music/ (rotated per reel, mixed at Instagram loudness).

Timeline:  hook (large)  ->  headline  ->  2-3 key points  ->  closing question + @ai_num1

Queue JSON fields (in addition to the photo-post fields; see AUTOMATION.md):
    "type": "reel",
    "reel": {
        "hook":   "short on-screen opener (<= 45 chars)",
        "points": ["point 1", "point 2", "point 3"],          # 2-3, each <= 90 chars
        "image_prompts": ["English prompt", ... ]             # 4-5 different concept scenes
    }
    The headline and question come from the post's "headline" and "question". An old "reel.say" field is
    ignored.

Usage:
    python3 scripts/reel.py queue/<id>.json out.mp4 [--regen-images] [--keyframes DIR]
"""
import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from imagegen import ImageGenError, check_prompt, generate_vertical  # noqa: E402
from render import ACCENT, ACCENT2, BG_TOP, WHITE, font, tw  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MUSIC = ROOT / "music"
HISTORY = ROOT / "data" / "posted.json"
CACHE = ROOT / "cache"
VERSION = 2   # bump when the look changes, so cached renders are not reused

W, H, FPS = 1080, 1920, 30
MIN_DUR, MAX_DUR = 20.0, 30.0
MAX_BYTES = 50 * 1024 * 1024
XFADE = 0.9            # image crossfade, seconds
LEAD = 0.15            # pause before the first word of a beat
POP = 0.16             # word pop-in time
EXIT = 0.22            # beat fade-out time
HOLD = {"hook": 0.6, "headline": 1.2, "point": 1.2, "question": 2.4}   # reading time after the last word
WPI = {"hook": 0.26, "body": 0.34, "min": 0.24}                         # seconds per word
SAFE_L, SAFE_R = 90, W - 150   # keep text clear of Instagram's right-side buttons
TEXT_W = SAFE_R - SAFE_L

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


class ReelError(Exception):
    pass


# ---------------------------------------------------------------- content

def reel_spec(post):
    r = post.get("reel") or {}
    errors = []
    hook = (r.get("hook") or "").strip()
    if not hook or len(hook) > 45:
        errors.append("reel.hook: required, at most 45 characters")
    points = [p.strip() for p in r.get("points") or [] if p.strip()]
    if not 2 <= len(points) <= 3 or any(len(p) > 90 for p in points):
        errors.append("reel.points: 2-3 points, each at most 90 characters")
    prompts = [p.strip() for p in r.get("image_prompts") or [] if p.strip()]
    if not 4 <= len(prompts) <= 5:
        errors.append("reel.image_prompts: 4-5 English concept prompts")
    for p in prompts:
        bad = check_prompt(p)
        if bad:
            errors.append(f"reel.image_prompts must not name companies/products/people: {', '.join(bad)}")
    if not (post.get("question") or "").strip():
        errors.append("question: required (closing question)")
    if errors:
        raise ReelError("invalid reel:\n  - " + "\n  - ".join(errors))
    segs = [{"kind": "hook", "text": hook}, {"kind": "headline", "text": post["headline"].strip()}]
    for i, p in enumerate(points):
        segs.append({"kind": "point", "n": i + 1, "of": len(points), "text": p})
    segs.append({"kind": "question", "text": post["question"].strip()})
    return segs, prompts


# ---------------------------------------------------------------- images

def story_images(post, prompts, regen=False):
    """4-5 vertical images, cached in cache/reel/<id>/ so the dry-run review matches the publish."""
    out = []
    d = CACHE / "reel" / post["id"]
    for i, prompt in enumerate(prompts):
        img = d / f"img{i + 1}.jpg"
        meta = img.with_suffix(".json")
        ok = img.exists() and meta.exists() and not regen and \
            json.loads(meta.read_text(encoding="utf-8")).get("prompt") == prompt
        if not ok:
            generate_vertical(prompt, img)
        out.append(img)
    return out


def cover(img, w, h):
    scale = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


class KenBurns:
    """Slow zoom + drift over one still, sampled with sub-pixel accuracy (no zoompan jitter)."""

    def __init__(self, path, index):
        src = Image.open(path).convert("RGB")
        # work on a 9:16 crop at a bit more than output size, so a 12% zoom stays sharp
        self.src = cover(src, int(W * 1.12), int(H * 1.12))
        zin = index % 2 == 0
        self.z0, self.z1 = (1.0, 1.12) if zin else (1.12, 1.0)
        dirs = [(0, -1), (1, 0), (0, 1), (-1, 0), (1, -1)]
        self.dx, self.dy = dirs[index % len(dirs)]

    def frame(self, p, punch=1.0):
        """p: 0..1 progress through this image's time on screen; punch: extra zoom factor (beat accent)."""
        z = (self.z0 + (self.z1 - self.z0) * p) * punch
        sw, sh = self.src.size
        k = min(sw / W, sh / H) / z          # source pixels per output pixel
        vw, vh = W * k, H * k                # visible window in source pixels
        mx, my = (sw - vw) / 2, (sh - vh) / 2  # free room to pan
        cx = sw / 2 + self.dx * mx * 0.8 * (p - 0.5)
        cy = sh / 2 + self.dy * my * 0.8 * (p - 0.5)
        return self.src.transform((W, H), Image.AFFINE, (k, 0, cx - vw / 2, 0, k, cy - vh / 2),
                                  resample=Image.BICUBIC)


# ---------------------------------------------------------------- text layers

DARK = (6, 8, 22)


def wrap_words(d, words, f, max_w):
    """Greedy line breaking over a word list; returns a list of word lists."""
    lines, cur = [], []
    for w in words:
        trial = " ".join(cur + [w])
        if cur and tw(d, trial, f) > max_w:
            lines.append(cur)
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def word_sprite(word, f, active):
    """One word as an RGBA sprite: white with a dark outline and soft shadow, or (active) dark on an accent pill."""
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    w = int(tw(probe, word, f))
    pad_x, pad_y = 26, 18
    sw, sh = w + 2 * pad_x, int(f.size * 1.45) + 2 * pad_y
    im = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    if active:
        ImageDraw.Draw(im).rounded_rectangle((4, 8, sw - 4, sh - 4), radius=int(f.size * 0.38), fill=ACCENT)
        ImageDraw.Draw(im).text((pad_x, pad_y - f.size * 0.05), word, font=f, fill=BG_TOP, direction="rtl",
                                language="fa")
        return im
    shadow = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((pad_x, pad_y + 6), word, font=f, fill=(0, 0, 0, 200), direction="rtl",
                                language="fa", stroke_width=8, stroke_fill=(0, 0, 0, 200))
    im.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(8)))
    ImageDraw.Draw(im).text((pad_x, pad_y), word, font=f, fill=WHITE, direction="rtl", language="fa",
                            stroke_width=max(3, f.size // 16), stroke_fill=DARK)
    return im


class Caption:
    """Word-by-word subtitle for one beat: fixed layout, words revealed over time."""

    def __init__(self, text, weight, sizes, max_lines, max_w=TEXT_W):
        d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        words = text.split()
        for size in sizes:
            f = font(weight, size)
            lines = wrap_words(d, words, f, max_w)
            if len(lines) <= max_lines:
                break
        else:
            raise ReelError(f"text too long for the reel: {text[:50]}...")
        self.f = f
        self.line_h = int(f.size * 1.62)
        self.height = self.line_h * len(lines)
        space = tw(d, " ", f) + 8
        cx = (SAFE_L + SAFE_R) / 2
        self.words = []   # (normal sprite, active sprite, x of the word's left edge, line index)
        for li, line in enumerate(lines):
            widths = [tw(d, w, f) for w in line]
            right = cx + (sum(widths) + space * (len(line) - 1)) / 2
            for w, wd in zip(line, widths):       # RTL: first word at the right
                left = right - wd
                self.words.append((word_sprite(w, f, False), word_sprite(w, f, True), left, li))
                right = left - space
        self.n = len(self.words)

    def draw(self, frame, top, shown, since_last, alpha=1.0, dy=0):
        """shown: number of words visible; since_last: seconds since the newest word appeared."""
        for i, (normal, active, left, li) in enumerate(self.words[:shown]):
            newest = i == shown - 1 and since_last is not None
            spr = active if newest else normal
            a = alpha
            sc = 1.0
            if newest and since_last < POP:
                k = ease(since_last / POP)
                sc = 1.3 - 0.3 * k
                a *= min(1.0, since_last / (POP * 0.6))
            if sc != 1.0:
                spr = spr.resize((max(1, int(spr.width * sc)), max(1, int(spr.height * sc))), Image.BILINEAR)
            x = left - 26 - (spr.width - normal.width) / 2
            y = top + li * self.line_h - 18 - (spr.height - normal.height) / 2 + dy
            frame.alpha_composite(with_alpha(spr, a), (int(x), int(y)))


def text_block(lines, f, fill, line_h):
    """Centered RTL lines on a transparent layer, with a soft shadow."""
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    widths = [tw(probe, ln, f) for ln in lines]
    bw, bh = int(max(widths)) + 60, line_h * len(lines) + 60
    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))

    def draw(dr, color):
        for i, (ln, w) in enumerate(zip(lines, widths)):
            dr.text(((bw - w) / 2, 30 + i * line_h), ln, font=f, fill=color, direction="rtl", language="fa")
    shadow = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    draw(ImageDraw.Draw(shadow), (0, 0, 0, 230))
    layer = Image.alpha_composite(layer, shadow.filter(ImageFilter.GaussianBlur(9)))
    draw(ImageDraw.Draw(layer), fill)
    return layer


def text_block_latin(text, f, fill):
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    w = int(probe.textlength(text, font=f)) + 60
    h = int(f.size * 1.5) + 40
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(sh).text((30, 20), text, font=f, fill=(0, 0, 0, 230))
    layer = Image.alpha_composite(layer, sh.filter(ImageFilter.GaussianBlur(9)))
    ImageDraw.Draw(layer).text((30, 20), text, font=f, fill=fill)
    return layer


def accent_bar():
    b = Image.new("RGBA", (250, 12), (0, 0, 0, 0))
    dr = ImageDraw.Draw(b)
    dr.rounded_rectangle((90, 0, 249, 11), radius=6, fill=ACCENT)
    dr.rounded_rectangle((0, 0, 74, 11), radius=6, fill=ACCENT2)
    return b


def badge(n, of):
    f = font("Black", 44)
    t = f"{n}/{of}".translate(FA_DIGITS)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    w = int(probe.textlength(t, font=f)) + 60
    b = Image.new("RGBA", (w, 82), (0, 0, 0, 0))
    dr = ImageDraw.Draw(b)
    dr.rounded_rectangle((0, 0, w - 1, 81), radius=41, fill=ACCENT2)
    dr.text((w / 2, 41), t, font=f, fill=WHITE, anchor="mm")
    return b


def tag_pill(text):
    f = font("Bold", 38)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    w = int(tw(probe, text, f)) + 64
    b = Image.new("RGBA", (w, 72), (0, 0, 0, 0))
    dr = ImageDraw.Draw(b)
    dr.rounded_rectangle((0, 0, w - 1, 71), radius=36, fill=ACCENT2)
    dr.text((w - 32 - tw(dr, text, f), 8), text, font=f, fill=WHITE, direction="rtl", language="fa")
    return b


def cta_layer():
    cf = font("Bold", 44)
    parts = [accent_bar(),
             text_block(["نظرت رو کامنت کن و دنبال کن"], cf, (220, 226, 245), int(cf.size * 1.6)),
             text_block_latin("@ai_num1", font("Black", 72), ACCENT)]
    w = max(p.width for p in parts)
    h = sum(p.height for p in parts) + 10 * (len(parts) - 1)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    y = 0
    for p in parts:
        out.alpha_composite(p, ((w - p.width) // 2, y))
        y += p.height + 10
    return out


def static_overlay():
    """Vignette for legibility + brand mark (top). Kept out of Instagram's top/bottom UI zones."""
    col = Image.new("L", (1, H))
    for y in range(H):
        t = y / (H - 1)
        a = 120 * max(0, 1 - t / 0.18) + 80 + 110 * max(0, (t - 0.62) / 0.38)
        col.putpixel((0, y), int(min(a, 235)))
    shade = Image.new("RGBA", (W, H), (6, 8, 22, 0))
    shade.putalpha(col.resize((W, H)))
    d = ImageDraw.Draw(shade)
    d.text((SAFE_L, 190), "@ai_num1", font=font("Black", 40), fill=WHITE)
    d.rectangle((SAFE_L, 248, SAFE_L + 70, 254), fill=ACCENT)
    return shade


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def with_alpha(layer, a):
    if a >= 0.999:
        return layer
    out = layer.copy()
    out.putalpha(layer.getchannel("A").point(lambda v: int(v * a)))
    return out


# ---------------------------------------------------------------- beats

class Beat:
    def __init__(self, seg, tag):
        k = seg["kind"]
        self.kind = k
        self.above = None      # small layer above the caption (tag pill / point badge)
        self.below = None      # layer that fades in under the caption once all words are shown
        if k == "hook":
            self.cap = Caption(seg["text"], "Black", [124, 116, 108, 100, 92, 84], 3)
            self.below = accent_bar()
        elif k == "headline":
            self.cap = Caption(seg["text"], "Black", [86, 80, 74, 68, 62], 5)
            self.above = tag_pill(tag)
        elif k == "point":
            self.cap = Caption(seg["text"], "Black", [84, 78, 72, 66, 60], 5)
            self.above = badge(seg["n"], seg["of"])
        else:
            self.cap = Caption(seg["text"], "Black", [80, 74, 68, 62, 58], 5)
            self.below = cta_layer()
        self.n = self.cap.n

    def place(self):
        """Vertical position of the caption block (centered with its extras, slightly above the middle)."""
        h = self.cap.height + (self.above.height + 34 if self.above else 0) + \
            (self.below.height + 30 if self.below else 0)
        top = (H - h) / 2 - 60
        cap_top = top + (self.above.height + 34 if self.above else 0)
        return top, cap_top

    def draw(self, frame, t, st, en, wpi, last):
        top, cap_top = self.place()
        local = t - st
        a_out = 1.0 if last else ease((en - t) / EXIT)
        dy = 0 if last else int((1 - a_out) * -40)
        cx = (SAFE_L + SAFE_R) // 2
        if self.above is not None:
            a = ease(local / 0.25) * a_out
            frame.alpha_composite(with_alpha(self.above, a), (cx - self.above.width // 2,
                                                              int(top + (1 - ease(local / 0.25)) * 30) + dy))
        k = local - LEAD
        shown = 0 if k < 0 else min(self.n, int(k / wpi) + 1)
        since = None
        if shown:
            since = k - (shown - 1) * wpi
            if shown == self.n and since > wpi:    # all words read: no highlight
                since = None
        self.cap.draw(frame, cap_top, shown, since, alpha=a_out, dy=dy)
        if self.below is not None and shown == self.n:
            done = LEAD + self.n * wpi
            a = ease((local - done) / 0.3) * a_out
            frame.alpha_composite(with_alpha(self.below, a), (cx - self.below.width // 2,
                                                              int(cap_top + self.cap.height + 30) + dy))


def pick_track():
    tracks = json.loads((MUSIC / "tracks.json").read_text(encoding="utf-8"))
    hist = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
    n = sum(1 for h in hist if h.get("type") == "reel")
    return tracks[n % len(tracks)]


def plan(beats):
    """Seconds per word and start/end of every beat; total clamped to 20-30 s."""
    def lengths(wpi_body, hold_scale):
        out = []
        for b in beats:
            wpi = WPI["hook"] if b.kind == "hook" else wpi_body
            out.append((wpi, LEAD + b.n * wpi + HOLD[b.kind] * hold_scale))
        return out
    wpi_body, hold_scale = WPI["body"], 1.0
    durs = lengths(wpi_body, hold_scale)
    while sum(d for _, d in durs) + 0.8 > MAX_DUR:
        if wpi_body > WPI["min"]:
            wpi_body = max(WPI["min"], wpi_body - 0.02)
        elif hold_scale > 0.6:
            hold_scale -= 0.1
        else:
            words = sum(b.n for b in beats)
            raise ReelError(f"reel text too long ({words} words): shorten reel.points / headline / question")
        durs = lengths(wpi_body, hold_scale)
    total = sum(d for _, d in durs) + 0.8          # tail: last frame holds while music fades
    if total < MIN_DUR:                             # give the body beats more reading time
        extra = (MIN_DUR - total) / (len(durs) - 1)
        durs = [durs[0]] + [(w, d + extra) for w, d in durs[1:]]
        total = MIN_DUR
    t, timing = 0.0, []
    for (wpi, d) in durs:
        timing.append((t, t + d, wpi))
        t += d
    total = round(total, 2)
    timing[-1] = (timing[-1][0], total, timing[-1][2])   # the closing question stays until the end
    return timing, total


# ---------------------------------------------------------------- render

def render_reel(post, out_path, regen_images=False, keyframes=None, log=print):
    segs, prompts = reel_spec(post)
    images = story_images(post, prompts, regen=regen_images)
    tag = post.get("tag") or "خبر داغ هوش مصنوعی"
    beats = [Beat(s, tag) for s in segs]
    timing, total = plan(beats)

    nframes = int(round(total * FPS))
    n = len(images)
    span = (total + (n - 1) * XFADE) / n
    shots = [(i * (span - XFADE), i * (span - XFADE) + span, KenBurns(p, i)) for i, p in enumerate(images)]
    overlay = static_overlay()
    track = pick_track()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    music_in = ["-ss", str(track.get("start", 0)), "-t", f"{total:.2f}", "-i", str(MUSIC / track["file"])]
    # music is the only audio now: Instagram loudness (-14 LUFS), short fade in, 2 s fade out
    afilter = (f"[1:a]aresample=48000,afade=t=in:d=0.5,afade=t=out:st={total - 2.0:.2f}:d=2.0,"
               f"loudnorm=I=-14:TP=-1.0:LRA=11,aformat=channel_layouts=stereo[a]")
    kf_times = keyframe_times(timing, beats) if keyframes else {}

    def encode(crf):
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
               "-r", str(FPS), "-i", "-", *music_in, "-filter_complex", afilter,
               "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
               "-maxrate", "8M", "-bufsize", "16M", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
               "-g", "60", "-r", str(FPS), "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
               "-movflags", "+faststart", "-t", f"{total:.3f}", str(out_path)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        for fi in range(nframes):
            t = fi / FPS
            # zoom punch at the start of every beat
            punch = 1.0
            for st, _, _ in timing:
                if 0 <= t - st < 0.4:
                    punch = 1.0 + 0.045 * (1 - ease((t - st) / 0.4))
            frame = None
            for s0, s1, kb in shots:
                if s0 - 1e-6 <= t <= s1:
                    img = kb.frame((t - s0) / (s1 - s0), punch)
                    if frame is None:
                        frame = img
                    else:  # crossfade into the next shot
                        a = (t - s0) / XFADE
                        frame = Image.blend(frame, img, 0.5 - 0.5 * math.cos(math.pi * min(a, 1)))
            frame = frame.convert("RGBA")
            frame.alpha_composite(overlay)
            for i, ((st, en, wpi), beat) in enumerate(zip(timing, beats)):
                if st <= t < en or (i == len(beats) - 1 and t >= st):
                    beat.draw(frame, t, st, en, wpi, last=i == len(beats) - 1)
            rgb = frame.convert("RGB")
            proc.stdin.write(rgb.tobytes())
            for name, kt in kf_times.items():
                if fi == int(kt * FPS):
                    Path(keyframes).mkdir(parents=True, exist_ok=True)
                    rgb.save(Path(keyframes) / f"{name}.jpg", quality=90)
        proc.stdin.close()
        if proc.wait() != 0:
            raise ReelError("ffmpeg failed")

    crf = 21
    encode(crf)
    while out_path.stat().st_size > MAX_BYTES and crf < 30:
        crf += 3
        log(f"file too large ({out_path.stat().st_size / 1e6:.1f} MB); re-encoding at crf {crf}")
        encode(crf)
    if out_path.stat().st_size > MAX_BYTES:
        raise ReelError("video is over 50 MB")
    if keyframes:
        contact_sheet(keyframes)

    return {"duration": total, "size_mb": round(out_path.stat().st_size / 1e6, 2), "crf": crf,
            "music": track, "words_per_sec": round(1 / timing[1][2], 2),
            "images": [json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))["model"] for p in images],
            "segments": [{"kind": s["kind"], "start": round(a, 2), "end": round(b, 2)}
                         for s, (a, b, _) in zip(segs, timing)]}


def keyframe_times(timing, beats):
    """One review frame per beat, just after its last word appeared (full text, newest word highlighted)."""
    names = ["1-hook", "2-headline"] + [f"{i + 3}-point{i + 1}" for i in range(len(timing) - 3)] + \
        [f"{len(timing)}-question"]
    out = {}
    for name, (a, b, wpi), beat in zip(names, timing, beats):
        t = a + LEAD + (beat.n - 1) * wpi + POP + 0.04 + (0.6 if beat.kind == "question" else 0)
        out[name] = min(t, b - EXIT - 0.05)
    return out


def contact_sheet(kf_dir):
    """All keyframes side by side in <kf_dir>/sheet.jpg, for a quick visual review."""
    files = sorted(p for p in Path(kf_dir).glob("*.jpg") if p.name != "sheet.jpg")
    if not files:
        return None
    w, h, cols = 360, 640, 3
    rows = math.ceil(len(files) / cols)
    sheet = Image.new("RGB", (cols * (w + 10) + 10, rows * (h + 10) + 10), (20, 20, 30))
    for i, f in enumerate(files):
        sheet.paste(Image.open(f).resize((w, h)), (10 + (i % cols) * (w + 10), 10 + (i // cols) * (h + 10)))
    out = Path(kf_dir) / "sheet.jpg"
    sheet.save(out, quality=88)
    return out


def post_hash(post):
    keys = ("headline", "question", "tag", "reel")
    blob = json.dumps({k: post.get(k) for k in keys} | {"v": VERSION}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration,size:stream=codec_name,width,height,r_frame_rate,sample_rate",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("post")
    ap.add_argument("out")
    ap.add_argument("--regen-images", action="store_true")
    ap.add_argument("--keyframes")
    a = ap.parse_args()
    post = json.loads(Path(a.post).read_text(encoding="utf-8"))
    try:
        info = render_reel(post, a.out, regen_images=a.regen_images, keyframes=a.keyframes)
    except (ReelError, ImageGenError) as e:
        sys.exit(f"ERROR: {e}")
    print(json.dumps(info, ensure_ascii=False, indent=2))
