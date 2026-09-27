create extension if not exists "pgcrypto";

create table if not exists public.events (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text unique not null,
  venue text,
  starts_at timestamptz not null,
  ends_at timestamptz not null,
  status text not null default 'draft',
  brand_color text default '#7568f3',
  created_at timestamptz not null default now()
);

create table if not exists public.sessions (
  id uuid primary key default gen_random_uuid(),
  event_id uuid not null references public.events(id) on delete cascade,
  title text not null,
  track text,
  room text,
  speaker text,
  starts_at timestamptz not null,
  ends_at timestamptz not null,
  status text not null default 'scheduled',
  attendance integer default 0,
  sentiment numeric default 0.82,
  summary text,
  created_at timestamptz not null default now()
);

create table if not exists public.attendees (
  id uuid primary key default gen_random_uuid(),
  event_id uuid not null references public.events(id) on delete cascade,
  full_name text not null,
  company text,
  role text,
  intent_score integer default 0,
  checked_in boolean default false,
  interests text[] default '{}',
  created_at timestamptz not null default now()
);

create table if not exists public.insights (
  id uuid primary key default gen_random_uuid(),
  event_id uuid not null references public.events(id) on delete cascade,
  session_id uuid references public.sessions(id) on delete cascade,
  kind text not null,
  title text not null,
  body text not null,
  confidence numeric default 0.9,
  created_at timestamptz not null default now()
);

create table if not exists public.share_links (
  id uuid primary key default gen_random_uuid(),
  event_id uuid not null references public.events(id) on delete cascade,
  session_id uuid references public.sessions(id) on delete cascade,
  label text not null,
  token text unique not null,
  destination text not null,
  clicks integer default 0,
  created_at timestamptz not null default now()
);

create table if not exists public.transcripts (
  id uuid primary key default gen_random_uuid(),
  event_id uuid not null references public.events(id) on delete cascade,
  session_id uuid references public.sessions(id) on delete cascade,
  language text default 'auto',
  model text not null,
  text text not null,
  created_at timestamptz not null default now()
);

alter table public.events enable row level security;
alter table public.sessions enable row level security;
alter table public.attendees enable row level security;
alter table public.insights enable row level security;
alter table public.share_links enable row level security;
alter table public.transcripts enable row level security;
