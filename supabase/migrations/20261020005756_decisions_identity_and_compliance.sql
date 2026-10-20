-- Case page (blueprint change 5): record whether the underwriter confirmed the identity match, and allow
-- "compliance" (referred to compliance), the only decision on a compliance hold. Additive: old rows keep
-- identity_confirmed = false, and the old values stay valid.
alter table public.decisions add column if not exists identity_confirmed boolean not null default false;
alter table public.decisions drop constraint if exists decisions_decision_check;
alter table public.decisions add constraint decisions_decision_check
  check (decision in ('proceed', 'refer', 'decline', 'compliance'));
