-- Every-minute scheduler: pg_cron fires an HTTP POST (pg_net) to the backend's /internal/scheduler/tick.
-- Run AFTER the backend is deployed. Replace the two placeholders first.
create extension if not exists pg_cron;
create extension if not exists pg_net;

select cron.unschedule('scheduler-tick') where exists (select 1 from cron.job where jobname = 'scheduler-tick');

select cron.schedule(
    'scheduler-tick',
    '* * * * *',
    $$
    select net.http_post(
        url     := 'https://YOUR-BACKEND.vercel.app/internal/scheduler/tick',
        headers := jsonb_build_object('Content-Type', 'application/json',
                                      'X-Cron-Secret', 'YOUR_CRON_SECRET'),
        body    := '{}'::jsonb,
        timeout_milliseconds := 30000
    );
    $$
);

-- Inspect:  select * from cron.job_run_details order by start_time desc limit 10;
--           select * from net._http_response order by created desc limit 10;
