#!/usr/bin/env python3
"""Fixed news-card template for @ai_num1 (1080x1350 JPEG, Vazirmatn).

Usage:
    python3 scripts/render.py post.json out.jpg

post.json fields used: headline, summary, sources (list of {name, url}), date (YYYY-MM-DD), tag (optional).
"""
import datetime as dt
import json
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "fonts"

W, H = 1080, 1350
MARGIN = 84
BG_TOP = (11, 16, 38)
BG_BOTTOM = (24, 12, 52)
ACCENT = (0, 214, 255)
ACCENT2 = (168, 85, 247)
WHITE = (245, 247, 255)
MUTED = (170, 178, 210)

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
FA_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
             "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]


def font(weight, size):
    return ImageFont.truetype(str(FONTS / f"Vazirmatn-{weight}.ttf"), size, layout_engine=ImageFont.Layout.RAQM)


def fa(text):
    return str(text).translate(FA_DIGITS)


def gregorian_to_jalali(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = (355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100
            + (gy2 + 399) // 400 + gd + g_d_m[gm - 1])
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def jalali_label(iso_date):
    d = dt.date.fromisoformat(iso_date)
    jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
    return fa(f"{jd} {FA_MONTHS[jm - 1]} {jy}")


def tw(draw, text, f):
    return draw.textlength(text, font=f, direction="rtl", language="fa")


def wrap(draw, text, f, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and tw(draw, trial, f) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def draw_rtl(draw, right_x, y, text, f, fill):
    w = tw(draw, text, f)
    draw.text((right_x - w, y), text, font=f, fill=fill, direction="rtl", language="fa")
    return w


def fit_block(draw, text, weight, sizes, max_w, max_lines):
    for size in sizes:
        f = font(weight, size)
        lines = wrap(draw, text, f, max_w)
        if len(lines) <= max_lines:
            return f, lines, size
    raise ValueError(f"Text too long for template: {text[:60]}...")


def background():
    grad = Image.new("RGB", (1, H))
    for y in range(H):
        t = y / (H - 1)
        grad.putpixel((0, y), tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)))
    img = grad.resize((W, H))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((W - 520, -260, W + 260, 520), fill=(0, 70, 110))
    gd.ellipse((-320, H - 560, 420, H + 180), fill=(70, 20, 110))
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    return ImageChops.screen(img, glow)


def render(post, out_path):
    img = background()
    d = ImageDraw.Draw(img)
    right = W - MARGIN
    content_w = W - 2 * MARGIN

    # --- header: brand (left) + tag pill (right)
    brand = font("Black", 40)
    d.text((MARGIN, 78), "@ai_num1", font=brand, fill=WHITE)
    d.rectangle((MARGIN, 136, MARGIN + 70, 142), fill=ACCENT)

    tag = post.get("tag") or "خبر داغ هوش مصنوعی"
    tf = font("Bold", 30)
    t_w = tw(d, tag, tf)
    pad = 26
    d.rounded_rectangle((right - t_w - 2 * pad, 76, right, 136), radius=30, fill=ACCENT)
    draw_rtl(d, right - pad, 84, tag, tf, BG_TOP)

    # --- date
    df = font("Medium", 30)
    draw_rtl(d, right, 176, jalali_label(post["date"]), df, MUTED)

    # --- headline
    y = 262
    hf, hlines, hsize = fit_block(d, post["headline"], "Black", [72, 66, 60, 56, 52], content_w, 5)
    lh = int(hsize * 1.55)
    for line in hlines:
        draw_rtl(d, right, y, line, hf, WHITE)
        y += lh

    # accent divider
    y += 22
    d.rounded_rectangle((right - 160, y, right, y + 10), radius=5, fill=ACCENT)
    d.rounded_rectangle((right - 230, y, right - 175, y + 10), radius=5, fill=ACCENT2)
    y += 58

    # --- summary
    footer_top = H - 230
    avail_lines_for = lambda s: max(1, (footer_top - 30 - y) // int(s * 1.75))
    for size in [40, 38, 36, 34, 32]:
        sf = font("Regular", size)
        slines = wrap(d, post["summary"], sf, content_w)
        if len(slines) <= avail_lines_for(size):
            break
    else:
        raise ValueError("Summary too long for template")
    slh = int(size * 1.75)
    for line in slines:
        draw_rtl(d, right, y, line, sf, (222, 226, 245))
        y += slh

    # --- footer: source box
    box = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(box).rounded_rectangle((MARGIN, footer_top, right, footer_top + 112), radius=26,
                                          fill=(255, 255, 255, 18), outline=(110, 120, 180, 255), width=2)
    img = Image.alpha_composite(img.convert("RGBA"), box).convert("RGB")
    d = ImageDraw.Draw(img)
    names = "، ".join(s["name"] for s in post["sources"])
    lf = font("Bold", 30)
    nf = font("Medium", 30)
    lw = draw_rtl(d, right - 34, footer_top + 34, "منبع:", lf, ACCENT)
    latin = all(s["name"].isascii() for s in post["sources"])
    if latin:
        names = "  ·  ".join(s["name"] for s in post["sources"])
    names_f = nf
    max_names_w = content_w - 68 - lw - 16

    def names_w(f):
        return d.textlength(names, font=f) if latin else tw(d, names, f)

    while names_w(names_f) > max_names_w and names_f.size > 20:
        names_f = font("Medium", names_f.size - 2)
    ny = footer_top + 34 + (30 - names_f.size) // 2
    nx_right = right - 34 - lw - 16
    if latin:
        d.text((nx_right - names_w(names_f), ny), names, font=names_f, fill=WHITE)
    else:
        draw_rtl(d, nx_right, ny, names, names_f, WHITE)

    # bottom line
    bf = font("Medium", 26)
    draw_rtl(d, right, H - 84, "هر روز، مهم‌ترین خبرهای هوش مصنوعی", bf, MUTED)
    d.text((MARGIN, H - 84), "AI NUM1", font=font("Black", 26), fill=ACCENT)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=92, optimize=True, progressive=True)
    return out_path


if __name__ == "__main__":
    post = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(render(post, sys.argv[2]))
