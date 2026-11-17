# Runbook: daily posts for @ai_num1

Every scheduled run (10:00 and 20:00 Dubai time) follows these steps. **One post per run.** If no fresh, verified, non-duplicate story is found, post nothing and stop.

## 0. Prepare the repo
```bash
cd ai-num1-media   # or the checkout directory
git fetch origin main && git checkout -B main origin/main
```
GitHub Pages serves the `main` branch, so images must be pushed there (`publish.py` does that itself).
`MAKE_WEBHOOK_URL` must be set in the environment; if it is not, stop and report it.

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
  "summary": "…",
  "sources": [{"name": "TechCrunch", "url": "https://…"}, {"name": "CalMatters", "url": "https://…"}],
  "hashtags": ["هوش_مصنوعی", "AI", "…"]
}
```
- `headline`: fluent Persian, at most ~90 characters, without exaggeration or clickbait.
- `summary`: 2–3 sentences, at most ~420 characters; who, what, when, and why it matters. Neutral tone.
- Keep proper nouns of companies and products in English (OpenAI, Gemini, …).
- `sources`: the original article URLs (not aggregator URLs), at most 3.
- `tag` is optional: e.g. «خبر داغ هوش مصنوعی», «مدل جدید», «قانون‌گذاری», «سرمایه‌گذاری», «پژوهش».
- 4 to 7 hashtags, mixing Persian and English.

## 5. Review and publish
```bash
python3 scripts/publish.py queue/<id>.json --dry-run   # render + caption
```
Open and view the image (`posts/YYYY/MM/<id>.jpg`): text must not overflow, overlap, or show broken characters. If it is too long, shorten the text.
```bash
python3 scripts/publish.py queue/<id>.json
```
The script pushes the image to `main`, waits until it is live on Pages, sends it to the Make webhook, and records it in `data/posted.json`.

## 6. Report
Report in one or two lines: the headline, the sources, the image URL, and the webhook response. On error, write the exact error and do not retry more than once (to avoid a double post).
