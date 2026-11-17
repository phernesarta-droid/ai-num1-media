# Runbook: daily posts for @ai_num1

Every scheduled run (10:00 and 20:00 Dubai time) follows these steps. **One post per run.** If no fresh, verified, non-duplicate story is found, post nothing and stop.

## 0. Prepare the repo
```bash
cd ai-num1-media   # or the checkout directory
git fetch origin main && git checkout -B main origin/main
```
GitHub Pages serves the `main` branch, so images must be pushed there (`publish.py` does that itself).
`MAKE_WEBHOOK_URL` must be set in the environment; if it is not, stop and report it.
`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` are used for the concept image; if they are missing, the post still goes out with the classic template (note it in the report).

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

## 5. Review and publish
```bash
python3 scripts/publish.py queue/<id>.json --dry-run   # render + caption
```
Open and view the image (`posts/YYYY/MM/<id>.jpg`): text must not overflow, overlap, or show broken characters. If it is too long, shorten the text.
The output line `mode: ai` or `mode: classic` says which template was used. With `mode: ai`, check the picture itself:
- no faces of real-looking people, no logos or brand marks, no letters or pseudo-text anywhere in the picture (look closely at objects);
- the headline is clearly readable over the image.

If the picture fails, change `image_prompt` (simpler, more abstract) or run again with `--regen-bg` for a new image. After **two** failed regenerations, use `--no-ai` (classic template).
The reviewed background is cached in `cache/bg/<id>.jpg` (git-ignored) and reused by the real publish as long as `image_prompt` is unchanged, so the published image is exactly the one you reviewed.
```bash
python3 scripts/publish.py queue/<id>.json
```
The script pushes the image to `main`, waits until it is live on Pages, sends it to the Make webhook, and records it in `data/posted.json`.

## 6. Report
Report in one or two lines: the headline, the sources, the image URL, the image mode (ai/classic, and why if classic), and the webhook response. On error, write the exact error and do not retry more than once (to avoid a double post).
