-- Optional starter data for local/demo workspaces.
-- Run production_schema.sql first. This file contains no secrets.
begin;

with org as (
  insert into public.organizations (name, slug, timezone, preferred_language)
  values ('Atelier Events', 'atelier-events', 'Asia/Colombo', 'en')
  on conflict (slug) do update set name = excluded.name
  returning id
), event_row as (
  insert into public.events (organization_id, title, slug, description, starts_at, ends_at, timezone, venue, status, privacy)
  select id, 'Global Futures Forum', 'global-futures-forum',
    'A live event content intelligence demo workspace.',
    '2026-09-26T08:30:00Z', '2026-09-26T16:30:00Z', 'Asia/Colombo', 'Marina Bay Sands · Singapore', 'live', 'public'
  from org
  on conflict (organization_id, slug) do update set title = excluded.title, status = excluded.status
  returning id, organization_id
)
insert into public.sessions (event_id, title, description, starts_at, ends_at, room, track, status)
select id, 'Designing for what’s next', 'The most resilient organizations build the habit of responding together.',
  '2026-09-26T09:00:00Z', '2026-09-26T09:45:00Z', 'Auditorium 1', 'Main stage', 'live'
from event_row
where not exists (select 1 from public.sessions s where s.event_id = event_row.id and s.title = 'Designing for what’s next');

insert into public.insights (event_id, session_id, kind, title, body, confidence)
select e.id, s.id, 'theme', 'Collective response is the new advantage',
  'Across live signals, attendees connected resilience with shared rituals, not isolated prediction.', .94
from public.events e
join public.sessions s on s.event_id = e.id and s.title = 'Designing for what’s next'
where e.slug = 'global-futures-forum'
  and not exists (select 1 from public.insights i where i.event_id = e.id and i.title = 'Collective response is the new advantage');

insert into public.share_links (event_id, label, token, destination, clicks)
select id, 'Attendee portal', 'gff-live', '/attendee/global-futures-forum', 0
from public.events
where slug = 'global-futures-forum'
on conflict (token) do nothing;

commit;
