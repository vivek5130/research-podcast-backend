# DailyCast Backend (FastAPI)

A generic daily scheduler API (`schedulers` → `scheduler_runs`) with a **podcast** scheduler type: it researches a topic every day and delivers an audio briefing made with Gnani.ai TTS.

- How it works: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md)
- curl walkthrough: [docs/TESTING.md](docs/TESTING.md)
- Guardrails (LangChain `PIIMiddleware` + a blocked-terms middleware) and per-episode evaluation metrics, dashboard insights and the offline eval scorecard (`python scripts/eval_topics.py`): [How it works §7d](docs/HOW_IT_WORKS.md#7d-guardrails-and-evaluation)

## Project structure

```
app/
  routes/        HTTP handlers: parse input, auth, cookies, call a controller
  controllers/   generic business logic (schedulers, runs, tick, stage retry policy)
  handlers/      scheduler types behind one interface (base.py, registry.py)
    podcast/     config, handler (research → synthesize → email), LangGraph agent
  workers/       generic, idempotent run executor (one queued job per stage)
  repositories/  SQL, one module per table
  models/        User, Scheduler, SchedulerType, SchedulerRun, RunStatus
  schemas/       API request/response bodies + validation
  services/      external APIs: Tavily, Gemini, Gnani TTS, Supabase Storage, email (Gmail SMTP/Resend), QStash
  core/          settings, DB pool, security, errors, dependencies
  utils/         pure helpers (timezone scheduling)
```
Each layer only calls the layers below it. The file-by-file map is in [HOW_IT_WORKS.md §9](docs/HOW_IT_WORKS.md#9-code-map-and-layers).

## Run locally

Requires **Python 3.12+**. All commands run from the `backend/` folder.

### 1. First-time setup (once)

**Windows (PowerShell)**
```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements-dev.txt
copy .env.example .env              # then fill in your keys
```

**Windows (Git Bash) / macOS / Linux**
```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate       # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                # then fill in your keys
```

For local development, set these in `.env` (never in production):
```env
QUEUE_MODE=local               # run jobs in-process (QStash can't reach localhost)
ALLOW_DEV_LOGIN=true           # email-only sign-in for testing
SESSION_COOKIE_SECURE=false    # localhost is http
CRON_SECRET=local-cron-secret
```

Then apply the database migrations once, in the Supabase SQL editor: [`001_init.sql`](supabase/migrations/001_init.sql), [`003_recurrence.sql`](supabase/migrations/003_recurrence.sql), [`004_llm_run_traces.sql`](supabase/migrations/004_llm_run_traces.sql), then [`005_run_retries.sql`](supabase/migrations/005_run_retries.sql). They create the tables, the scheduling columns and the private `artifacts` bucket.

### 2. Start the server (every time)

**Windows (PowerShell)**
```powershell
cd backend
.venv\Scripts\Activate.ps1
uvicorn app.main:app --port 3001 --reload
```

**Git Bash / macOS / Linux**
```bash
cd backend
source .venv/Scripts/activate       # macOS/Linux: source .venv/bin/activate
uvicorn app.main:app --port 3001 --reload
```

You can also skip activation: `.venv\Scripts\python -m uvicorn app.main:app --port 3001 --reload` (PowerShell), or `.venv/bin/python -m uvicorn ...` (macOS/Linux).

| Check | URL / command |
|---|---|
| Server is up | http://localhost:3001/health → `{"ok":true,"queue_mode":"local"}` |
| Database connected | http://localhost:3001/health/db |
| Interactive API docs | http://localhost:3001/docs |
| Unit tests (no network) | `pytest -q` |

`--reload` restarts the server when you edit code, which also kills any episode being generated in local mode. Leave it off while testing a full run.

### 3. Trigger the scheduler by hand (optional)

In production, pg_cron calls the tick every minute. Locally, call it yourself:
```bash
curl -X POST http://localhost:3001/internal/scheduler/tick -H "X-Cron-Secret: local-cron-secret"
```

### Troubleshooting

| Problem | Fix |
|---|---|
| `uvicorn: command not found` / `not recognized` | Install the **dev** requirements: `pip install -r requirements-dev.txt` (`requirements.txt` is the production set Vercel uses and has no uvicorn). If it's installed, the venv isn't active: activate it, or use `.venv\Scripts\python -m uvicorn ...` |
| `Address already in use` / port 3001 busy | Another server is running. Stop it, or use `--port 3002` (and change `BACKEND_URL` in `frontend/.env.local`) |
| `/health/db` fails with `getaddrinfo failed` | URL-encode special characters in the DB password (`@` → `%40`) |
| PowerShell blocks `Activate.ps1` | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once |

Try the podcast agent on its own, without the DB or queue:
```bash
python scripts/run_agent.py "startup funding announced in the last 24 hours" --tts test.mp3
```

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/auth/signin/google` | none | Google ID token → session cookie |
| POST | `/api/auth/signup` | none | Create an email + password account → session cookie (409 if taken) |
| POST | `/api/auth/signin/password` | none | Email + password → session cookie (401 if wrong) |
| POST | `/api/auth/signin/dev` | none | **Local only** (`ALLOW_DEV_LOGIN=true`): sign in by email (also returns a Bearer token) |
| POST | `/api/auth/signout` | — | Clear the session cookie |
| GET | `/api/auth/session` | session | Current user, or 401 |
| GET | `/api/schedulers/types` | session | Supported types, stages, config JSON schema |
| GET, POST | `/api/schedulers` | session | List (`?type=podcast`) or create schedulers (once / daily / weekly / monthly) |
| POST | `/api/schedulers/preview` | session | Next run times for a schedule, without saving it |
| GET, PATCH, DELETE | `/api/schedulers/{id}` | session (owner) | Read, update (including pause/resume) or delete |
| POST | `/api/schedulers/{id}/trigger` | session (owner) | Start a run now |
| GET | `/api/schedulers/{id}/runs` | session (owner) | Runs of one scheduler |
| GET | `/api/podcast/voices` | session | Gnani voice catalog (gender, persona, use case) + languages |
| GET | `/api/podcast/voices/{voice}/preview?language=` | session | Short cached voice sample (signed URL) |
| GET | `/api/runs` | session | My runs (`?scheduler_id=&type=`) |
| GET | `/api/runs/{id}` | session (owner) | Run status and output (podcast: title, script, sources, signed `audio_url`) |
| POST | `/api/runs/{id}/retry` | session (owner) | Retry a failed run from the stage that failed (earlier output is reused) |
| GET | `/api/runs/{id}/traces` | session (owner) | Telemetry: every Gemini attempt (prompt, response, tokens, latency) and tool call, with totals |
| POST | `/internal/scheduler/tick` | `X-Cron-Secret` | Claim due schedulers, create runs, enqueue first stage |
| POST | `/internal/runs/{id}/stages/{stage}` | QStash signature | Execute one stage |

"session" means the httpOnly cookie, or `Authorization: Bearer <token>` for scripts.

## Adding a scheduler type

1. Add a value to `SchedulerType` (`app/models/scheduler.py`) and to the `type` check in the migration.
2. Create `app/handlers/<type>/handler.py` subclassing `SchedulerHandler`. Set `config_model` and `stages`, implement `run_stage`, and optionally `present_output` and `on_completed`.
3. Register it in `app/handlers/registry.py`.

## Deploy to Vercel (free)

1. Push the repo to GitHub. In Vercel, choose **New Project**, pick the repo, and set **Root Directory** to `backend`. [`vercel.json`](vercel.json) sends every route to `api/index.py` with `maxDuration: 300`.
2. Add the environment variables from `.env.example`, with these differences:
   - `QUEUE_MODE=qstash`
   - `ALLOW_DEV_LOGIN=false`
   - `SESSION_COOKIE_SECURE=true`
   - `BACKEND_URL=https://<this-project>.vercel.app`
   - `FRONTEND_URL=https://<frontend>.vercel.app`
   - `QSTASH_TOKEN`, `QSTASH_CURRENT_SIGNING_KEY` and `QSTASH_NEXT_SIGNING_KEY`, all from the Upstash console
3. Deploy, then check `https://<backend>.vercel.app/health`.
4. In [`supabase/migrations/002_cron.sql`](supabase/migrations/002_cron.sql), replace the URL and secret placeholders and run the file in the Supabase SQL editor.
5. Turn off **Vercel Deployment Protection** for production on this project (or add a bypass). Otherwise pg_cron and QStash get a 401 from Vercel before the request reaches FastAPI.
6. In the frontend, proxy `/api/:path*` to the backend (Next.js `rewrites`), so the session cookie is first-party.
