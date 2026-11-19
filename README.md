# ai-num1-media

Media and automation for the Instagram page [@ai_num1](https://instagram.com/ai_num1): the most important AI news, in Persian.

- `scripts/imagegen.py` — concept background from Cloudflare Workers AI FLUX (`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`)
- `scripts/render.py` — card templates (1080×1350 JPEG, Vazirmatn font): `render_ai` (AI image + Persian headline) and the classic `render` (fallback)
- `scripts/reel.py` — reels: 1080×1920 MP4 (H.264/AAC, 30 fps, 20–30 s) from 4–5 vertical FLUX images with Ken Burns motion, animated Persian text, Piper narration (`fa_IR-ganji-medium`) and background music
- `scripts/publish.py` — duplicate check → caption checks (hook, closing question, ≤5 hashtags) → image or reel → push to GitHub Pages → send `{type: "photo", image_url, caption}` or `{type: "reel", video_url, caption}` to the Make webhook (`MAKE_WEBHOOK_URL`); reel videos are removed from the repo after publishing
- `music/` — background tracks by Kevin MacLeod (incompetech.com, CC BY 4.0), credited in each reel caption
- `queue/` — data for each post (JSON)
- `posts/YYYY/MM/` — published images (and reel videos, only until they are posted), served at `https://phernesarta-droid.github.io/ai-num1-media/posts/...`
- `data/posted.json` — post history (for de-duplication)
- `AUTOMATION.md` — runbook for the scheduled runs (09:45 photo, 19:45 reel, Dubai time)
- `fonts/` — Vazirmatn font (SIL OFL license, `fonts/OFL.txt`)

Planned: carousel route.
