-- Three-way evaluation: one row per run of the old research graph (POST /api/prescreen/eval/legacy).
-- Pre-screens and agent reviews are already stored in reports and agent_runs.
create table if not exists public.eval_runs (
  id          text primary key,
  created_at  timestamptz not null default now(),
  system      text not null,
  label       text not null default '',
  payload     jsonb not null
);
create index if not exists eval_runs_created_idx on public.eval_runs (created_at desc);
alter table public.eval_runs enable row level security;
revoke all on public.eval_runs from anon, authenticated;
