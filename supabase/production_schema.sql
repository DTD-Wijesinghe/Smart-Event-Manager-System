-- Smart Event Manager production data foundation.
-- Run after reviewing in Supabase SQL Editor. Secrets never belong in this file.
create extension if not exists "pgcrypto";

create or replace function public.set_updated_at() returns trigger
language plpgsql as $$ begin new.updated_at = now(); return new; end; $$;

create table if not exists public.organizations (
  id uuid primary key default gen_random_uuid(), name text not null, slug text unique not null,
  logo_url text, website text, industry text, timezone text not null default 'UTC',
  preferred_language text not null default 'en', created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade, full_name text, avatar_url text,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table if not exists public.organization_members (
  organization_id uuid references public.organizations(id) on delete cascade,
  user_id uuid references public.profiles(id) on delete cascade,
  role text not null check (role in ('super_admin','organization_admin','event_organizer','content_editor','speaker','attendee')),
  status text not null default 'active' check (status in ('invited','active','suspended')),
  created_at timestamptz not null default now(), primary key (organization_id, user_id)
);
create table if not exists public.events (
  id uuid primary key default gen_random_uuid(), organization_id uuid not null references public.organizations(id) on delete cascade,
  title text not null, slug text not null, description text, starts_at timestamptz not null, ends_at timestamptz not null,
  timezone text not null default 'UTC', venue text, event_type text not null default 'conference', banner_url text,
  default_language text not null default 'en', translation_languages text[] not null default '{en}',
  privacy text not null default 'public' check (privacy in ('public','private')), status text not null default 'draft'
    check (status in ('draft','scheduled','live','completed','archived')), created_by uuid references public.profiles(id),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique (organization_id, slug)
);
create table if not exists public.sessions (
  id uuid primary key default gen_random_uuid(), event_id uuid not null references public.events(id) on delete cascade,
  title text not null, description text, starts_at timestamptz not null, ends_at timestamptz not null, room text, track text,
  spoken_language text not null default 'en', translation_languages text[] not null default '{en}',
  session_type text not null default 'live' check (session_type in ('live','online','on_demand')),
  meeting_url text, tags text[] not null default '{}', status text not null default 'scheduled'
    check (status in ('draft','scheduled','live','completed','archived')), created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table if not exists public.speakers (
  id uuid primary key default gen_random_uuid(), organization_id uuid references public.organizations(id) on delete cascade,
  name text not null, title text, company text, biography text, photo_url text, profile_url text, created_at timestamptz not null default now()
);
create table if not exists public.session_speakers (
  session_id uuid references public.sessions(id) on delete cascade, speaker_id uuid references public.speakers(id) on delete cascade,
  sort_order integer not null default 0, primary key (session_id, speaker_id)
);
create table if not exists public.transcript_segments (
  id uuid primary key default gen_random_uuid(), session_id uuid not null references public.sessions(id) on delete cascade,
  speaker_id uuid references public.speakers(id) on delete set null, text text not null, language text not null default 'auto',
  start_time_ms integer, end_time_ms integer, confidence numeric check (confidence between 0 and 1), source text not null default 'upload',
  created_at timestamptz not null default now()
);
create table if not exists public.translations (
  id uuid primary key default gen_random_uuid(), transcript_segment_id uuid not null references public.transcript_segments(id) on delete cascade,
  language text not null, text text not null, created_at timestamptz not null default now(), unique (transcript_segment_id, language)
);
-- Compatibility table for the current REST API. The normalized transcript_segments
-- table remains the source for future diarization/translation fan-out.
create table if not exists public.transcripts (
  id uuid primary key default gen_random_uuid(), event_id uuid references public.events(id) on delete cascade,
  session_id uuid references public.sessions(id) on delete cascade, language text not null default 'auto',
  model text not null default 'manual-capture', text text not null, speaker text, created_at timestamptz not null default now()
);
-- Read-model compatibility tables used by the original command center.
create table if not exists public.insights (
  id uuid primary key default gen_random_uuid(), event_id uuid references public.events(id) on delete cascade,
  session_id uuid references public.sessions(id) on delete cascade, kind text not null, title text not null,
  body text not null, confidence numeric default 0.9, created_at timestamptz not null default now()
);
create table if not exists public.share_links (
  id uuid primary key default gen_random_uuid(), event_id uuid references public.events(id) on delete cascade,
  label text not null, token text unique not null, destination text not null, clicks integer not null default 0,
  created_at timestamptz not null default now()
);
create table if not exists public.takeaways (
  id uuid primary key default gen_random_uuid(), session_id uuid not null references public.sessions(id) on delete cascade,
  title text not null, body text not null, confidence numeric, evidence jsonb not null default '[]', created_at timestamptz not null default now()
);
create table if not exists public.topics (
  id uuid primary key default gen_random_uuid(), event_id uuid not null references public.events(id) on delete cascade,
  label text not null, weight numeric not null default 0, first_seen_at timestamptz, last_seen_at timestamptz, created_at timestamptz not null default now(), unique (event_id, label)
);
create table if not exists public.topic_relations (
  topic_id uuid references public.topics(id) on delete cascade, related_topic_id uuid references public.topics(id) on delete cascade,
  strength numeric not null default 0, primary key (topic_id, related_topic_id), check (topic_id <> related_topic_id)
);
create table if not exists public.summaries (
  id uuid primary key default gen_random_uuid(), session_id uuid references public.sessions(id) on delete cascade,
  event_id uuid references public.events(id) on delete cascade, content jsonb not null default '{}', model text, created_at timestamptz not null default now()
);
create table if not exists public.attendees (
  id uuid primary key default gen_random_uuid(), event_id uuid not null references public.events(id) on delete cascade,
  user_id uuid references public.profiles(id) on delete set null, anonymous_token text unique, full_name text, email text,
  checked_in boolean not null default false, created_at timestamptz not null default now()
);
create table if not exists public.attendee_preferences (
  attendee_id uuid primary key references public.attendees(id) on delete cascade, role text, industry text, interests text[] not null default '{}', language text not null default 'en'
);
create table if not exists public.questions (
  id uuid primary key default gen_random_uuid(), session_id uuid not null references public.sessions(id) on delete cascade,
  attendee_id uuid references public.attendees(id) on delete set null, body text not null, anonymous boolean not null default false,
  status text not null default 'pending' check (status in ('pending','approved','rejected','answered','hidden')), pinned boolean not null default false,
  created_at timestamptz not null default now()
);
create table if not exists public.question_votes (
  question_id uuid references public.questions(id) on delete cascade, attendee_id uuid references public.attendees(id) on delete cascade,
  created_at timestamptz not null default now(), primary key (question_id, attendee_id)
);
create table if not exists public.polls (
  id uuid primary key default gen_random_uuid(), session_id uuid not null references public.sessions(id) on delete cascade,
  question text not null, poll_type text not null default 'single', is_open boolean not null default false, created_at timestamptz not null default now()
);
create table if not exists public.poll_options (
  id uuid primary key default gen_random_uuid(), poll_id uuid not null references public.polls(id) on delete cascade, label text not null, sort_order integer not null default 0
);
create table if not exists public.poll_responses (
  id uuid primary key default gen_random_uuid(), poll_id uuid not null references public.polls(id) on delete cascade,
  option_id uuid references public.poll_options(id) on delete cascade, attendee_id uuid references public.attendees(id) on delete cascade,
  answer_text text, rating integer, created_at timestamptz not null default now()
);
create table if not exists public.feedback (
  id uuid primary key default gen_random_uuid(), session_id uuid not null references public.sessions(id) on delete cascade,
  attendee_id uuid references public.attendees(id) on delete set null, speaker_rating integer, content_rating integer, session_rating integer, comment text, created_at timestamptz not null default now()
);
create table if not exists public.brand_kits (
  id uuid primary key default gen_random_uuid(), organization_id uuid not null references public.organizations(id) on delete cascade,
  name text not null default 'Default brand', logo_url text, primary_color text, secondary_color text, accent_color text, font_family text, tone text, website text, created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table if not exists public.generated_assets (
  id uuid primary key default gen_random_uuid(), event_id uuid not null references public.events(id) on delete cascade,
  created_by uuid references public.profiles(id) on delete set null, asset_type text not null, title text not null, content jsonb not null default '{}', status text not null default 'draft', created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table if not exists public.processing_jobs (
  id uuid primary key default gen_random_uuid(), event_id uuid references public.events(id) on delete cascade, session_id uuid references public.sessions(id) on delete cascade,
  job_type text not null, status text not null default 'queued' check (status in ('queued','running','completed','failed','retrying')), progress integer not null default 0 check (progress between 0 and 100), error_message text, attempts integer not null default 0, created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table if not exists public.audit_logs (
  id uuid primary key default gen_random_uuid(), organization_id uuid references public.organizations(id) on delete cascade, user_id uuid references public.profiles(id) on delete set null, action text not null, resource_type text, resource_id uuid, metadata jsonb not null default '{}', created_at timestamptz not null default now()
);

create index if not exists transcript_segments_session_time_idx on public.transcript_segments(session_id, start_time_ms);
create index if not exists sessions_event_time_idx on public.sessions(event_id, starts_at);
create index if not exists questions_session_status_idx on public.questions(session_id, status, created_at desc);
create index if not exists jobs_status_idx on public.processing_jobs(status, created_at);
create index if not exists assets_event_idx on public.generated_assets(event_id, created_at desc);

drop trigger if exists organizations_updated_at on public.organizations;
create trigger organizations_updated_at before update on public.organizations for each row execute function public.set_updated_at();
drop trigger if exists profiles_updated_at on public.profiles;
create trigger profiles_updated_at before update on public.profiles for each row execute function public.set_updated_at();
drop trigger if exists events_updated_at on public.events;
create trigger events_updated_at before update on public.events for each row execute function public.set_updated_at();
drop trigger if exists sessions_updated_at on public.sessions;
create trigger sessions_updated_at before update on public.sessions for each row execute function public.set_updated_at();
drop trigger if exists brand_kits_updated_at on public.brand_kits;
create trigger brand_kits_updated_at before update on public.brand_kits for each row execute function public.set_updated_at();
drop trigger if exists assets_updated_at on public.generated_assets;
create trigger assets_updated_at before update on public.generated_assets for each row execute function public.set_updated_at();
drop trigger if exists jobs_updated_at on public.processing_jobs;
create trigger jobs_updated_at before update on public.processing_jobs for each row execute function public.set_updated_at();

alter table public.organizations enable row level security;
alter table public.profiles enable row level security;
alter table public.organization_members enable row level security;
alter table public.events enable row level security;
alter table public.sessions enable row level security;
alter table public.speakers enable row level security;
alter table public.session_speakers enable row level security;
alter table public.transcript_segments enable row level security;
alter table public.translations enable row level security;
alter table public.transcripts enable row level security;
alter table public.insights enable row level security;
alter table public.share_links enable row level security;
alter table public.takeaways enable row level security;
alter table public.topics enable row level security;
alter table public.topic_relations enable row level security;
alter table public.summaries enable row level security;
alter table public.attendees enable row level security;
alter table public.attendee_preferences enable row level security;
alter table public.questions enable row level security;
alter table public.question_votes enable row level security;
alter table public.polls enable row level security;
alter table public.poll_options enable row level security;
alter table public.poll_responses enable row level security;
alter table public.feedback enable row level security;
alter table public.brand_kits enable row level security;
alter table public.generated_assets enable row level security;
alter table public.processing_jobs enable row level security;
alter table public.audit_logs enable row level security;
