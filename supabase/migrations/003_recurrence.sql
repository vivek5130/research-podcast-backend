-- Scheduling options beyond "daily": one-time, weekly and monthly runs.
--
--   frequency     once | daily | weekly | monthly
--   run_date      once:    the local date to run on (with run_time)
--   days_of_week  weekly:  ISO-style weekdays, 0 = Monday ... 6 = Sunday
--   day_of_month  monthly: 1-31 (shorter months run on their last day)
--
-- A one-time scheduler is deactivated after it fires; next_run_at keeps the time it ran.
alter table schedulers
    add column if not exists frequency    text not null default 'daily',
    add column if not exists run_date     date,
    add column if not exists days_of_week smallint[],
    add column if not exists day_of_month smallint;

alter table schedulers drop constraint if exists schedulers_frequency_check;
alter table schedulers add constraint schedulers_frequency_check check (
    (frequency = 'daily')
    or (frequency = 'once')
    or (frequency = 'weekly'  and cardinality(days_of_week) between 1 and 7
                              and days_of_week <@ array[0,1,2,3,4,5,6]::smallint[])
    or (frequency = 'monthly' and day_of_month between 1 and 31)
);
