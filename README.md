# Fitbit Air HR

A self-hosted, single-user web app for the heart rate from your Google Fitbit Air workouts.

- **History.** Every workout, newest first, each with a heart-rate chart over faint zone bands and a time-in-zone bar. Tap one for a zoomable chart, the zone breakdown, recovery heart rate, overlays of past workouts, and CSV/JSON/PNG export. Pin workouts and tag them.
- **Live.** A phone page that reads the band directly over Web Bluetooth and shows a huge zone-colored bpm, a rolling chart and live time in zones.

Data comes from the **Google Health API v4**, read-only. The app never writes, edits or deletes anything in Google Health.

## How it's kept private

| | |
|---|---|
| Google access | Two read-only scopes: `googlehealth.activity_and_fitness.readonly` and `googlehealth.health_metrics_and_measurements.readonly`. |
| Network | The container port is bound to `127.0.0.1` only. You reach it through `tailscale serve`, which is visible to your tailnet only. No `tailscale funnel`, port forwards, webhooks, public callbacks or tunnels. The app makes outbound calls to Google only. |
| Login | None. Anyone on your tailnet who can reach the address can use it, so keep the tailnet to your own devices. |
| Secrets | The OAuth client ID and secret live in `.env`, and the refresh token in `data/token.json` (mode 600). Neither is in the repo. Don't share them. |
| Your data | Workouts, samples, pins, tags and settings stay in a local SQLite file in `data/`. Pins and tags are never sent to Google. |
| Live page | Readings go from the band to your phone's browser and nowhere else. Nothing from a live session is saved. |
| Sync | Sync runs only while the app is open (on load and when you tap **Sync now**). There is no background job. |

## Before you start

- **Your Google account must already be linked to your profile in the Google Health app.** If it isn't, the API answers `ACCOUNT_NOT_LINKED` and the app shows that error. If your band already syncs to the Google Health app, you're set.
- You need a machine with Docker that runs Tailscale (these docs use an Unraid server called Tower).
- The Live page needs Chrome on Android and the band's heart-rate sharing turned on.

## 1. Google OAuth setup (once)

Follow [docs/google-setup.md](docs/google-setup.md), section 1. In short:

1. Create a Google Cloud project and enable the **Google Health API**.
2. On the consent screen, choose **External**, fill in Branding (home page and privacy URLs are required to publish), and add only the two read-only scopes above.
3. Under **Audience**, click **Publish app** so it's **In production**. Leave it unverified. In Testing mode, refresh tokens expire after 7 days.
4. Create a **Web application** OAuth client with the redirect URI `https://www.google.com` exactly.
5. Put the client ID and secret in `.env` (copy `.env.example`, then `chmod 600 .env`).

The redirect goes to google.com on purpose: you copy the code from the address bar and paste it into the app, so the app never needs a public callback URL.

## 2. Run it

Full steps for Unraid are in [docs/tower-setup.md](docs/tower-setup.md). The short version:

```bash
mkdir -p /mnt/user/appdata/fitbit-air && cd /mnt/user/appdata/fitbit-air
wget -qO- https://github.com/rjgrandy/fitbit-air-hr/archive/refs/heads/main.tar.gz | tar xz --strip-components=1
cp .env.example .env && chmod 600 .env    # then fill in the client ID and secret
mkdir -p data && chown -R 1000:1000 data  # the container runs as uid 1000
docker compose up -d --build
curl -s http://127.0.0.1:8787/healthz     # {"ok":true}
```

`docker-compose.yml` publishes `127.0.0.1:8787` only. Don't change it to `0.0.0.0`.

## 3. Put it on your tailnet with `tailscale serve`

With MagicDNS and HTTPS certificates turned on in the Tailscale admin console:

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:8787
tailscale serve status        # shows https://<host>.<tailnet>.ts.net:8443/
tailscale funnel status       # must list nothing
```

This uses port 8443 because Unraid's web UI already owns 443 on the Tailscale address. On another host you can use plain `--https=443`. HTTPS matters: browsers only allow Web Bluetooth on secure pages.

## 4. First run

1. Open the tailnet address. The app shows **Connect Google Health**.
2. Tap **Open Google sign-in**. Google warns that the app isn't verified; click **Advanced → Go to … (unsafe)**. It's your own app. Tick **both** permissions.
3. Google sends you to google.com with `?code=…` in the address bar. Copy that whole URL, paste it into the app and tap **Connect**.
4. The first sync loads your whole history, newest first, then the heart-rate samples, with a progress bar. If it gets interrupted (closed tab, rate limit, restart), it picks up where it left off next time you open the app.
5. Open **Settings** to change the chart's bpm range (default 50–200) and the max heart rate used for zones when Google hasn't provided any.

After that, every time you open the app it fetches new workouts, and the header shows when it last synced. **Sync now** runs it on demand.

## Live heart rate

1. On your Android phone, open the tailnet address in **Chrome** and tap **Live**.
2. Start heart-rate sharing on your Fitbit Air (from the Google Health app or the band).
3. Tap **Connect** and choose the band. If it isn't listed, use **Show all devices**.

The screen stays on while you're connected. If the band drops out, the page retries 5 times and then shows **Reconnect**. Turn the phone sideways for a side-by-side layout.

## Backing up

Everything lives in `data/`: `fitbit-air-hr.sqlite3` (workouts, samples, pins, tags, settings) and `token.json` (your Google refresh token). To take a consistent copy while the app is running:

```bash
docker exec fitbit-air-hr python -c "import sqlite3;s=sqlite3.connect('/data/fitbit-air-hr.sqlite3');d=sqlite3.connect('/data/backup.sqlite3');s.backup(d)"
```

That writes `data/backup.sqlite3`; copy it somewhere safe. Alternatively, stop the container and copy the whole `data/` folder. On Unraid the Appdata Backup plugin covers it.

To restore, stop the container, put the backup in place as `data/fitbit-air-hr.sqlite3`, delete any `-wal`/`-shm` files next to it, and start the container again. Anything Google still has will re-sync, and pins and tags come back from the backup.

## Updating

```bash
cd /mnt/user/appdata/fitbit-air
wget -qO- https://github.com/rjgrandy/fitbit-air-hr/archive/refs/heads/main.tar.gz | tar xz --strip-components=1
docker compose up -d --build
```

This leaves `.env` and `data/` alone, and `tailscale serve` keeps working without changes. Any new database tables are added automatically when it starts.

## When something goes wrong

| What you see | What it means / what to do |
|---|---|
| **Re-authorize Google Health** | The refresh token was revoked, expired (Testing mode does that after 7 days), or Google rejected it. Your saved workouts stay visible. Follow the banner's steps to paste a new code. |
| **Google is rate-limiting requests** | The app already backs off and retries. It stops after repeated 429s and shows what's synced. Tap **Sync now** later; it resumes where it stopped. |
| **Sync failed: … HTTP 403 … isn't enabled** | Enable the Google Health API in your Cloud project. |
| **Your Google account isn't linked in the Google Health app** | Link it in the Google Health mobile app, then tap **Sync now**. |
| **Couldn't reach Google** | The server has no internet access, or DNS is failing. |
| **No workouts yet** | Nothing has synced. Record a workout with the band, let the Google Health app sync it, then tap **Sync now**. |
| **No HR data** on a workout | Google has no heart-rate samples for that time. |
| **Live: Web Bluetooth isn't available** | Use Chrome on Android. iPhone browsers and Firefox don't support Web Bluetooth. |
| **Live: page isn't on HTTPS** | Open the `https://…ts.net` address, not the plain IP. |
| **Live: band isn't in the picker** | Turn on heart-rate sharing first, and make sure no other app is connected to it. Try **Show all devices**. |
| Container won't start, `Permission denied` on `/data` | Run `chown -R 1000:1000 data`. |

`docker logs fitbit-air-hr` shows server errors. Tokens are never written to the log.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/py        # sync, backfill/resume, zones, time in zone, recovery HR, auth, errors, API
npm test                         # BLE heart-rate parser and live stats (Node 18+, no installs)
python tests/py/dev_server.py 8799   # UI against a fake Google API with 300 workouts
```

`tools/gh_probe.py` is a standalone script that checks API access from a terminal. It isn't needed to run the app.

Stack: FastAPI and SQLite on the server, plain ES modules with [uPlot](https://github.com/leeoniya/uPlot) (vendored, MIT) in the browser. No build step and no external CDN.
