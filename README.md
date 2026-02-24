# momoney Mini App

## Production Deploy

1. Backend (Render)
- Create a new Render Web Service from this repo.
- Use `render.yaml` (Blueprint) or manual values:
  - Build: `pip install -r requirements.txt`
  - Start: `uvicorn server:app --host 0.0.0.0 --port $PORT`
- Set env vars in Render:
  - `MONETAG_SDK_SRC=https://libtl.com/sdk.js`
  - `MONETAG_ZONE_ID=10648187`
  - `MONETAG_SHOW_FN=show_10648187`
  - `MONETAG_VIDEO_SHOW_FN=show_10648187`
  - `MONETAG_POSTBACK_TOKEN=<strong-secret>`
  - `ADS_ALLOW_SIMULATE=false`
  - `DB_PATH=/var/data/mapp.db`
- Your API URL will be like: `https://momoney-api.onrender.com`

2. Frontend (Vercel)
- Import this same repo into Vercel.
- Framework preset: `Other`.
- Deploy root static files (`index.html`, `app.js`, `styles.css`).
- Your frontend URL will be like: `https://momoney.vercel.app`

3. Bot env (`.env` in your bot server)
- `MINIAPP_URL=https://momoney.vercel.app?v=<new-version>`
- `MINIAPP_API_BASE_URL=https://momoney-api.onrender.com`

4. Monetag postback
- Set callback URL in Monetag:
  - `https://momoney-api.onrender.com/api/monetag/postback?token=<same-token>`

## Local Run

```powershell
py -3.11 -m uvicorn server:app --host 0.0.0.0 --port 8090
```
