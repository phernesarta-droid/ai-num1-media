# ai-num1-media

Media and automation for the Instagram page [@ai_num1](https://instagram.com/ai_num1): the most important AI news, in Persian.

- `scripts/imagegen.py` — concept background from Cloudflare Workers AI FLUX (`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`)
- `scripts/render.py` — card templates (1080×1350 JPEG, Vazirmatn font): `render_ai` (AI image + Persian headline) and the classic `render` (fallback)
- `scripts/publish.py` — duplicate check → caption checks (hook, closing question, ≤5 hashtags) → image → push to GitHub Pages → send `{image_url, caption}` to the Make webhook (`MAKE_WEBHOOK_URL`)
- `queue/` — data for each post (JSON)
- `posts/YYYY/MM/` — published images, served at `https://phernesarta-droid.github.io/ai-num1-media/posts/...`
- `data/posted.json` — post history (for de-duplication)
- `AUTOMATION.md` — runbook for the scheduled runs (10:00 and 20:00 Dubai time)
- `fonts/` — Vazirmatn font (SIL OFL license, `fonts/OFL.txt`)

Planned: carousel route.
