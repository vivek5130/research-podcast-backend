# Testing the backend with curl

Work through this top to bottom. Each section checks one layer, so if something breaks you know which layer is at fault.

> **Shell:** these commands are for **Git Bash** (or macOS/Linux). In PowerShell, `curl` is an alias for something else and the JSON quoting breaks.
> Sessions are **cookies**, so curl keeps them in a cookie jar file (`-c` saves, `-b` sends).
> JSON values are pulled out with a tiny `python -c` helper, so you don't need `jq`.

---

## 0. Setup

```bash
cd backend
source .venv/Scripts/activate        # macOS/Linux: source .venv/bin/activate
```

Minimum `.env` for local testing (see `.env.example` for all keys):

```env
DATABASE_URL=postgresql://postgres.xxxx:PASSWORD_URL_ENCODED@aws-0-ap-south-1.pooler.supabase.com:6543/postgres
GOOGLE_CLIENT_ID=...
JWT_SECRET=some-long-random-string
CRON_SECRET=local-cron-secret
QUEUE_MODE=local
ALLOW_DEV_LOGIN=true
SESSION_COOKIE_SECURE=false          # localhost is http
QSTASH_CURRENT_SIGNING_KEY=sig_local # any string, for section 7
TAVILY_API_KEY=...  GEMINI_API_KEY=...  GNANI_API_KEY=...
SUPABASE_URL=...    SUPABASE_SERVICE_ROLE_KEY=...
RESEND_API_KEY=...
```

> If your DB password contains `@ : / # ?`, URL-encode it (`@` → `%40`).

Run `supabase/migrations/001_init.sql` once in the Supabase SQL editor. Then start the API:

```bash
uvicorn app.main:app --port 3001       # avoid --reload while testing runs: a reload kills in-process jobs
```

In a **second** Git Bash window (with the venv activated), set up helpers:

```bash
API=http://localhost:3001
JAR=/tmp/dailycast_cookies.txt
json() { python -c "import sys,json; d=json.load(sys.stdin); print(d$1)"; }   # usage: ... | json "['id']"
code() { curl -s -o /dev/null -w '%{http_code}\n' "$@"; }                    # print only the status code
set -a; source .env; set +a      # loads your keys into this shell (for the direct-service checks)
```

---

## 1. Health

```bash
curl -s $API/health          # {"ok":true,"queue_mode":"local"}
curl -s $API/health/db       # {"ok":true,"db_time":"..."}   <- fix DATABASE_URL first if this fails
```
Interactive API docs are at http://localhost:3001/docs.

---

## 2. Check each external service on its own

These calls go **straight to the provider**, without our backend. If one fails here, the problem is the key or account.

### Gnani TTS (writes `gnani_test.mp3`; play it)
```bash
curl -s -X POST https://api.vachana.ai/api/v1/tts/inference \
  -H "Content-Type: application/json" -H "X-API-Key-ID: $GNANI_API_KEY" \
  -d '{"text":"Good morning! This is your daily podcast test.","model":"timbre-v3.0","voice":"Shivani","language":"en-IN","audio_config":{"sample_rate":44100,"num_channels":1,"sample_width":2,"container":"mp3","bitrate":"128k"}}' \
  -o gnani_test.mp3 -w "HTTP %{http_code}, %{size_download} bytes\n"
```
`HTTP 200` with a few KB means it works. For 401/402/400, open the file to read the error JSON.

### Tavily search
```bash
curl -s -X POST https://api.tavily.com/search \
  -H "Authorization: Bearer $TAVILY_API_KEY" -H "Content-Type: application/json" \
  -d '{"query":"startup funding","topic":"news","days":1,"max_results":3}' \
  | python -c "import sys,json; [print('-', r['title'], '|', r['url']) for r in json.load(sys.stdin)['results']]"
```

### Gemini
```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent" \
  -H "x-goog-api-key: $GEMINI_API_KEY" -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"Say hello in five words."}]}]}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['candidates'][0]['content']['parts'][0]['text'])"
```

### Supabase Storage (the `artifacts` bucket should be listed, `public: false`)
```bash
curl -s "$SUPABASE_URL/storage/v1/bucket" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" -H "apikey: $SUPABASE_SERVICE_ROLE_KEY"
```

### Email through the app's own code (Gmail SMTP: sends a real email)
```bash
python -c "import asyncio; from app.services import email; asyncio.run(email.send_email('YOUR_EMAIL', 'DailyCast test', '<p>It works</p>', 'It works'))"
```
No output means it was sent. An `SMTP send failed: (535 ...)` error means `SMTP_USERNAME` isn't the Gmail account that created the App Password.

### QStash token (prints your signing keys; copy them into `.env`)
```bash
curl -s https://qstash.upstash.io/v2/keys -H "Authorization: Bearer $QSTASH_TOKEN"
```

### Podcast agent only, no DB (prints each stage)
```bash
python scripts/run_agent.py "startup funding announced in the last 24 hours"
python scripts/run_agent.py "AI news today" --tts agent_test.mp3      # also runs Gnani TTS
```

---

## 3. Sign in / session / sign out

### Dev sign-in (local only, needs `ALLOW_DEV_LOGIN=true`)
```bash
curl -s -c $JAR -X POST $API/api/auth/signin/dev -H "Content-Type: application/json" \
  -d '{"email":"YOUR_EMAIL","name":"Your Name"}'
# {"user":{...},"expires_at":...,"access_token":"...","token_type":"bearer"}   + Set-Cookie: dailycast_session (HttpOnly)

grep dailycast_session $JAR                  # the cookie line starts with #HttpOnly_
curl -s -b $JAR $API/api/auth/session        # {"id":"...","email":"YOUR_EMAIL",...}
```
Use the email where you want to receive the "podcast ready" emails.

### Real Google sign-in (what the frontend does)
```bash
curl -s -c $JAR -X POST $API/api/auth/signin/google -H "Content-Type: application/json" \
  -d '{"credential":"<GOOGLE_ID_TOKEN>"}'
# {"user":{...},"expires_at":...}   (no token in the body: it is only in the httpOnly cookie)
```
To get an ID token without the frontend, use the [OAuth Playground](https://developers.google.com/oauthplayground):
1. Click the gear icon, choose "Use your own OAuth credentials", and enter your client ID and secret. Add the playground URL as an authorized redirect URI first.
2. Use the `openid email profile` scopes and exchange the code. Copy `id_token`.

### Bearer also works (handy for scripts)
```bash
TOKEN=$(curl -s -X POST $API/api/auth/signin/dev -H "Content-Type: application/json" -d '{"email":"YOUR_EMAIL"}' | json "['access_token']")
curl -s $API/api/auth/session -H "Authorization: Bearer $TOKEN"
```

### Expected failures
```bash
code $API/api/auth/session                                     # 401 no cookie
code $API/api/auth/session -H "Authorization: Bearer junk"     # 401 bad token
```

---

## 4. Schedulers (CRUD)

```bash
# What types exist, their stages, and the config JSON schema
curl -s -b $JAR $API/api/schedulers/types

# Create a daily podcast at 08:00 IST
SCHED=$(curl -s -b $JAR -X POST $API/api/schedulers -H "Content-Type: application/json" \
  -d '{"type":"podcast","run_time":"08:00","timezone":"Asia/Kolkata","config":{"topic":"Find all startup funding announced in the last 24 hours"}}' \
  | json "['id']")
echo $SCHED

# List / filter / get
curl -s -b $JAR $API/api/schedulers
curl -s -b $JAR "$API/api/schedulers?type=podcast"
curl -s -b $JAR $API/api/schedulers/$SCHED
# next_run_at is UTC: 08:00 IST = 02:30Z. "name" defaults to the topic.

# Update: rename, change time (next_run_at is recalculated), change topic
curl -s -b $JAR -X PATCH $API/api/schedulers/$SCHED -H "Content-Type: application/json" \
  -d '{"name":"Morning funding","run_time":"09:30","config":{"topic":"Startup funding rounds today"}}'

# Pause / resume
curl -s -b $JAR -X PATCH $API/api/schedulers/$SCHED -H "Content-Type: application/json" -d '{"is_active":false}'
curl -s -b $JAR -X PATCH $API/api/schedulers/$SCHED -H "Content-Type: application/json" -d '{"is_active":true}'

# Validation errors -> 422
curl -s -b $JAR -X POST $API/api/schedulers -H "Content-Type: application/json" -d '{"type":"podcast","run_time":"08:00","config":{"topic":"x"}}'
# {"detail":"config.topic: String should have at least 3 characters"}
code -b $JAR -X POST $API/api/schedulers -H "Content-Type: application/json" -d '{"type":"newsletter","run_time":"08:00","config":{}}'
code -b $JAR -X POST $API/api/schedulers -H "Content-Type: application/json" -d '{"type":"podcast","run_time":"25:00","timezone":"Mars/Base","config":{"topic":"AI news"}}'
```

---

### Once / weekly / monthly

```bash
# Preview: next run times for any schedule, without saving (what the form's live preview uses)
curl -s -b $JAR -X POST $API/api/schedulers/preview -H "Content-Type: application/json"   -d '{"frequency":"weekly","days_of_week":[0,2,4],"run_time":"07:30","timezone":"Asia/Kolkata"}'
# {"description":"Weekly on Mon, Wed, Fri at 07:30","next_runs":[...3 UTC instants...]}

curl -s -b $JAR -X POST $API/api/schedulers/preview -H "Content-Type: application/json"   -d '{"frequency":"monthly","day_of_month":31,"run_time":"09:00","timezone":"Asia/Kolkata","count":4}'
# short months clamp to their last day: 30 Nov, 31 Dec, 31 Jan, 28 Feb

# Weekly schedule (0 = Mon ... 6 = Sun)
curl -s -b $JAR -X POST $API/api/schedulers -H "Content-Type: application/json"   -d '{"type":"podcast","frequency":"weekly","days_of_week":[0,4],"run_time":"08:00","timezone":"Asia/Kolkata","config":{"topic":"Weekly AI roundup"}}'

# Monthly on the 15th
curl -s -b $JAR -X POST $API/api/schedulers -H "Content-Type: application/json"   -d '{"type":"podcast","frequency":"monthly","day_of_month":15,"run_time":"09:00","timezone":"Asia/Kolkata","config":{"topic":"Monthly markets recap"}}'

# Once at a specific date and time (deactivates after it runs)
curl -s -b $JAR -X POST $API/api/schedulers -H "Content-Type: application/json"   -d '{"type":"podcast","frequency":"once","run_date":"2026-12-01","run_time":"10:00","timezone":"Asia/Kolkata","config":{"topic":"Budget day highlights"}}'

# Once, right now: creates it and starts a run immediately (response has triggered_run_id)
curl -s -b $JAR -X POST $API/api/schedulers -H "Content-Type: application/json"   -d '{"type":"podcast","frequency":"once","run_now":true,"timezone":"Asia/Kolkata","config":{"topic":"AI news right now"}}'

# Change an existing schedule's frequency
curl -s -b $JAR -X PATCH $API/api/schedulers/$SCHED -H "Content-Type: application/json" -d '{"frequency":"weekly","days_of_week":[5,6]}'

# Errors -> 422: weekly without days, one-time in the past
curl -s -b $JAR -X POST $API/api/schedulers -H "Content-Type: application/json" -d '{"type":"podcast","frequency":"weekly","run_time":"08:00","config":{"topic":"x y z"}}'
curl -s -b $JAR -X POST $API/api/schedulers -H "Content-Type: application/json" -d '{"type":"podcast","frequency":"once","run_date":"2026-01-01","run_time":"08:00","config":{"topic":"x y z"}}'
```

---

## 5. Run now (the full pipeline)

```bash
RUN=$(curl -s -b $JAR -X POST $API/api/schedulers/$SCHED/trigger | json "['id']")
echo $RUN

# Watch it: status pending -> running (stage research -> synthesize) -> completed   (about 1-2 min)
while true; do
  L=$(curl -s -b $JAR $API/api/runs/$RUN | json "['status'], d['stage']"); echo "$(date +%T) $L"
  case "$L" in completed*|failed*) break;; esac; sleep 5
done
```
Watch the uvicorn window too: every stage and error is logged.

```bash
# Full run: output has title, script, sources, duration_sec and a signed audio_url
curl -s -b $JAR $API/api/runs/$RUN | python -m json.tool

# Download the audio
curl -s -o podcast.mp3 "$(curl -s -b $JAR $API/api/runs/$RUN | json "['output']['audio_url']")"

# Lists
curl -s -b $JAR $API/api/runs                          # all my runs
curl -s -b $JAR "$API/api/runs?type=podcast"
curl -s -b $JAR $API/api/schedulers/$SCHED/runs        # runs of one scheduler
```
Check your inbox for the "Your podcast" email with the link. If the run failed, `error` says why.

Triggering again while a run is still pending or running returns **409**.

---

## 6. The scheduler tick (what pg_cron does every minute)

```bash
code -X POST $API/internal/scheduler/tick -H "X-Cron-Secret: wrong"        # 401

# Make a scheduler due in 1 minute (Git Bash's `date` ignores TZ=Asia/Kolkata, so use Python)
DUE=$(python -c "from datetime import datetime,timedelta;from zoneinfo import ZoneInfo;print((datetime.now(ZoneInfo('Asia/Kolkata'))+timedelta(minutes=1)).strftime('%H:%M'))")
echo "due at $DUE IST"
S2=$(curl -s -b $JAR -X POST $API/api/schedulers -H "Content-Type: application/json" \
  -d "{\"type\":\"podcast\",\"run_time\":\"$DUE\",\"timezone\":\"Asia/Kolkata\",\"config\":{\"topic\":\"Latest AI model releases\"}}" | json "['id']")

sleep 70
curl -s -X POST $API/internal/scheduler/tick -H "X-Cron-Secret: $CRON_SECRET"   # {"claimed":1,"enqueued":1,"failed":0}
curl -s -X POST $API/internal/scheduler/tick -H "X-Cron-Secret: $CRON_SECRET"   # {"claimed":0,...}  never twice

curl -s -b $JAR $API/api/schedulers/$S2 | json "['next_run_at']"               # moved to tomorrow
curl -s -b $JAR $API/api/schedulers/$S2/runs                                   # trigger: "scheduled"
```

---

## 7. Stage endpoint (what QStash calls)

```bash
code -X POST $API/internal/runs/$RUN/stages/research                           # 401 unsigned

# Sign a request the way QStash does (uses QSTASH_CURRENT_SIGNING_KEY + BACKEND_URL from .env)
eval "$(python scripts/sign_qstash.py $RUN research)"
curl -s -X POST $API/internal/runs/$RUN/stages/research \
  -H "Upstash-Signature: $SIG" -H "Content-Type: application/json" -d "$BODY"
# {"status":"skipped"}  because the stage already ran: duplicate deliveries are no-ops

code -X POST $API/internal/runs/$RUN/stages/research -H "Upstash-Signature: $SIG" -d '{}'   # 401 body tampered
```

---

## 8. Isolation: users only see their own data

```bash
JAR2=/tmp/dailycast_cookies_b.txt
curl -s -c $JAR2 -X POST $API/api/auth/signin/dev -H "Content-Type: application/json" -d '{"email":"someone-else@example.com"}' > /dev/null

curl -s -b $JAR2 $API/api/schedulers                        # []
curl -s -b $JAR2 $API/api/runs                              # []
code -b $JAR2 $API/api/schedulers/$SCHED                    # 404
code -b $JAR2 $API/api/runs/$RUN                            # 404
code -b $JAR2 -X POST $API/api/schedulers/$SCHED/trigger    # 404
code -b $JAR2 -X DELETE $API/api/schedulers/$SCHED          # 404
```

---

## 9. Sign out and cleanup

```bash
code -b $JAR -c $JAR -X POST $API/api/auth/signout     # 204, cookie cleared
code -b $JAR $API/api/auth/session                     # 401

# (sign in again first) delete schedulers; their runs are deleted too
code -b $JAR -X DELETE $API/api/schedulers/$S2         # 204
```

---

## 10. Production (Vercel + QStash + pg_cron)

```bash
API=https://YOUR-BACKEND.vercel.app
curl -s $API/health      # {"ok":true,"queue_mode":"qstash"}
curl -s $API/health/db
```
- `ALLOW_DEV_LOGIN` must be unset or `false`, so `/api/auth/signin/dev` returns 404. Keep `SESSION_COOKIE_SECURE=true`.
- Triggers in production publish to QStash, which calls `/internal/runs/{id}/stages/{stage}`. Each delivery and retry appears in the Upstash console under **Logs**.
- To check that the cron is firing, run in the Supabase SQL editor:
  ```sql
  select status, return_message, start_time from cron.job_run_details order by start_time desc limit 5;
  select status_code, content, created from net._http_response order by created desc limit 5;
  ```

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `/health/db` gives `getaddrinfo failed` | Unencoded `@` in the DB password |
| Sign-in works but `/api/auth/session` returns 401 in curl | Forgot `-b $JAR`, or `SESSION_COOKIE_SECURE=true` on http://localhost (curl won't send Secure cookies over http) |
| 401 in the browser on a deployed frontend | The frontend must proxy `/api` to the backend (same site), or set `SESSION_COOKIE_SAMESITE=none` with Secure |
| Run `failed`, `No recent sources found` | Tavily returned nothing for the last 3 days. Use a broader topic. |
| Run `failed`, `Gnani TTS 401/402` | Bad key, or out of credits |
| Run `failed`, `Gnani TTS 400` | The voice doesn't belong to `GNANI_MODEL` (names are case-sensitive) |
| Run `failed`, `Gnani TTS 500` | The chunk is too long. Lower `GNANI_CHUNK_CHARS`. |
| Run `completed` but no email | Check the server log. SMTP 535 means SMTP_USERNAME isn't the Gmail that created the App Password. With Resend, there's no verified domain. |
| Stuck in `pending` locally | Check the uvicorn log. `--reload` restarts kill in-process jobs. |
| Stuck in `pending` in production | QStash couldn't reach `BACKEND_URL`, or got a 401 because the signing keys are wrong. Check Upstash Logs. |
