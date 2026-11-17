#!/usr/bin/env python3
"""Concept background images for @ai_num1 via Cloudflare Workers AI (FLUX).

Needs CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in the environment.
Any failure raises ImageGenError; the caller falls back to the classic template.

Usage:
    python3 scripts/imagegen.py "prompt" out.jpg
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
from pathlib import Path

from PIL import Image

MODEL = "@cf/black-forest-labs/flux-1-schnell"
STEPS = 8  # schnell maximum

# Appended to every prompt: the image is a mood picture, not a depiction of anyone or any brand.
STYLE = ("Editorial concept art, cinematic lighting, deep navy and violet palette with cyan highlights, "
         "high detail, strong composition with calm dark negative space in the lower half. ")
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

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=95)
    out_path.with_suffix(".json").write_text(json.dumps(
        {"model": MODEL, "prompt": prompt,
         "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("out")
    a = ap.parse_args()
    print(generate(a.prompt, a.out))
