#!/usr/bin/env python3
"""Carousel slides for @ai_num1: exactly 6 JPEG slides, 1080x1350 (4:5), Vazirmatn.

The Make carousel module maps exactly six items (get(1.image_urls; 1) ... get(1.image_urls; 6)), so every
carousel is cover + 4 content slides + closing slide.

Slide 1   cover: full-bleed FLUX concept image (or the brand gradient) + tag + big title + swipe cue
Slides    content: one idea per slide (kicker, optional big number, title, body, optional prompt box)
Last      closing question + save/follow call to action (+ sources for news)

Queue JSON (see AUTOMATION.md): "type": "carousel", "kind": "news" | "edu", the usual caption fields, and
    "cover_title": "optional, defaults to headline",
    "slides": [ {"kicker": "...", "title": "...", "body": "...", "big": "۴۰٪", "prompt": "..."}, ... ]   # exactly 4
Every slide needs a title; kicker, body, big and prompt are optional.

Usage:
    python3 scripts/carousel.py queue/<id>.json OUT_DIR [background.jpg]
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render import (ACCENT, ACCENT2, BG_TOP, MARGIN, MUTED, WHITE, H, W, background, blurred_bg, cover,  # noqa: E402
                    draw_rtl, emoji, fa, font, jalali_label, shade, text_shadow, tw, wrap)

CONTENT_SLIDES = 4                      # + cover + closing slide = exactly 6 (Make maps 6 items)
RIGHT = W - MARGIN
CONTENT_W = W - 2 * MARGIN
TOP, BOTTOM = 210, H - 170              # content area between header and footer


class CarouselError(Exception):
    pass


def spec(post):
    """Validate the carousel fields; returns the list of content slides."""
    errors = []
    kind = post.get("kind")
    if kind not in ("news", "edu"):
        errors.append('kind must be "news" or "edu"')
    slides = post.get("slides") or []
    if len(slides) != CONTENT_SLIDES:
        errors.append(f"slides: exactly {CONTENT_SLIDES} content slides (6 in total with cover and closing slide), "
                      f"got {len(slides)}")
    for i, s in enumerate(slides, 1):
        if not (s.get("title") or "").strip():
            errors.append(f"slides[{i}]: title is required")
        if len(s.get("title") or "") > 80:
            errors.append(f"slides[{i}].title: at most 80 characters")
        if len(s.get("body") or "") > 300:
            errors.append(f"slides[{i}].body: at most 300 characters")
        if len(s.get("prompt") or "") > 260:
            errors.append(f"slides[{i}].prompt: at most 260 characters")
        if len(s.get("big") or "") > 10:
            errors.append(f"slides[{i}].big: at most 10 characters (a number like ۶۰٪)")
    if len(post.get("cover_title") or post.get("headline") or "") > 90:
        errors.append("cover_title: at most 90 characters")
    if errors:
        raise CarouselError("invalid carousel:\n  - " + "\n  - ".join(errors))
    return slides


# ---------------------------------------------------------------- pieces

def header(img, n, total):
    d = ImageDraw.Draw(img)
    d.text((MARGIN, 78), "@ai_num1", font=font("Black", 40), fill=WHITE)
    d.rectangle((MARGIN, 136, MARGIN + 70, 142), fill=ACCENT)
    if n:
        f = font("Black", 34)
        t = fa(f"{n}/{total}")
        w = d.textlength(t, font=f) + 48
        d.rounded_rectangle((RIGHT - w, 80, RIGHT, 136), radius=28, outline=ACCENT, width=3)
        d.text((RIGHT - w / 2, 108), t, font=f, fill=ACCENT, anchor="mm")


def dots(img, n, total):
    d = ImageDraw.Draw(img)
    gap, r = 30, 7
    x0 = W / 2 - (total - 1) * gap / 2
    y = H - 96
    for i in range(total):
        # same direction as Instagram's own indicator: slide 1 is the leftmost dot
        x = x0 + i * gap
        if i + 1 == n:
            d.rounded_rectangle((x - 18, y - r, x + 18, y + r), radius=r, fill=ACCENT)
        else:
            d.ellipse((x - r, y - r, x + r, y + r), fill=(110, 118, 160))


def arrow_left(d, x, y, size, fill, width=6):
    """A drawn left arrow (Vazirmatn has no arrow glyphs): tip at (x, y)."""
    d.line((x, y, x + size, y), fill=fill, width=width)
    d.line((x, y, x + size * 0.45, y - size * 0.45), fill=fill, width=width)
    d.line((x, y, x + size * 0.45, y + size * 0.45), fill=fill, width=width)


def swipe_cue(img, text="ورق بزن"):
    d = ImageDraw.Draw(img)
    f = font("Bold", 34)
    arrow = 40
    w = tw(d, text, f) + arrow + 18 + 64
    y = H - 126
    d.rounded_rectangle((MARGIN, y, MARGIN + w, y + 64), radius=32, fill=(34, 40, 78), outline=ACCENT, width=2)
    draw_rtl(d, MARGIN + w - 32, y + 8, text, f, WHITE)
    arrow_left(d, MARGIN + 30, y + 32, arrow, ACCENT, width=5)


def with_emoji(img, right_x, y, text, f, fill, ch, size=None):
    """Draw RTL text with a color emoji after it (to its left). Returns the total width."""
    d = ImageDraw.Draw(img)
    w = draw_rtl(d, right_x, y, text, f, fill)
    e = emoji(ch, size or int(f.size * 1.05))
    if e is not None:
        img.paste(e, (int(right_x - w - 14 - e.width), int(y + f.size * 0.18)), e)
        w += 14 + e.width
    return w


class Block:
    def __init__(self, height, draw, gap_after=0):
        self.height, self.draw, self.gap_after = height, draw, gap_after


def text_lines(d, text, weight, size, max_w, fill, lh=1.6, align="right"):
    f = font(weight, size)
    lines = wrap(d, text, f, max_w)
    line_h = int(size * lh)

    def draw(img, y):
        dd = ImageDraw.Draw(img)
        for i, ln in enumerate(lines):
            if align == "center":
                x = W / 2 + tw(dd, ln, f) / 2
            else:
                x = RIGHT
            draw_rtl(dd, x, y + i * line_h, ln, f, fill)
    return lines, Block(line_h * len(lines), draw)


def is_latin(text):
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isascii() for c in letters) / len(letters) > 0.6


def prompt_box(d, text, size):
    f = font("Medium", size)
    latin = is_latin(text)
    inner_w = CONTENT_W - 72
    if latin:
        words, lines, cur = text.split(), [], ""
        for w in words:
            t = f"{cur} {w}".strip()
            if cur and d.textlength(t, font=f) > inner_w:
                lines.append(cur)
                cur = w
            else:
                cur = t
        if cur:
            lines.append(cur)
    else:
        lines = wrap(d, text, f, inner_w)
    line_h = int(size * 1.6)
    label_h = 64
    h = label_h + line_h * len(lines) + 40

    def draw(img, y):
        dd = ImageDraw.Draw(img, "RGBA")
        dd.rounded_rectangle((MARGIN, y, RIGHT, y + h), radius=28, fill=(0, 214, 255, 26), outline=ACCENT, width=3)
        lf = font("Bold", 28)
        draw_rtl(dd, RIGHT - 34, y + 18, "پرامپت (کپی کن):", lf, ACCENT)
        yy = y + label_h
        for ln in lines:
            if latin:
                dd.text((MARGIN + 36, yy), ln, font=f, fill=WHITE)
            else:
                draw_rtl(dd, RIGHT - 36, yy, ln, f, WHITE)
            yy += line_h
    return Block(h, draw)


def accent_bar_block():
    def draw(img, y):
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((RIGHT - 160, y, RIGHT, y + 10), radius=5, fill=ACCENT)
        d.rounded_rectangle((RIGHT - 230, y, RIGHT - 175, y + 10), radius=5, fill=ACCENT2)
    return Block(10, draw)


def layout(img, blocks, top=TOP, bottom=BOTTOM):
    total = sum(b.height + b.gap_after for b in blocks)
    if total > bottom - top:
        raise CarouselError("too much text")
    y = top + (bottom - top - total) // 2
    for b in blocks:
        b.draw(img, y)
        y += b.height + b.gap_after


# ---------------------------------------------------------------- slides

def cover_slide(post, bg_path, total):
    title = (post.get("cover_title") or post["headline"]).strip()
    if bg_path:
        img = cover(Image.open(bg_path).convert("RGB"), W, H)
        img = shade(img, [(0.0, 150), (0.16, 40), (0.34, 0), (0.48, 120), (0.64, 225), (1.0, 245)])
    else:
        img = background()
    d = ImageDraw.Draw(img)
    header(img, 0, total)
    tag = post.get("tag") or ("آموزش هوش مصنوعی" if post.get("kind") == "edu" else "خبر داغ هوش مصنوعی")
    tf = font("Bold", 30)
    t_w = tw(d, tag, tf)
    d.rounded_rectangle((RIGHT - t_w - 52, 76, RIGHT, 136), radius=30, fill=ACCENT)
    draw_rtl(d, RIGHT - 26, 84, tag, tf, BG_TOP)

    for size in (88, 82, 76, 70, 64, 58):
        f = font("Black", size)
        lines = wrap(d, title, f, CONTENT_W)
        if len(lines) <= 4:
            break
    else:
        raise CarouselError("cover_title too long")
    lh = int(size * 1.45)
    bottom = H - 200
    top = bottom - lh * len(lines)
    bar_y = top - 44
    d.rounded_rectangle((RIGHT - 160, bar_y, RIGHT, bar_y + 10), radius=5, fill=ACCENT)
    d.rounded_rectangle((RIGHT - 230, bar_y, RIGHT - 175, bar_y + 10), radius=5, fill=ACCENT2)
    if post.get("kind") == "news" and post.get("date"):
        draw_rtl(d, RIGHT, bar_y - 60, jalali_label(post["date"]), font("Medium", 30), MUTED)

    def headline(draw, fill):
        for i, ln in enumerate(lines):
            draw_rtl(draw, RIGHT, top + i * lh, ln, f, fill or WHITE)
    img = text_shadow(img, headline)
    swipe_cue(img)
    dots(img, 1, total)
    return img


def content_slide(s, n, total, base):
    img = base.copy()
    header(img, n, total)
    d = ImageDraw.Draw(img)
    # shrink everything together until it fits
    for step in range(8):
        k = 1 - 0.06 * step
        blocks = []
        if s.get("kicker"):
            _, b = text_lines(d, s["kicker"], "Bold", int(40 * k), CONTENT_W, ACCENT, lh=1.5)
            b.gap_after = int(18 * k)
            blocks.append(b)
        if s.get("big"):
            bf = font("Black", int(150 * k))
            big = fa(s["big"])

            def draw_big(im, y, big=big, bf=bf):
                draw_rtl(ImageDraw.Draw(im), RIGHT, y - int(bf.size * 0.15), big, bf, ACCENT)
            blocks.append(Block(int(bf.size * 1.2), draw_big, int(10 * k)))
        lines, b = text_lines(d, s["title"], "Black", int(74 * k), CONTENT_W, WHITE, lh=1.5)
        b.gap_after = int(30 * k)
        blocks.append(b)
        bar = accent_bar_block()
        bar.gap_after = int(38 * k)
        blocks.append(bar)
        if s.get("body"):
            _, b = text_lines(d, s["body"], "Medium", int(46 * k), CONTENT_W, (222, 226, 245), lh=1.75)
            b.gap_after = int(34 * k)
            blocks.append(b)
        if s.get("prompt"):
            blocks.append(prompt_box(d, s["prompt"], int(38 * k)))
        try:
            layout(img, blocks)
            break
        except CarouselError:
            img = base.copy()
            header(img, n, total)
            d = ImageDraw.Draw(img)
    else:
        raise CarouselError(f"slide {n}: too much text ({s['title'][:40]}...)")
    swipe_cue(img)
    dots(img, n, total)
    return img


def closing_slide(post, total, base):
    for step in range(5):
        k = 1 - 0.07 * step
        img = base.copy()
        header(img, total, total)
        try:
            layout(img, closing_blocks(post, img, k))
            break
        except CarouselError:
            continue
    else:
        raise CarouselError("closing slide: question too long")
    dots(img, total, total)
    return img


def closing_blocks(post, img, k):
    d = ImageDraw.Draw(img)
    blocks = []
    lf = font("Black", int(40 * k))

    def label(im, y):
        with_emoji(im, RIGHT, y, "سؤال امروز", lf, ACCENT, "💬")
    blocks.append(Block(int(60 * k), label, int(26 * k)))
    for size in (64, 58, 54, 50):
        lines, qb = text_lines(d, post["question"], "Black", int(size * k), CONTENT_W, WHITE, lh=1.5)
        if len(lines) <= 4:
            break
    qb.gap_after = int(30 * k)
    blocks.append(qb)
    bar = accent_bar_block()
    bar.gap_after = int(44 * k)
    blocks.append(bar)
    cf = font("Bold", int(38 * k))
    edu = post.get("kind") == "edu"
    ctas = [("جوابت رو کامنت کن", "👇"),
            ("ذخیره کن که بعداً لازمت می‌شه" if edu else "ذخیره کن و برای دوستت بفرست", "🔖"),
            ("برای ترفندهای بیشتر دنبال کن" if edu else "خبرهای معتبر هر روز: دنبال کن", "➕")]
    for text, ch in ctas:
        def cta(im, y, text=text, ch=ch):
            with_emoji(im, RIGHT, y, text, cf, (230, 234, 250), ch)
        blocks.append(Block(int(cf.size * 1.55), cta, int(10 * k)))
    hf = font("Black", int(54 * k))

    def handle(im, y):
        ImageDraw.Draw(im).text((MARGIN, y + 12), "@ai_num1", font=hf, fill=ACCENT)
    blocks.append(Block(int(80 * k), handle))
    if post.get("kind") == "news" and post.get("sources"):
        names = "  ·  ".join(s["name"] for s in post["sources"])

        def src(im, y):
            dd = ImageDraw.Draw(im)
            lw = draw_rtl(dd, RIGHT, y, "منبع:", font("Bold", 26), ACCENT)
            sf = font("Medium", 26)
            while (dd.textlength(names, font=sf) if names.isascii() else tw(dd, names, sf)) > CONTENT_W - lw - 14 \
                    and sf.size > 18:
                sf = font("Medium", sf.size - 2)
            if names.isascii():
                dd.text((RIGHT - lw - 14 - dd.textlength(names, font=sf), y + 2), names, font=sf, fill=MUTED)
            else:
                draw_rtl(dd, RIGHT - lw - 14, y + 2, names, sf, MUTED)
        blocks[-1].gap_after = int(36 * k)
        blocks.append(Block(40, src))
    return blocks


def render_carousel(post, out_dir, bg_path=None):
    """Render all slides into out_dir as <id>-01.jpg ... ; returns the list of paths."""
    slides = spec(post)
    total = len(slides) + 2
    assert total == 6, total
    base = blurred_bg(bg_path, W, H, darken=0.72) if bg_path else background()
    images = [cover_slide(post, bg_path, total)]
    images += [content_slide(s, i + 2, total, base) for i, s in enumerate(slides)]
    images.append(closing_slide(post, total, base))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"{post['id']}-[0-9][0-9].jpg"):
        old.unlink()
    paths = []
    for i, im in enumerate(images, 1):
        p = out_dir / f"{post['id']}-{i:02d}.jpg"
        im.convert("RGB").save(p, "JPEG", quality=92, optimize=True, progressive=True)
        paths.append(p)
    return paths


def contact_sheet(paths, out):
    w, h, cols = 360, 450, 4
    rows = -(-len(paths) // cols)
    sheet = Image.new("RGB", (cols * (w + 10) + 10, rows * (h + 10) + 10), (20, 20, 30))
    for i, p in enumerate(paths):
        sheet.paste(Image.open(p).resize((w, h)), (10 + (i % cols) * (w + 10), 10 + (i // cols) * (h + 10)))
    sheet.save(out, quality=88)
    return out


if __name__ == "__main__":
    post = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    bg = sys.argv[3] if len(sys.argv) > 3 else None
    try:
        paths = render_carousel(post, sys.argv[2], bg)
    except CarouselError as e:
        sys.exit(f"ERROR: {e}")
    print("\n".join(str(p) for p in paths))
    print("sheet:", contact_sheet(paths, Path(sys.argv[2]) / "sheet.jpg"))
