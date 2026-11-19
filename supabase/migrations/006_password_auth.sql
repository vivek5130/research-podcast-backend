-- Email + password sign-in alongside Google.
-- * google_sub becomes optional (password-only accounts have none)
-- * password_hash: scrypt hash (see app/core/security.py), NULL for Google-only accounts
-- * one account per email address (case-insensitive), so a person who signs up with a
--   password and later uses Google with the same address lands in the same account

alter table users alter column google_sub drop not null;
alter table users add column if not exists password_hash text;
create unique index if not exists users_email_lower_key on users (lower(email));
