#!/usr/bin/env python3
"""Reel renderer for @ai_num1: 1080x1920, 30 fps, H.264 + AAC MP4, 20-30 s, under 50 MB.

Timeline (Persian text in Vazirmatn, animated in and out):
    hook (0-2 s, large)  ->  headline  ->  2-3 key points  ->  closing question + @ai_num1
Background: 4-5 vertical FLUX concept images made for this story, with slow Ken Burns zoom/pan and soft
crossfades. Audio: Persian narration with Piper (optional; skipped if Piper or the voice is unavailable
or the synthesized audio looks broken) over a quiet music bed from music/ (rotated per reel).

Queue JSON fields (in addition to the photo-post fields; see AUTOMATION.md):
    "type": "reel",
    "reel": {
        "hook":   "short on-screen opener (<= 45 chars)",
        "points": ["point 1", "point 2", "point 3"],          # 2-3, each <= 90 chars
        "image_prompts": ["English prompt", ... ],            # 4-5 different concept scenes
        "say": {                                              # optional: what the narrator says,
            "hook": "...", "headline": "...",                 # when on-screen text has English names
            "points": ["...", ...], "question": "..."         # or numbers Piper reads badly
        }
    }
    The headline and question come from the post's "headline" and "question".

Usage:
    python3 scripts/reel.py queue/<id>.json out.mp4 [--no-voice] [--regen-images] [--keyframes DIR]
"""
import argparse
import array
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from imagegen import ImageGenError, check_prompt, generate_vertical  # noqa: E402
from render import ACCENT, ACCENT2, BG_TOP, WHITE, font, tw  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MUSIC = ROOT / "music"
HISTORY = ROOT / "data" / "posted.json"
CACHE = ROOT / "cache"
VERSION = 1   # bump when the look changes, so cached renders are not reused

W, H, FPS = 1080, 1920, 30
MIN_DUR, MAX_DUR = 20.0, 30.0
MAX_BYTES = 50 * 1024 * 1024
XFADE = 0.9            # image crossfade, seconds
HOOK_DUR = 2.0         # hook stays 0-2 s (longer only if its narration needs it)
TEXT_IN, TEXT_OUT = 0.35, 0.25
SAFE_L, SAFE_R = 90, W - 150   # keep text clear of Instagram's right-side buttons
TEXT_W = SAFE_R - SAFE_L

VOICE = os.environ.get("REEL_VOICE", "fa_IR-ganji-medium")
VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/fa/fa_IR/{name}/medium/{file}"

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
    say = r.get("say") or {}
    if say.get("points") and len(say["points"]) != len(points):
        errors.append("reel.say.points must have one entry per point")
    if errors:
        raise ReelError("invalid reel:\n  - " + "\n  - ".join(errors))
    segs = [{"kind": "hook", "text": hook, "say": say.get("hook") or hook},
            {"kind": "headline", "text": post["headline"].strip(), "say": say.get("headline") or post["headline"]}]
    for i, p in enumerate(points):
        segs.append({"kind": "point", "n": i + 1, "of": len(points), "text": p,
                     "say": (say.get("points") or points)[i]})
    segs.append({"kind": "question", "text": post["question"].strip(), "say": say.get("question") or post["question"]})
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

    def frame(self, p):
        """p: 0..1 progress through this image's time on screen."""
        z = self.z0 + (self.z1 - self.z0) * p
        sw, sh = self.src.size
        k = min(sw / W, sh / H) / z          # source pixels per output pixel
        vw, vh = W * k, H * k                # visible window in source pixels
        mx, my = (sw - vw) / 2, (sh - vh) / 2  # free room to pan
        cx = sw / 2 + self.dx * mx * 0.8 * (p - 0.5)
        cy = sh / 2 + self.dy * my * 0.8 * (p - 0.5)
        return self.src.transform((W, H), Image.AFFINE, (k, 0, cx - vw / 2, 0, k, cy - vh / 2),
                                  resample=Image.BICUBIC)


# ---------------------------------------------------------------- text layers

def wrap(d, text, f, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and tw(d, trial, f) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def fit(d, text, weight, sizes, max_w, max_lines):
    for size in sizes:
        f = font(weight, size)
        lines = wrap(d, text, f, max_w)
        if len(lines) <= max_lines:
            return f, lines
    raise ReelError(f"text too long for the reel: {text[:50]}...")


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
    shadow = shadow.filter(ImageFilter.GaussianBlur(9))
    layer = Image.alpha_composite(layer, shadow)
    draw(ImageDraw.Draw(layer), fill)
    return layer


def panel(w, h, alpha=150):
    p = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(p).rounded_rectangle((0, 0, w - 1, h - 1), radius=40, fill=(8, 10, 28, alpha),
                                        outline=(255, 255, 255, 40), width=2)
    return p


def stack(parts, gap=24, pad=(56, 48), bg=True):
    """Stack layers vertically, centered, optionally on a dark rounded panel."""
    w = max(p.width for p in parts) + 2 * pad[0]
    w = min(max(w, 560), TEXT_W + 2 * 30)
    h = sum(p.height for p in parts) + gap * (len(parts) - 1) + 2 * pad[1]
    out = panel(w, h) if bg else Image.new("RGBA", (w, h), (0, 0, 0, 0))
    y = pad[1]
    for p in parts:
        out.alpha_composite(p, ((w - p.width) // 2, y))
        y += p.height + gap
    return out


def accent_bar():
    b = Image.new("RGBA", (250, 12), (0, 0, 0, 0))
    dr = ImageDraw.Draw(b)
    dr.rounded_rectangle((90, 0, 249, 11), radius=6, fill=ACCENT)
    dr.rounded_rectangle((0, 0, 74, 11), radius=6, fill=ACCENT2)
    return b


def badge(n, of):
    f = font("Black", 40)
    t = f"{n}/{of}".translate(FA_DIGITS)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    w = int(probe.textlength(t, font=f)) + 56
    b = Image.new("RGBA", (w, 76), (0, 0, 0, 0))
    dr = ImageDraw.Draw(b)
    dr.rounded_rectangle((0, 0, w - 1, 75), radius=38, fill=ACCENT)
    dr.text((w / 2, 38), t, font=f, fill=BG_TOP, anchor="mm")
    return b


def segment_layer(seg, tag):
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    k = seg["kind"]
    if k == "hook":
        f, lines = fit(d, seg["text"], "Black", [112, 104, 96, 88, 80], TEXT_W, 3)
        return stack([text_block(lines, f, WHITE, int(f.size * 1.45)), accent_bar()], bg=False), "center"
    if k == "headline":
        f, lines = fit(d, seg["text"], "Black", [76, 70, 66, 62, 58], TEXT_W - 80, 5)
        tf = font("Bold", 34)
        tag_l = text_block([tag], tf, ACCENT, int(tf.size * 1.5))
        return stack([tag_l, text_block(lines, f, WHITE, int(f.size * 1.5)), accent_bar()], gap=10), "center"
    if k == "point":
        f, lines = fit(d, seg["text"], "Bold", [64, 60, 56, 52, 48], TEXT_W - 80, 5)
        return stack([badge(seg["n"], seg["of"]), text_block(lines, f, WHITE, int(f.size * 1.6))], gap=8), "center"
    # question + handle
    f, lines = fit(d, seg["text"], "Bold", [60, 56, 52, 48, 44], TEXT_W - 80, 5)
    hf = font("Black", 64)
    cf = font("Medium", 36)
    return stack([text_block(lines, f, WHITE, int(f.size * 1.6)),
                  accent_bar(),
                  text_block(["نظرت رو کامنت کن و دنبال کن"], cf, (200, 206, 235), int(cf.size * 1.6)),
                  text_block_latin("@ai_num1", hf, ACCENT)], gap=16), "center"


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


def static_overlay():
    """Vignette for legibility + brand mark (top). Kept out of Instagram's top/bottom UI zones."""
    col = Image.new("L", (1, H))
    for y in range(H):
        t = y / (H - 1)
        a = 120 * max(0, 1 - t / 0.18) + 70 + 110 * max(0, (t - 0.62) / 0.38)
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


# ---------------------------------------------------------------- audio

def piper_available():
    if shutil.which("piper"):
        return True
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "piper-tts", "--break-system-packages"],
                       capture_output=True, text=True)
    return r.returncode == 0 and shutil.which("piper") is not None


def voice_model():
    d = CACHE / "piper"
    d.mkdir(parents=True, exist_ok=True)
    name = VOICE.split("-")[1]
    paths = []
    for ext in (".onnx", ".onnx.json"):
        p = d / f"{VOICE}{ext}"
        if not p.exists() or p.stat().st_size < 1000:
            tmp = p.with_suffix(p.suffix + ".part")
            urllib.request.urlretrieve(VOICE_URL.format(name=name, file=p.name), tmp)
            tmp.rename(p)
        paths.append(p)
    return paths[0]


def tts(text, out, model, length_scale):
    r = subprocess.run(["piper", "-m", str(model), "-f", str(out), "--length-scale", f"{length_scale:.2f}",
                        "--sentence-silence", "0.15"], input=text, text=True, capture_output=True, timeout=120)
    if r.returncode != 0 or not out.exists():
        raise ReelError(f"piper failed: {r.stderr[-300:]}")
    with wave.open(str(out)) as w:
        n, rate = w.getnframes(), w.getframerate()
        pcm = array.array("h", w.readframes(n))
    dur = n / rate
    # sanity: plausible speaking rate, not silent, not clipped to pieces
    chars = len(text.replace(" ", ""))
    peak = max((abs(v) for v in pcm), default=0)
    rms = math.sqrt(sum(v * v for v in pcm[::7]) / max(1, len(pcm[::7])))
    if not (0.03 * chars <= dur <= 0.2 * chars + 1.0) or peak < 2000 or rms < 300:
        raise ReelError(f"narration looks broken (dur {dur:.1f}s for {chars} chars, peak {peak}, rms {rms:.0f})")
    return dur, rate, pcm


def pick_track():
    tracks = json.loads((MUSIC / "tracks.json").read_text(encoding="utf-8"))
    hist = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
    n = sum(1 for h in hist if h.get("type") == "reel")
    return tracks[n % len(tracks)]


# ---------------------------------------------------------------- timeline

def plan(segs, voice_durs):
    """Start/end for every text segment; total length clamped to 20-30 s."""
    base = {"hook": HOOK_DUR, "headline": 4.2, "point": 4.4, "question": 4.8}
    gap = 0.35
    durs = []
    for s, vd in zip(segs, voice_durs):
        d = base[s["kind"]]
        if vd is not None:
            d = max(d if s["kind"] == "hook" else 2.5, vd + (0.15 if s["kind"] == "hook" else gap + 0.4))
        durs.append(d)
    total = sum(durs) + 1.2       # tail: last frame holds while music fades
    if total < MIN_DUR:            # stretch body segments (not the hook) to reach 20 s
        extra = MIN_DUR - total
        body = len(durs) - 1
        durs = [durs[0]] + [d + extra / body for d in durs[1:]]
        total = MIN_DUR
    t, out = 0.0, []
    for s, d in zip(segs, durs):
        out.append((t, t + d))
        t += d
    total = round(total, 2)
    out[-1] = (out[-1][0], total)   # the closing question stays until the end
    return out, total


def build_voice_track(clips, total, rate, path):
    buf = array.array("h", bytes(2 * int(total * rate)))
    for start, pcm in clips:
        i = int(start * rate)
        seg = pcm[: max(0, len(buf) - i)]
        buf[i:i + len(seg)] = seg
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(buf.tobytes())


# ---------------------------------------------------------------- render

def render_reel(post, out_path, voice=True, regen_images=False, keyframes=None, log=print):
    segs, prompts = reel_spec(post)
    images = story_images(post, prompts, regen=regen_images)
    tag = post.get("tag") or "خبر داغ هوش مصنوعی"

    # narration (optional)
    voice_info, clips, voice_durs, rate = None, [], [None] * len(segs), 22050
    tmp = Path(tempfile.mkdtemp(prefix="reel-"))
    if voice:
        try:
            if not piper_available():
                raise ReelError("piper is not installed")
            model = voice_model()
            for ls in (0.95, 0.88, 0.82):
                res = [tts(s["say"], tmp / f"v{i}.wav", model, ls) for i, s in enumerate(segs)]
                voice_durs = [r[0] for r in res]
                rate = res[0][1]
                timing, total = plan(segs, voice_durs)
                if total <= MAX_DUR:
                    break
            else:
                raise ReelError(f"narration too long even at faster speed ({total:.1f}s > {MAX_DUR}s)")
            lead = {"hook": 0.1}
            clips = [(st + lead.get(s["kind"], 0.3), r[2]) for (st, _), s, r in zip(timing, segs, res)]
            voice_info = {"voice": VOICE, "length_scale": ls}
        except Exception as e:  # any narration problem: publish without the narrator
            log(f"WARNING: narration skipped ({e})")
            voice_durs, clips, voice_info = [None] * len(segs), [], None
    timing, total = plan(segs, voice_durs)
    if total > MAX_DUR:
        raise ReelError(f"reel too long ({total:.1f}s); shorten the points")

    nframes = int(round(total * FPS))
    # images: equal shares with crossfades
    n = len(images)
    span = (total + (n - 1) * XFADE) / n
    shots = [(i * (span - XFADE), i * (span - XFADE) + span, KenBurns(p, i)) for i, p in enumerate(images)]
    overlay = static_overlay()
    layers = [segment_layer(s, tag)[0] for s in segs]

    track = pick_track()
    audio_wav = tmp / "voice.wav"
    if clips:
        build_voice_track(clips, total, rate, audio_wav)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    music_in = ["-ss", str(track.get("start", 0)), "-t", f"{total:.2f}", "-i", str(MUSIC / track["file"])]
    fade = f"afade=t=in:d=0.8,afade=t=out:st={total - 2.0:.2f}:d=2.0"
    if clips:
        audio_in = music_in + ["-i", str(audio_wav)]
        afilter = (f"[1:a]aresample=48000,{fade},loudnorm=I=-26:TP=-3,volume=0.9[m];"
                   f"[2:a]aresample=48000,loudnorm=I=-16:TP=-1.5,asplit=2[v][vs];"
                   f"[m][vs]sidechaincompress=threshold=0.03:ratio=5:attack=30:release=500[md];"
                   f"[md][v]amix=inputs=2:normalize=0,alimiter=limit=0.95,aformat=channel_layouts=stereo[a]")
    else:
        audio_in = music_in
        afilter = f"[1:a]aresample=48000,{fade},loudnorm=I=-16:TP=-1.5,aformat=channel_layouts=stereo[a]"

    def encode(crf):
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
               "-r", str(FPS), "-i", "-", *audio_in, "-filter_complex", afilter,
               "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
               "-maxrate", "8M", "-bufsize", "16M", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
               "-g", "60", "-r", str(FPS), "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
               "-movflags", "+faststart", "-t", f"{total:.3f}", str(out_path)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        kf_times = keyframe_times(timing) if keyframes else {}
        for fi in range(nframes):
            t = fi / FPS
            frame = None
            for s0, s1, kb in shots:
                if s0 - 1e-6 <= t <= s1:
                    img = kb.frame((t - s0) / (s1 - s0))
                    if frame is None:
                        frame = img
                    else:  # crossfade into the next shot
                        a = (t - s0) / XFADE
                        frame = Image.blend(frame, img, 0.5 - 0.5 * math.cos(math.pi * min(a, 1)))
            frame = frame.convert("RGBA")
            frame.alpha_composite(overlay)
            for (st, en), layer, s in zip(timing, layers, segs):
                if st <= t < en:
                    a_in = ease((t - st) / TEXT_IN)
                    a_out = 1.0 if s is segs[-1] else ease((en - t) / TEXT_OUT)
                    a = min(a_in, a_out)
                    dy = int((1 - a_in) * 60)
                    lay = layer
                    if s["kind"] == "hook" and a_in < 1:   # small pop for the hook
                        sc = 0.9 + 0.1 * a_in
                        lay = layer.resize((max(1, int(layer.width * sc)), max(1, int(layer.height * sc))),
                                           Image.BILINEAR)
                    x = (W - lay.width) // 2 + (SAFE_L - (W - SAFE_R)) // 2
                    y = (H - lay.height) // 2 - 40 + dy
                    frame.alpha_composite(with_alpha(lay, a), (x, y))
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
    shutil.rmtree(tmp, ignore_errors=True)
    if keyframes:
        contact_sheet(keyframes)

    info = {"duration": total, "size_mb": round(out_path.stat().st_size / 1e6, 2), "crf": crf,
            "voice": voice_info, "music": track,
            "images": [json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))["model"] for p in images],
            "segments": [{"kind": s["kind"], "start": round(a, 2), "end": round(b, 2)} for s, (a, b) in zip(segs, timing)]}
    return info


def keyframe_times(timing):
    names = ["1-hook", "2-headline"] + [f"{i + 3}-point{i + 1}" for i in range(len(timing) - 3)] + \
        [f"{len(timing)}-question"]
    return {n: (a + min(b, a + 3.0)) / 2 + 0.4 for n, (a, b) in zip(names, timing)}


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
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("--regen-images", action="store_true")
    ap.add_argument("--keyframes")
    a = ap.parse_args()
    post = json.loads(Path(a.post).read_text(encoding="utf-8"))
    try:
        info = render_reel(post, a.out, voice=not a.no_voice, regen_images=a.regen_images, keyframes=a.keyframes)
    except (ReelError, ImageGenError) as e:
        sys.exit(f"ERROR: {e}")
    print(json.dumps(info, ensure_ascii=False, indent=2))
