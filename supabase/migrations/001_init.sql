-- DailyCast schema. Run in the Supabase SQL editor (or `supabase db push`).
--
--   users           people who signed in with Google
--   schedulers      a user's recurring task: run <type> with <config> daily at <run_time> in <timezone>
--   scheduler_runs  one execution of a scheduler, moving through the type's stages
--
-- The tables are generic. Type-specific data lives in jsonb:
--   schedulers.config      e.g. podcast: {"topic": "..."}
--   scheduler_runs.output  e.g. podcast: {"title", "script", "sources", "audio_path", "duration_sec", ...}
create extension if not exists pgcrypto;

create table if not exists users (
    id            uuid primary key default gen_random_uuid(),
    google_sub    text not null unique,               -- Google's stable account id
    email         text not null,
    name          text,
    picture       text,
    created_at    timestamptz not null default now(),
    last_login_at timestamptz not null default now()
);

create table if not exists schedulers (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references users(id) on delete cascade,
    type        text not null check (type in ('podcast')),   -- extend when a new handler is added
    name        text not null check (char_length(name) between 1 and 200),
    config      jsonb not null default '{}'::jsonb,           -- validated per type by the backend
    run_time    time not null,                                -- local wall-clock time, e.g. 08:00
    timezone    text not null default 'Asia/Kolkata',         -- IANA name
    next_run_at timestamptz not null,                         -- UTC instant of the next run
    is_active   boolean not null default true,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create index if not exists schedulers_user_idx on schedulers (user_id, created_at desc);
-- The tick's hot query: WHERE is_active AND next_run_at <= now()
create index if not exists schedulers_due_idx on schedulers (next_run_at) where is_active;

create table if not exists scheduler_runs (
    id            uuid primary key default gen_random_uuid(),
    scheduler_id  uuid not null references schedulers(id) on delete cascade,
    user_id       uuid not null references users(id) on delete cascade,
    trigger       text not null default 'scheduled' check (trigger in ('scheduled', 'manual')),
    scheduled_for timestamptz not null,                       -- the slot this run belongs to
    status        text not null default 'pending'
                  check (status in ('pending', 'running', 'completed', 'failed')),
    stage         text,                                       -- current stage (null = not started)
    output        jsonb not null default '{}'::jsonb,         -- accumulated stage outputs
    error         text,
    started_at    timestamptz,
    completed_at  timestamptz,
    notified_at   timestamptz,                                -- when the user was notified
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    unique (scheduler_id, scheduled_for)                      -- one run per slot (idempotent ticks)
);
create index if not exists scheduler_runs_user_idx on scheduler_runs (user_id, scheduled_for desc);
create index if not exists scheduler_runs_scheduler_idx on scheduler_runs (scheduler_id, scheduled_for desc);

-- The backend connects as the database owner and enforces ownership in every query.
-- RLS with no policies blocks the public anon/authenticated roles from reading these
-- tables through Supabase's auto-generated REST API (defence in depth).
alter table users          enable row level security;
alter table schedulers     enable row level security;
alter table scheduler_runs enable row level security;

-- Private bucket for generated files (e.g. podcast audio); served via short-lived signed URLs.
insert into storage.buckets (id, name, public)
values ('artifacts', 'artifacts', false)
on conflict (id) do nothing;
