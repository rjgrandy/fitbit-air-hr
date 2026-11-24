# Running on Tower (Unraid) behind `tailscale serve`

The app listens only on `127.0.0.1:8787` on the host. You reach it only through
`tailscale serve`, which gives it a tailnet-only HTTPS address. Nothing is exposed to the LAN or the internet,
and nothing needs a port forward. Never use `tailscale funnel`.

Keep everything under `/mnt/user/appdata/fitbit-air` (on Unraid, `/root` is wiped on reboot).

## 1. Get the code

```bash
mkdir -p /mnt/user/appdata/fitbit-air && cd /mnt/user/appdata/fitbit-air
BRANCH=main   # or a PR branch you're testing, e.g. milestone-6-live-page
wget -qO- "https://github.com/rjgrandy/fitbit-air-hr/archive/refs/heads/$BRANCH.tar.gz" | tar xz --strip-components=1
```

This leaves `.env` and `data/` alone, so you can re-run it later to update.

The container runs as user 1000, so give it the data folder (needed once, e.g. after running the
milestone 1 probe as root, which left `data/token.json` owned by root):

```bash
mkdir -p data && chown -R 1000:1000 data
```

If `data/token.json` from the milestone 1 probe is there, the app reuses it and you don't need to authorize again.

`.env` (copy `.env.example`, `chmod 600 .env`) holds the Google client ID and secret. It is never committed.

## 2. Build and start the container

With Docker Compose (Unraid: install the **Docker Compose Manager** plugin, or use any `docker compose` you have):

```bash
docker compose up -d --build
```

Without Compose, use the equivalent plain Docker commands:

```bash
docker build -t fitbit-air-hr .
docker rm -f fitbit-air-hr 2>/dev/null
docker run -d --name fitbit-air-hr --restart unless-stopped \
  -p 127.0.0.1:8787:8000 \
  -v /mnt/user/appdata/fitbit-air/data:/data \
  --env-file .env -e TZ=America/Chicago -e DATA_DIR=/data \
  --memory 256m --cpus 0.5 \
  fitbit-air-hr
```

Check: `curl -s http://127.0.0.1:8787/healthz` should print `{"ok":true}`.

## 3. Publish it on the tailnet with `tailscale serve`

One-time, in the Tailscale admin console (<https://login.tailscale.com/admin/dns>): make sure **MagicDNS** is on
and **HTTPS Certificates** is enabled.

On Tower (Tailscale installed on the host: the Unraid 7 built-in Tailscale or the Tailscale plugin):

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:8787
tailscale serve status
```

Port 8443 is used because Unraid's own web UI already answers on 443 at Tower's Tailscale address.
`status` shows the address: `https://tower.<your-tailnet>.ts.net:8443/`. The setting persists across reboots.
Turn it off with `tailscale serve --https=8443 off`.

Make sure Funnel is **not** on: `tailscale funnel status` should list nothing.

## 4. Confirm it's not reachable from the LAN

From another device on your home Wi-Fi with Tailscale **off**:

```bash
curl -m 5 http://<tower-lan-ip>:8787/    # must fail (connection refused / timeout)
```

## 5. Live heart rate

1. On your Android phone, connect Tailscale and open `https://tower.<your-tailnet>.ts.net:8443/` in **Chrome**, then tap **Live**.
2. Start heart-rate sharing on the Fitbit Air (from the Google Health app or the band).
3. Tap **Connect** and pick the band. If it doesn't appear, tap **Show all devices** instead.
4. The big number is your current bpm, colored by zone. Below it are elapsed time, average, max, time in each zone and a rolling chart (last 10 minutes by default; change it with **Show last**). **Reset** starts the numbers over.
5. The screen stays on while connected. Turn the phone sideways for a layout with the number and the chart side by side.
6. If the band drops out, the page says so and retries 5 times. If that fails, tap **Reconnect**.

Live data stays between the phone and the band. The server only serves the page and your zones; nothing from a live session is saved.

## Updating

```bash
cd /mnt/user/appdata/fitbit-air
wget -qO- https://github.com/rjgrandy/fitbit-air-hr/archive/refs/heads/main.tar.gz | tar xz --strip-components=1
docker compose up -d --build
```

Your workouts, settings and Google token live in `data/` and survive updates. `tailscale serve` doesn't need re-running.

## Backing up

Everything is in `/mnt/user/appdata/fitbit-air/data/` (`fitbit-air-hr.sqlite3` plus `token.json`). For a consistent copy while it runs:

```bash
docker exec fitbit-air-hr python -c "import sqlite3;s=sqlite3.connect('/data/fitbit-air-hr.sqlite3');d=sqlite3.connect('/data/backup.sqlite3');s.backup(d)"
```

Or stop the container and copy the folder. The Unraid "Appdata Backup" plugin also covers it.
