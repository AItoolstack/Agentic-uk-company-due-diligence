-- Agent reviews: one row per run of the multi-agent layer (src/agents_v2) on a saved report.
-- The payload holds the review (route copied from the report, specialist sections, guardrail results) and the
-- full trace (every model and tool call with tokens, cost and time). Same access rule as the other tables:
-- RLS on, no policies, server connection only.

create table if not exists public.agent_runs (
  id          text primary key,                 -- 16 hex chars, from the app
  report_id   text not null references public.reports (id) on delete cascade,
  created_at  timestamptz not null default now(),
  model       text not null default '',
  status      text not null default '',
  cost_usd    numeric(12, 6) not null default 0,
  wall_ms     integer not null default 0,
  payload     jsonb not null
);
create index if not exists agent_runs_report_idx on public.agent_runs (report_id, created_at desc);

alter table public.agent_runs enable row level security;
revoke all on public.agent_runs from anon, authenticated;
