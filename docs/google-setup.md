# Milestone 1: Google Health API access check

This script checks that we can read your data before the real app gets built. It only reads. It never writes anything to Google Health.

## What I confirmed in the docs (2026-11-23)

Sources: the v4 discovery document (`health.googleapis.com/$discovery/rest?version=v4`, revision 20261119) and developers.google.com/health (setup, scopes, data-types, filters, rate-limits, workouts pages).

| Need | Data type / endpoint | Read-only scope |
|---|---|---|
| Workouts | `GET /v4/users/me/dataTypes/exercise/dataPoints` (session type, newest first, max page size 25) | `googlehealth.activity_and_fitness.readonly` |
| HR samples | `GET /v4/users/me/dataTypes/heart-rate/dataPoints?filter=heart_rate.sample_time.physical_time >= "…" AND heart_rate.sample_time.physical_time < "…"` (1 s storage resolution, page size up to 10,000) | `googlehealth.health_metrics_and_measurements.readonly` |
| HR zones | `GET /v4/users/me/dataTypes/daily-heart-rate-zones/dataPoints?filter=daily_heart_rate_zones.date >= "YYYY-MM-DD" AND …` (LIGHT / MODERATE / VIGOROUS / PEAK, min and max bpm per day, Karvonen-based) | `googlehealth.health_metrics_and_measurements.readonly` |
| Linked-profile check | `GET /v4/users/me/identity` (gives `400 ACCOUNT_NOT_LINKED` if the profile isn't linked) | any of the above |

We need just those **two scopes**. Each workout also comes with `metricsSummary` (average HR, calories, Google's own time-in-zone totals). It has no max HR field, so the app will work out max HR from the samples.

Rate limits are 300 requests a minute per user. The script backs off exponentially, with jitter, on 429, 503 and 504.

## 1. Google Cloud project and OAuth client

1. Go to <https://console.cloud.google.com/>, open the project picker, choose **New project**, name it something like `fitbit-air-hr`, and create it. Make sure it is selected.
2. Enable the API: open <https://console.developers.google.com/apis/library/health.googleapis.com> and click **Enable** on **Google Health API**.
3. Set up the consent screen: go to **Google Auth Platform** (APIs & Services → OAuth consent screen). Click **Get started** if asked.
   - App name: anything, e.g. `Fitbit Air HR`. User support email: yours.
   - Audience / User type: **External**.
   - Contact email: yours. Accept the policy and click **Create**.
   - Then open **Branding**. Google won't publish an External app until it has an app name, support email, **home page URL** and **privacy policy URL**. Host the two pages in `docs/github-pages/` at a public `rjgrandy.github.io` repo. Set Home page to `https://rjgrandy.github.io/`, and set Privacy policy and Terms to `https://rjgrandy.github.io/privacy.html`. Add `rjgrandy.github.io` under Authorized domains, then click **Save**. Leave the logo empty, because a logo triggers verification.
4. Add the scopes under **Data Access** → **Add or remove scopes**. Search for "Google Health API" and tick only these two:
   - `.../auth/googlehealth.activity_and_fitness.readonly`
   - `.../auth/googlehealth.health_metrics_and_measurements.readonly`

   Click **Update**, then **Save**.
5. Publish the app under **Audience**: click **Publish app** to change the publishing status from *Testing* to **In production**, and confirm. Don't submit it for verification. According to the setup page, an unverified client is capped at 100 users and works fine in production. Refresh tokens from a *Testing* app expire after 7 days, but production tokens only expire if they're revoked or go unused for about 6 months.
6. Create the client under **Clients** → **Create client**:
   - Application type: **Web application** (the docs' "Web Server").
   - Authorized redirect URIs: add exactly `https://www.google.com`
   - Click **Create**, then download the JSON or copy the Client ID and Client secret.

**Prerequisite:** your Google account must already be linked in the Google Health mobile app. Yours is, because the band syncs.

## 2. Run the script

You need Python 3.9 or newer and nothing else. It works on your home server or any computer.

```bash
cd tools
cat > .env <<'EOF'
GOOGLE_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxx
EOF
chmod 600 .env

python3 gh_probe.py auth   # once
python3 gh_probe.py run    # any time after
```

If you'd rather not install Python on the server: `docker run --rm -it -v "$PWD":/w -w /w python:3.12-slim python gh_probe.py auth` (and then the same with `run`).

**auth** prints a Google sign-in URL. Open it, click **Advanced → Go to … (unsafe)** on the unverified-app warning, tick both permissions, and continue. You'll end up on google.com with `?code=…` in the address bar. Paste that whole URL back into the terminal. The refresh token is saved to `data/token.json` with permissions set to 600. If Google says the token expires in a few days, the app is still in Testing (go back to step 1.5).

**run** prints your last 10 workouts and writes these files to `data/`:
- `workouts_last10.json`: the raw exercise sessions
- `workout_hr_samples.json`: every HR sample for the newest workout, plus 3 minutes after it ends (the extra 3 minutes are for recovery HR later). Use `--pick N` to choose a different workout.
- `hr_zones.json`: your zones for that workout's day, and the latest zones

## 3. What to send back

Paste the terminal output from `run`. The things I need to know:
- Do the workouts and activity types match what the Google Health app shows?
- What is the sample count and spacing for a workout? The docs give 1 s storage resolution, but the band may record less often.
- Did the zones come back? I couldn't find any endpoint for *custom* zones you set yourself. If yours are custom and `hr_zones.json` doesn't match them, tell me.
- Do the `sources` show duplicate HR streams? If they do, I'll filter to `google-wearables`.

Please don't send `token.json` or `.env`.
