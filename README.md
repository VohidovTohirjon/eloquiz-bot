# eloquiz-bot

Cloud runner for the **@eloquiz** CS2 reels (Guess the Elo · Clutch or choke · Pure edit).
Runs on GitHub Actions 6×/day (`.github/workflows/slot.yml`): picks a public FACEIT/Allstar highlight
(Claude chooses among candidates and writes the hook), renders a 1080×1920 reel, posts it to Instagram
through the Graph API (the video is served briefly from this repo's GitHub Pages) and reports to Telegram.

Clips: players on FACEIT, captured by Allstar (credited in every reel and caption).
Music: "Happy Beats / Business Moves" by Sascha Ende, ende.app (CC BY 4.0). Font: Archivo Black (SIL OFL 1.1).
Secrets (repo settings → Secrets → Actions): `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TG_CHAT_ID`, `IG_USER_ID`, `IG_ACCESS_TOKEN`.
