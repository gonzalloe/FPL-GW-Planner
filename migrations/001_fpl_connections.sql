-- Run in Supabase SQL editor. The Flask server uses the service-role key.
create table if not exists public.fpl_connections (
    owner_key text primary key,
    payload jsonb not null
);
alter table public.fpl_connections enable row level security;
revoke all on public.fpl_connections from anon, authenticated;
grant select, insert, update, delete on public.fpl_connections to service_role;
-- No browser-facing policies: access is only through authenticated Flask routes.
