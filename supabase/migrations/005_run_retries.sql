-- Retry bookkeeping for scheduler runs.
--   attempts     how many times a stage execution started (automatic queue retries included)
--   retry_count  how many times the user pressed "Retry" on a failed run
alter table scheduler_runs
    add column if not exists attempts    int not null default 0,
    add column if not exists retry_count int not null default 0;
