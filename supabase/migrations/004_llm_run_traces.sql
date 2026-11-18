-- Telemetry: one row per external call made while executing a scheduler run.
--
--   kind = 'llm'   Gemini generateContent (one row per attempt, including failovers)
--   kind = 'tool'  deterministic tool steps: Tavily search, Gnani TTS chunk
--
-- Stores what went in (prompt / tool arguments), what came out (response / result
-- summary), token usage, latency and errors, so every episode can be audited and
-- its LLM cost measured. Large text is truncated by the backend (~20 KB per field).
create table if not exists llm_run_traces (
    id              uuid primary key default gen_random_uuid(),
    run_id          uuid references scheduler_runs(id) on delete cascade,
    user_id         uuid references users(id) on delete cascade,
    stage           text,                 -- run stage, e.g. research / synthesize
    node            text,                 -- agent node, e.g. plan_queries / search / write_script
    kind            text not null check (kind in ('llm', 'tool')),
    name            text not null,        -- gemini.generate_content | tavily.search | gnani.tts
    requested_model text,                 -- first model in the chain
    model           text,                 -- model actually called on this attempt
    attempt         int not null default 1,
    status          text not null check (status in ('ok', 'error')),
    error           text,
    input           jsonb not null default '{}'::jsonb,
    output          jsonb not null default '{}'::jsonb,
    input_tokens    int,
    output_tokens   int,
    thinking_tokens int,
    total_tokens    int,
    input_chars     int,
    latency_ms      int,
    metadata        jsonb not null default '{}'::jsonb,
    started_at      timestamptz not null,
    created_at      timestamptz not null default now()
);
create index if not exists llm_run_traces_run_idx on llm_run_traces (run_id, started_at);
create index if not exists llm_run_traces_user_idx on llm_run_traces (user_id, started_at desc);

alter table llm_run_traces enable row level security;  -- backend-only, like the other tables
