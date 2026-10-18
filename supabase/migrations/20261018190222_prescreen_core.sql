-- UK Company Pre-Screen: core tables.
-- Project: uk-company-prescreen (eu-west-2, London).
--
-- The app connects server-side with the database connection string (SUPABASE_DB_URL).
-- Row level security is ON with no policies, so the public (anon) API key can read or
-- write nothing. Reports hold directors' names and birth month/year: keep them server-side.

create table if not exists public.reports (
  id              text primary key,                -- 16 hex chars, from the app
  created_at      timestamptz not null default now(),
  company_number  text not null,
  company_name    text not null,
  route           text not null check (route in ('note', 'refer', 'outside_appetite', 'compliance_hold')),
  rules_version   text not null,
  data_mode       text not null default 'live' check (data_mode in ('live', 'demo')),
  payload         jsonb not null,                  -- the full report, as returned by the API
  expires_at      timestamptz not null default (now() + interval '90 days')
);
create index if not exists reports_company_created_idx on public.reports (company_number, created_at desc);
create index if not exists reports_created_idx on public.reports (created_at desc);

-- Append-only: a later decision is a new row, never an update.
create table if not exists public.decisions (
  id                 bigint generated always as identity primary key,
  report_id          text not null references public.reports (id) on delete cascade,
  recorded_at        timestamptz not null default now(),
  decision           text not null check (decision in ('proceed', 'refer', 'decline')),
  reason             text not null check (char_length(reason) between 5 and 2000),
  underwriter        text not null default '',
  overrides_route    boolean not null default false,
  recommended_route  text not null,
  rules_version      text not null
);
create index if not exists decisions_report_idx on public.decisions (report_id, recorded_at);

-- Companies being watched for changes after the pre-screen.
create table if not exists public.watchlist (
  company_number    text primary key,
  company_name      text not null,
  added_at          timestamptz not null default now(),
  added_from_report text references public.reports (id) on delete set null,
  active            boolean not null default true,
  last_checked_at   timestamptz,
  last_route        text,
  snapshot          jsonb not null                 -- the facts compared on each re-check
);

-- What changed, found by the daily re-check.
create table if not exists public.changes (
  id              bigint generated always as identity primary key,
  company_number  text not null references public.watchlist (company_number) on delete cascade,
  detected_at     timestamptz not null default now(),
  kind            text not null,                   -- e.g. status, sanctions, officers, owners, insolvency, filings
  severity        text not null default 'note' check (severity in ('note', 'refer', 'outside_appetite', 'compliance_hold')),
  summary         text not null,
  before          jsonb,
  after           jsonb,
  route_before    text,
  route_after     text,
  seen            boolean not null default false
);
create index if not exists changes_detected_idx on public.changes (detected_at desc);
create index if not exists changes_company_idx on public.changes (company_number, detected_at desc);

-- Lock everything to the server connection.
alter table public.reports   enable row level security;
alter table public.decisions enable row level security;
alter table public.watchlist enable row level security;
alter table public.changes   enable row level security;
revoke all on public.reports, public.decisions, public.watchlist, public.changes from anon, authenticated;

-- Retention: reports expire after 90 days unless a decision was recorded or the company is watched.
create or replace function public.purge_expired_reports() returns integer
language sql security definer set search_path = public as $$
  with gone as (
    delete from public.reports r
    where r.expires_at < now()
      and not exists (select 1 from public.decisions d where d.report_id = r.id)
      and not exists (select 1 from public.watchlist w where w.added_from_report = r.id and w.active)
    returning 1
  )
  select count(*)::int from gone;
$$;
revoke all on function public.purge_expired_reports() from public, anon, authenticated;
