# HTML To APK Telegram Bot — Render Web Service

## GitHub
Upload every file in this folder to a GitHub repository.

## Telegram
1. Create the bot with @BotFather and copy TOKEN.
2. Add the bot as an administrator to @SANTO_BIO and @Premium1App.
3. Get your numeric Telegram ID and use it as ADMIN_ID.

## Render
1. Render → New → Web Service.
2. Connect the GitHub repository.
3. Runtime: Docker.
4. Render will use Dockerfile and listen on the PORT supplied by Render.
5. Add Environment Variables:
   TOKEN = your BotFather token
   ADMIN_ID = your numeric Telegram ID
   DB_PATH = /app/work/bot.db
6. Attach a persistent disk at /app/work (5 GB recommended).
7. Deploy.
8. Open https://YOUR-SERVICE.onrender.com/health. It should show {"ok":true}.

## Bot features
/start → channel join verification → four services.
HTML To APK → app name → logo → HTML → debug WebView APK.
APK To HTML → extracts HTML/CSS/JS that are physically packaged inside an APK.
Zip Extract → extracts a ZIP safely and sends its files.
Convert Zip → receive multiple files → Done → ZIP name → ZIP download.
/admin → user count, broadcast, service ON/OFF, block/unblock.

Telegram's Bot API does not provide a custom inline-button color property, so native Telegram inline buttons are used.

APK building is CPU/RAM intensive; use a paid Render instance for reliable builds. APK To HTML cannot reconstruct source code that was never packaged in the APK.
