# Runbook: daily posts for @ai_num1

Two scheduled runs a day (Dubai time), **one post per run**:

| Slot | Format | Queue `type` | Make route |
|------|--------|--------------|------------|
| 09:45 | photo post (1080×1350 news card) | `"photo"` (default) | Create a Photo Post (`image_url`, `caption`) |
| 19:45 | reel (1080×1920 video, 20–30 s) | `"reel"` | Create a Reel Post (`video_url`, `caption`) |

Decide the format from the slot: a run that starts before 15:00 Dubai time is the photo slot, later is the reel slot. If no fresh, verified, non-duplicate story is found, post nothing and stop.

## 0. Prepare the repo
```bash
cd ai-num1-media   # or the checkout directory
git fetch origin main && git checkout -B main origin/main
```
GitHub Pages serves the `main` branch, so images must be pushed there (`publish.py` does that itself).
`MAKE_WEBHOOK_URL` must be set in the environment; if it is not, stop and report it.
`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` are used for the concept image; if they are missing, the post still goes out with the classic template (note it in the report). A reel cannot be made without them: in the reel slot, post the story as a photo instead and say so in the report.

Then remove reel videos left on Pages by earlier runs (no-op when there are none):
```bash
python3 scripts/publish.py --cleanup
```

## 1. Find the news
- With WebSearch, look for the most important AI news **from the last 24 hours** (since the previous post).
- Priority: major model/product launches from leading companies, important AI laws and regulation, major deals and investments, significant research, and security/safety incidents.
- Skip: rumors, unsourced leaks, opinion pieces, ads, minor stories.

## 2. Verify (required)
A story counts as confirmed only if **at least one** of these holds:
- An official primary source (company blog/newsroom, government website, court ruling, paper on arXiv/a journal) **plus** one reputable news outlet; or
- At least **two independent** reputable news outlets report the same facts.

Reputable outlets: Reuters, AP, Bloomberg, Financial Times, WSJ, NYT, The Verge, TechCrunch, Wired, Ars Technica, CNBC, BBC, The Information, MIT Technology Review, CalMatters, Axios, and the like.
Aggregator sites, newsletters, Substacks and AI-generated sites only count as leads, never as sources.
Only write details that every source agrees on. If a number or name is inconsistent between sources, leave it out.
The session's network does not allow fetching most news sites directly; verify with WebSearch, using `allowed_domains` to read what specific outlets report (e.g. `["techcrunch.com", "bloomberg.com", "cnbc.com", "axios.com"]`; reuters.com, apnews.com, nytimes.com, wsj.com, ft.com and theverge.com are rejected by the search tool).

## 3. Duplicate check
- Read `data/posted.json`; a story that has already been posted (even from another source or with a new follow-up) is a duplicate, unless there is a substantive new development (e.g. a court ruling following an earlier lawsuit).
- Choose a stable `story_key` for the story (e.g. `california-v-openai-lawsuit`); if the next development is a follow-up, use a new key (e.g. `california-v-openai-ruling`).
- `python3 scripts/publish.py --check queue/<id>.json` must report `OK`. Exit code 3 = duplicate → pick another story.

## 4. Write the post (Persian)
Create the file `queue/<YYYY-MM-DD>-<slug>.json` (the Dubai date):
```json
{
  "id": "2026-11-17-california-sues-openai",
  "story_key": "california-v-openai-lawsuit",
  "date": "2026-11-17",
  "tag": "خبر داغ هوش مصنوعی",
  "headline": "…",
  "hook": "…",
  "summary": "…",
  "question": "…؟",
  "image_prompt": "English description of a concept image …",
  "sources": [{"name": "TechCrunch", "url": "https://…"}, {"name": "CalMatters", "url": "https://…"}],
  "hashtags": ["…", "…", "…"]
}
```
- `headline`: fluent Persian, at most ~90 characters, without exaggeration or clickbait.
- `summary`: 2–3 sentences, at most ~420 characters; who, what, when, and why it matters. Neutral tone.
- Keep proper nouns of companies and products in English (OpenAI, Gemini, …).
- `sources`: the original article URLs (not aggregator URLs), at most 3.
- `tag` is optional: e.g. «خبر داغ هوش مصنوعی», «مدل جدید», «قانون‌گذاری», «سرمایه‌گذاری», «پژوهش».

### Caption rules (enforced by `publish.py`)
The caption is built as: **hook → summary → sources → follow line → question → hashtags**. The headline is on the image, not repeated in the caption.
- `hook`: **one** Persian sentence (≤150 characters) that opens the caption and makes people want to read on: the surprising consequence, the stake, or the "why you should care". Factual, not clickbait; no claim the sources don't support; not a copy of the headline.
- `question`: one Persian question (≤150 characters, ends in «؟») that closes the caption and invites comments. Ask for an opinion or experience tied to this story (e.g. «به نظر شما …؟», «شما از … استفاده می‌کنید؟»); not yes/no-only and not generic ("نظرتان چیست؟").
- `hashtags`: **at most 5**, precise and specific to this story: the company/product/topic involved (e.g. `OpenAI`, `کنترل_صادرات`, `تراشه_هوش_مصنوعی`). At most one generic tag (`هوش_مصنوعی`). No filler tags (`اخبار`, `تکنولوژی`, `ترند`, …), no duplicates. Write them without `#`.

### Image rules
Every post gets a concept image from Cloudflare Workers AI (FLUX, `@cf/black-forest-labs/flux-1-schnell`) with the Persian headline overlaid (`render_ai` in `scripts/render.py`).
- `image_prompt` (English) describes a **conceptual metaphor** for the story: objects, light, landscapes, abstract shapes (e.g. "a translucent dome of cyan light guarding a glowing network").
- **Never** depict real people or their faces, and never company logos or brand marks. Do not put any company, product, or person's name in the prompt; `publish.py` rejects prompts with such names. People, if any, only as faceless silhouettes.
- Avoid subjects FLUX tends to cover in fake lettering: server racks, screens, signs, documents, chips with markings, storefronts. Prefer open, minimal scenes.
- `scripts/imagegen.py` adds the style and the no-text / no-logo / no-faces instructions itself; don't repeat them.
- If image generation fails (missing keys, HTTP error, timeout, bad image), `publish.py` automatically falls back to the classic template and prints `WARNING: AI image failed …`. That is fine: publish with the classic card and mention it in the report.

## 4b. Reel slot (19:45): extra fields
Same JSON as above plus `"type": "reel"` and a `reel` object. `headline`, `hook`, `summary`, `question`, `sources` and `hashtags` work exactly as for photos (the caption is built the same way, plus a music credit line). `image_prompt` is not needed.
```json
  "type": "reel",
  "reel": {
    "hook": "رقیب تازهٔ OpenAI و Anthropic؟",
    "points": ["…", "…", "…"],
    "image_prompts": ["English scene 1", "English scene 2", "English scene 3", "English scene 4", "English scene 5"],
    "say": {
      "hook": "رقیب تازه برای اوپن ای آی؟",
      "headline": "…", "points": ["…", "…", "…"], "question": "…؟"
    }
  }
```
The video shows, in order: `reel.hook` (0–2 s, large) → `headline` → each of `reel.points` → `question` + @ai_num1.
- `reel.hook`: on-screen opener, **≤45 characters**, the most striking angle in a few words (factual, may be a question). It differs from the caption `hook`.
- `reel.points`: **2–3** key facts, each **≤90 characters**, one fact per point, only what the sources agree on. Avoid "today/امروز" unless it really happened on the post date.
- `reel.image_prompts`: **4–5** different concept scenes made for this story (one per beat of the video), same rules as `image_prompt` above: metaphors, no names of companies/products/people, no logos, no faces, no screens/signs/documents. Images are generated vertically (9:16) with FLUX.2 [klein] on Workers AI.
- `reel.say` (optional but recommended): what the Persian narrator (Piper, voice `fa_IR-ganji-medium`) says for each beat. Piper reads English words and decimals badly, so write names in Persian script (OpenAI → «اوپن ای آی», Microsoft → «مایکروسافت», GitHub Copilot → «گیت‌هاب کوپایلت») and numbers as simple words or round figures. Keep it short: about **40–55 words in total**, the hook ≤5 words (it should take ~2 s). If a `say` entry is missing, the on-screen text is read instead.
- The script sizes each beat to its narration and keeps the whole reel at 20–30 s. If the narration does not fit even when read faster, the reel is rendered **without** a narrator and the dry run prints `WARNING: narration skipped (…)`: shorten `reel.say` and run the dry run again (`--rerender`). Piper missing or a broken voice file also falls back to music only; that is acceptable, mention it in the report. `--no-voice` forces music only.
- Music: one of the Kevin MacLeod tracks in `music/` (CC BY 4.0), used in turn; the caption credit is added automatically. Do not use other music.

## 5. Review and publish
```bash
python3 scripts/publish.py queue/<id>.json --dry-run   # render + caption
```
Open and view the image (`posts/YYYY/MM/<id>.jpg`): text must not overflow, overlap, or show broken characters. If it is too long, shorten the text.
The output line `mode: ai` or `mode: classic` says which template was used. With `mode: ai`, check the picture itself:
- no faces of real-looking people, no logos or brand marks, no letters or pseudo-text anywhere in the picture (look closely at objects);
- the headline is clearly readable over the image.

If the picture fails, change `image_prompt` (simpler, more abstract) or run again with `--regen-bg` for a new image. After **two** failed regenerations, use `--no-ai` (classic template).

**Reels:** the dry run renders the MP4 (about 2 minutes) and prints `frames:` with a contact sheet of one frame per beat (`cache/reel/<id>/keyframes/sheet.jpg`; single frames are next to it). Open the sheet and check every frame:
- text is fully visible, not cut off, no broken or reversed Persian letters, readable over the picture;
- the pictures: no faces of real-looking people, no logos or brand marks, no letters or pseudo-text (faceless silhouettes are fine);
- the `reel:` line shows a duration of 20–30 s, the size (must be under 50 MB) and whether `voice` is set.
To replace pictures, change the bad `reel.image_prompts` entries (only those are regenerated) or use `--regen-images` for all; after two failed attempts post the story as a photo instead. The reviewed render is cached and published as is; change the queue JSON or pass `--rerender` to render again.
The reviewed background is cached in `cache/bg/<id>.jpg` (git-ignored) and reused by the real publish as long as `image_prompt` is unchanged, so the published image is exactly the one you reviewed.
```bash
python3 scripts/publish.py queue/<id>.json
```
The script pushes the image (or video) to `main`, waits until it is live on Pages, sends it to the Make webhook, and records it in `data/posted.json`.
Webhook payload: `{"type": "photo", "image_url", "caption"}` or `{"type": "reel", "video_url", "caption"}`; the Make Router sends `type = reel` to *Create a Reel Post*, everything else to *Create a Photo Post*.

**Pages space:** reel videos are removed from the repo again after publishing. If Make's webhook response contains the Instagram media `id` (a *Webhook Response* module after *Create a Reel Post*), the video is removed right away; if Make only answers `Accepted`, the script waits 15 minutes (Instagram fetches the file in that time) and then removes it, so a reel publish takes up to ~20 minutes: let it finish. A video left behind by an interrupted run is removed by `publish.py --cleanup` at the start of the next run.

## 6. Report
Report in one or two lines: the headline, the sources, the image URL, the image mode (ai/classic, and why if classic), and the webhook response. For a reel: the video URL, duration, narration on/off (and why if off), the music track, and whether the video was removed from the repo. On error, write the exact error and do not retry more than once (to avoid a double post).
