-- Link each detected change to the re-check report that found it.
alter table public.changes add column if not exists report_id text references public.reports (id) on delete set null;
