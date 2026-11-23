#!/usr/bin/env python3
"""Instagram story images for @ai_num1: 1080x1920 JPEG.

intro     "پست جدید 👆" + the post's concept image as a card + the headline (every slot)
question  "سؤال روز" card: the question, optional 2-4 answer options, "answer under the new post" (17:45 slot)

Text stays inside y 250-1600 (Instagram's story UI covers the top and bottom edges).

Usage:
    python3 scripts/story.py intro    queue/<id>.json out.jpg [card_image.jpg]
    python3 scripts/story.py question queue/<id>.json out.jpg [background.jpg]
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render import ACCENT, ACCENT2, BG_TOP, WHITE, background, blurred_bg, cover, draw_rtl, emoji, font, tw, wrap  # noqa: E402

W, H = 1080, 1920
SIDE = 90
CONTENT_W = W - 2 * SIDE

KIND_LINE = {
    "carousel": "ورق بزن و کامل بخون",
    "reel": "ریلز جدید رو ببین",
    "photo": "خبر کامل توی پست جدید",
}


class StoryError(Exception):
    pass


def pill(img, y, text, ch=None, size=50, fill=ACCENT, color=BG_TOP):
    """Centered rounded label with an optional color emoji on its left."""
    d = ImageDraw.Draw(img)
    f = font("Black", size)
    e = emoji(ch, int(size * 1.1)) if ch else None
    tw_ = tw(d, text, f)
    inner = tw_ + ((e.width + 18) if e else 0)
    w, h = inner + 2 * 44, int(size * 1.9)
    x0 = (W - w) / 2
    d.rounded_rectangle((x0, y, x0 + w, y + h), radius=h // 2, fill=fill)
    right = x0 + w - 44
    draw_rtl(d, right, y + (h - size * 1.45) / 2, text, f, color)
    if e:
        img.paste(e, (int(right - tw_ - 18 - e.width), int(y + (h - e.height) / 2)), e)
    return h


def centered_lines(img, y, text, weight, sizes, max_lines, fill=WHITE, lh=1.45, shadow=True):
    d = ImageDraw.Draw(img)
    for size in sizes:
        f = font(weight, size)
        lines = wrap(d, text, f, CONTENT_W)
        if len(lines) <= max_lines:
            break
    else:
        raise StoryError(f"text too long for the story: {text[:50]}...")
    line_h = int(size * lh)
    if shadow:
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        for i, ln in enumerate(lines):
            draw_rtl(ld, W / 2 + tw(ld, ln, f) / 2, y + i * line_h, ln, f, (0, 0, 0, 220))
        img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(10)))
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        draw_rtl(d, W / 2 + tw(d, ln, f) / 2, y + i * line_h, ln, f, fill)
    return line_h * len(lines)


def rounded(im, radius):
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.width - 1, im.height - 1), radius=radius, fill=255)
    out = im.convert("RGBA")
    out.putalpha(mask)
    return out


def card(img, src, box, radius=44):
    """Paste `src` cover-cropped into box (x0, y0, w, h) with a glow and a thin accent frame."""
    x0, y0, w, h = box
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle((x0 - 6, y0 - 6, x0 + w + 6, y0 + h + 6), radius=radius + 6,
                                           fill=(0, 214, 255, 120))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(28)))
    img.alpha_composite(rounded(cover(src.convert("RGB"), w, h), radius), (x0, y0))
    ImageDraw.Draw(img).rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=radius, outline=(255, 255, 255, 90),
                                          width=3)


def base(bg_path):
    img = blurred_bg(bg_path, W, H, darken=0.6, blur=45) if bg_path else background(W, H)
    return img.convert("RGBA")


def footer(img, y=1660):
    d = ImageDraw.Draw(img)
    f = font("Black", 40)
    t = "@ai_num1"
    d.text(((W - d.textlength(t, font=f)) / 2, y), t, font=f, fill=WHITE)


def render_intro(post, out, card_path=None, kind="photo", show_headline=True):
    """card_path: the post's clean concept image (no text) -> card + headline below;
    or the rendered post itself (it already shows the headline) with show_headline=False."""
    img = base(card_path)
    y = 270
    y += pill(img, y, "پست جدید", "👆") + 60
    if card_path:
        src = Image.open(card_path)
        if show_headline:
            card(img, src, (150, y, 780, 780))
            y += 780 + 56
            y += centered_lines(img, y, post["headline"], "Black", [66, 62, 58, 54, 50], 4) + 26
        else:
            card(img, src, (170, y, 740, 925))
            y += 925 + 44
    else:
        y += 120
        y += centered_lines(img, y, post["headline"], "Black", [84, 78, 72, 66, 60], 6) + 40
    line = KIND_LINE.get(kind, KIND_LINE["photo"])
    d = ImageDraw.Draw(img)
    lf = font("Bold", 40)
    draw_rtl(d, W / 2 + tw(d, line, lf) / 2, min(y, 1560), line, lf, ACCENT)
    if y > 1620:
        raise StoryError("intro story: headline too long")
    footer(img)
    return save(img, out)


def render_question(post, out, bg_path=None):
    """The 'question of the day' story. Uses post["story_question"] = {"question", "options"} if present,
    else the post's closing question."""
    sq = post.get("story_question") or {}
    q = (sq.get("question") or post["question"]).strip()
    options = [o.strip() for o in sq.get("options") or [] if o.strip()]
    if options and not 2 <= len(options) <= 4:
        raise StoryError("story_question.options: 2-4 options")
    img = base(bg_path)
    # violet wash so the question card looks different from the intro stories
    wash = Image.new("RGBA", (W, H), ACCENT2 + (38,))
    img.alpha_composite(wash)
    y = 290
    y += pill(img, y, "سؤال روز", "🤔", size=56) + 90
    y += centered_lines(img, y, q, "Black", [80, 74, 68, 62, 58, 54], 6) + 70
    of = font("Bold", 46)
    for i, opt in enumerate(options):
        h = 112
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))   # translucent pill: draw on a layer, then blend
        ImageDraw.Draw(layer).rounded_rectangle((SIDE, y, W - SIDE, y + h), radius=56, fill=(255, 255, 255, 34),
                                                outline=(255, 255, 255, 130), width=3)
        img.alpha_composite(layer)
        d = ImageDraw.Draw(img)
        d.ellipse((W - SIDE - 96, y + 16, W - SIDE - 16, y + 96), fill=ACCENT)
        d.text((W - SIDE - 56, y + 58), "۱۲۳۴"[i], font=font("Black", 44), fill=BG_TOP, anchor="mm")
        if tw(d, opt, of) > CONTENT_W - 150:
            raise StoryError(f"option too long: {opt}")
        draw_rtl(d, W - SIDE - 124, y + (h - 46 * 1.45) / 2, opt, of, WHITE)
        y += h + 26
    y += 40
    if y > 1500:
        raise StoryError("question story: too much text")
    d = ImageDraw.Draw(img)
    lf = font("Bold", 40)
    t = "جوابت رو زیر پست جدید کامنت کن"
    e = emoji("👇", 46)
    w = tw(d, t, lf) + (e.width + 14 if e else 0)
    right = W / 2 + w / 2
    draw_rtl(d, right, 1530, t, lf, (230, 234, 250))
    if e:
        img.paste(e, (int(right - tw(d, t, lf) - 14 - e.width), 1540), e)
    footer(img)
    return save(img, out)


def save(img, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "JPEG", quality=92, optimize=True, progressive=True)
    return out


if __name__ == "__main__":
    mode, post_path, out = sys.argv[1:4]
    extra = sys.argv[4] if len(sys.argv) > 4 else None
    post = json.loads(Path(post_path).read_text(encoding="utf-8"))
    try:
        if mode == "intro":
            print(render_intro(post, out, extra, kind=post.get("type") or "photo"))
        else:
            print(render_question(post, out, extra))
    except StoryError as e:
        sys.exit(f"ERROR: {e}")
