#!/usr/bin/env python3
"""Concept background images for @ai_num1 via Cloudflare Workers AI (FLUX).

Needs CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in the environment.
Any failure raises ImageGenError; the caller falls back to the classic template.

Usage:
    python3 scripts/imagegen.py "prompt" out.jpg              # square (photo posts)
    python3 scripts/imagegen.py --vertical "prompt" out.jpg   # 9:16 (reels)
"""
import argparse
import base64
import datetime as dt
import io
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from PIL import Image

MODEL = "@cf/black-forest-labs/flux-1-schnell"
STEPS = 8  # schnell maximum
# Vertical (9:16) images for reels: FLUX.2 [klein] accepts width/height; on failure fall back to the
# smaller klein model, then to square flux-1-schnell (the caller crops it to 9:16).
VERTICAL_MODELS = ["@cf/black-forest-labs/flux-2-klein-9b", "@cf/black-forest-labs/flux-2-klein-4b"]
VERTICAL_SIZE = (1088, 1920)

# Appended to every prompt: the image is a mood picture, not a depiction of anyone or any brand.
STYLE = ("Editorial concept art, cinematic lighting, deep navy and violet palette with cyan highlights, "
         "high detail, strong composition with calm dark negative space in the lower half. ")
STYLE_VERTICAL = ("Vertical 9:16 editorial concept art, cinematic lighting, deep navy and violet palette with cyan "
                  "highlights, high detail, one clear subject, uncluttered calm areas around the center. ")
SAFETY = ("No text, no letters, no words, no numbers, no captions, no watermark, no logos, no brand marks, "
          "no trademarks, no flags. No real or recognizable people; if any figure appears it is a faceless "
          "silhouette or seen from behind, with no visible face.")

# Words that must not appear in image_prompt: company/product/person names invite logos and likenesses.
BLOCKLIST = [
    "openai", "chatgpt", "gpt", "sora", "anthropic", "claude", "google", "gemini", "deepmind", "alphabet",
    "microsoft", "copilot", "meta", "facebook", "instagram", "whatsapp", "llama", "apple", "siri", "amazon",
    "aws", "alexa", "nvidia", "tesla", "spacex", "xai", "grok", "twitter", "mistral", "deepseek", "qwen",
    "alibaba", "baidu", "tencent", "huawei", "samsung", "intel", "amd", "tsmc", "ibm", "oracle", "hugging face",
    "huggingface", "perplexity", "midjourney", "stability", "logo", "portrait", "face of", "photo of",
    "musk", "altman", "zuckerberg", "pichai", "nadella", "amodei", "hassabis", "huang", "trump", "biden",
    "bonta", "newsom", "president", "ceo",
]


class ImageGenError(Exception):
    pass


def check_prompt(prompt):
    """Return a list of blocked terms found in the prompt (empty list = OK)."""
    low = prompt.lower()
    return [w for w in BLOCKLIST if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low)]


def generate(prompt, out_path, timeout=120):
    """Generate a 1024x1024 concept image and save it as JPEG (+ .json metadata next to it).

    flux-1-schnell on Workers AI accepts only prompt + steps (no seed); every call gives a new image."""
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not token or not account:
        raise ImageGenError("CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID not set")
    bad = check_prompt(prompt)
    if bad:
        raise ImageGenError(f"image_prompt contains blocked terms: {', '.join(bad)}")
    body = json.dumps({"prompt": f"{prompt.strip()} {STYLE}{SAFETY}", "steps": STEPS}).encode()
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{MODEL}"
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise ImageGenError(f"HTTP {e.code}: {e.read()[:300]!r}") from e
    except Exception as e:  # timeout, DNS, proxy, bad JSON
        raise ImageGenError(f"{type(e).__name__}: {e}") from e

    b64 = (data.get("result") or {}).get("image")
    if not data.get("success", True) or not b64:
        raise ImageGenError(f"no image in response: {str(data.get('errors'))[:300]}")
    try:
        img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    except Exception as e:
        raise ImageGenError(f"undecodable image: {e}") from e
    if min(img.size) < 512:
        raise ImageGenError(f"image too small: {img.size}")

    return _save(img, out_path, MODEL, prompt)


def _save(img, out_path, model, prompt):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=95)
    out_path.with_suffix(".json").write_text(json.dumps(
        {"model": model, "prompt": prompt,
         "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _multipart(fields):
    boundary = "----ainum1" + uuid.uuid4().hex
    body = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
        for k, v in fields.items()) + f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def generate_vertical(prompt, out_path, timeout=150):
    """Generate a 9:16 concept image (1088x1920) for a reel; JPEG + .json metadata.

    Tries the FLUX.2 [klein] models (native vertical size); if both fail, falls back to square
    flux-1-schnell, which the reel renderer center-crops. Raises ImageGenError if everything fails."""
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not token or not account:
        raise ImageGenError("CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID not set")
    bad = check_prompt(prompt)
    if bad:
        raise ImageGenError(f"image prompt contains blocked terms: {', '.join(bad)}")
    full = f"{prompt.strip()} {STYLE_VERTICAL}{SAFETY}"
    errors = []
    for model in VERTICAL_MODELS:
        body, ctype = _multipart({"prompt": full, "width": VERTICAL_SIZE[0], "height": VERTICAL_SIZE[1]})
        url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {token}", "Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read())
            b64 = (data.get("result") or {}).get("image")
            if not b64:
                raise ImageGenError(f"no image: {str(data.get('errors'))[:200]}")
            img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
            if img.height < 1200 or img.width >= img.height:
                raise ImageGenError(f"unexpected size {img.size}")
            return _save(img, out_path, model, prompt)
        except urllib.error.HTTPError as e:
            errors.append(f"{model}: HTTP {e.code} {e.read()[:200]!r}")
        except Exception as e:  # timeout, flagged output, bad image
            errors.append(f"{model}: {type(e).__name__}: {str(e)[:200]}")
    try:
        return generate(prompt, out_path, timeout=timeout)
    except ImageGenError as e:
        errors.append(f"{MODEL}: {e}")
    raise ImageGenError("; ".join(errors))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--vertical", action="store_true")
    ap.add_argument("prompt")
    ap.add_argument("out")
    a = ap.parse_args()
    print((generate_vertical if a.vertical else generate)(a.prompt, a.out))
