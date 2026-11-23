# ai-num1-media

Media and automation for the Instagram page [@ai_num1](https://instagram.com/ai_num1): the most important AI news, in Persian.

- `scripts/imagegen.py` — concept background from Cloudflare Workers AI FLUX (`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`)
- `scripts/render.py` — card templates (1080×1350 JPEG, Vazirmatn font): `render_ai` (AI image + Persian headline) and the classic `render` (fallback)
- `scripts/reel.py` — reels: 1080×1920 MP4 (H.264/AAC, 30 fps, 20–30 s) from 4–5 vertical FLUX images with Ken Burns motion, big word-by-word Persian subtitles and background music (no narrator)
- `scripts/carousel.py` — carousels: exactly 6 slides, 1080×1350 (cover with concept image, content slides, closing question)
- `scripts/story.py` — stories, 1080×1920: «پست جدید 👆» intro and the «سؤال روز» card (not used: Make has no story module, stories are off)
- `scripts/stats.py` — weekly stats through the Make webhook's `stats` route (Sunday review)
- `scripts/publish.py` — slot + duplicate check → caption checks (hook, SEO keywords, closing question, ≤5 hashtags) → photo, carousel or reel → push to GitHub Pages → send `{type: "photo"|"carousel"|"reel", …}` to the Make webhook (`MAKE_WEBHOOK_URL`); reel videos are removed from the repo after publishing
- `music/` — background tracks by Kevin MacLeod (incompetech.com, CC BY 4.0), credited in each reel caption
- `queue/` — data for each post (JSON)
- `posts/YYYY/MM/` — published images (and reel videos, only until they are posted), served at `https://phernesarta-droid.github.io/ai-num1-media/posts/...`
- `data/posted.json` — post history (for de-duplication)
- `AUTOMATION.md` — runbook for the scheduled runs (Dubai time: 09:45 news carousel, 13:45 photo, 17:45 edu carousel, 20:45 reel; no stories) and the weekly content mix
- `examples/` — sample queue files for each format
- `fonts/` — Vazirmatn font (SIL OFL license, `fonts/OFL.txt`)

