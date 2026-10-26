-- Pick A Fight — database setup (Supabase)
-- Paste this whole file into Supabase → SQL Editor → Run. Safe to run again.
--
-- What lives here
--   profiles    public usernames (one per account)
--   accounts    each player's R balance + daily top-up
--   fights      fight list, odds, start times and results — written ONLY by
--               the GitHub sync job (secret key), readable by everyone
--   picks       every pick; created only through place_pick(), settled only
--               by settle_all() — players can't edit balances or results
--   fan_scores  People's Scorecard (one card per account per fight)
--   leagues     private groups with invite codes + weekly / all-time tables
--   takes       Hot Takes: one per player per fight, upvotes, graded after the result
--
-- Visitors use the public publishable key. Row Level Security is on for every
-- table; writes happen only through the functions below.

-- ───────────────────────── tables ─────────────────────────
create table if not exists public.profiles (
  id         uuid primary key references auth.users (id) on delete cascade,
  username   text not null check (username ~ '^[A-Za-z0-9_]{3,20}$'),
  created_at timestamptz not null default now()
);
create unique index if not exists profiles_username_lower on public.profiles (lower(username));

create table if not exists public.accounts (
  user_id    uuid primary key references auth.users (id) on delete cascade,
  balance    integer not null default 5000 check (balance >= 0),
  last_topup date,
  created_at timestamptz not null default now()
);

create table if not exists public.fights (
  fight_key     text primary key check (fight_key ~ '^f[0-9a-z]{1,15}$'),
  promotion     text not null,
  event_name    text not null,
  event_date    date,
  starts_at     timestamptz,          -- betting locks at this time
  fighter_a     text not null,
  fighter_b     text not null,
  discipline    text not null default 'mma',
  rounds        smallint not null default 3 check (rounds in (3, 5)),
  odds_a        numeric(6,2) not null check (odds_a > 1),
  odds_b        numeric(6,2) not null check (odds_b > 1),
  result_winner text check (result_winner in ('a', 'b', 'draw')),
  result_method text,                  -- 'KO/TKO' | 'Submission' | 'Decision' | null (unknown)
  result_round  smallint,
  result_finish boolean,               -- true = stopped early, false = went the distance
  updated_at    timestamptz not null default now()
);

create table if not exists public.picks (
  id         bigint generated always as identity primary key,
  user_id    uuid not null references auth.users (id) on delete cascade,
  fight_key  text not null references public.fights (fight_key) on delete cascade,
  market     text not null check (market in ('winner', 'method', 'round', 'distance')),
  selection  text not null,
  odds       numeric(6,2) not null,
  stake      integer not null check (stake > 0),
  status     text not null default 'pending' check (status in ('pending', 'won', 'lost', 'void')),
  payout     integer not null default 0,
  created_at timestamptz not null default now(),
  settled_at timestamptz
);
create index if not exists picks_user on public.picks (user_id, created_at desc);
create index if not exists picks_pending on public.picks (fight_key) where status = 'pending';

create table if not exists public.fan_scores (
  fight_key  text not null check (fight_key ~ '^f[0-9a-z]{1,15}$'),
  round      smallint not null check (round between 0 and 5),   -- 0 = whole-fight verdict (ONE)
  user_id    uuid not null references auth.users (id) on delete cascade,
  pick       text not null,
  updated_at timestamptz not null default now(),
  primary key (fight_key, round, user_id)
);

-- ───────────────────────── security ─────────────────────────
alter table public.profiles   enable row level security;
alter table public.accounts   enable row level security;
alter table public.fights     enable row level security;
alter table public.picks      enable row level security;
alter table public.fan_scores enable row level security;

revoke all on public.profiles, public.accounts, public.fights, public.picks, public.fan_scores
  from anon, authenticated;
grant select on public.profiles to anon, authenticated;     -- usernames are public
grant select on public.fights   to anon, authenticated;     -- fight list is public
grant select on public.accounts to authenticated;           -- own row only (policy below)
grant select on public.picks    to authenticated;           -- own rows only (policy below)

drop policy if exists "usernames are public" on public.profiles;
create policy "usernames are public" on public.profiles for select using (true);
drop policy if exists "fights are public" on public.fights;
create policy "fights are public" on public.fights for select using (true);
drop policy if exists "see own account" on public.accounts;
create policy "see own account" on public.accounts for select to authenticated using (user_id = auth.uid());
drop policy if exists "see own picks" on public.picks;
create policy "see own picks" on public.picks for select to authenticated using (user_id = auth.uid());

-- ───────────────────────── player functions ─────────────────────────
-- Creates the 5,000 R account on first sign-in; returns balance + username.
create or replace function public.my_account()
returns table (balance integer, username text, last_topup date)
language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is null then raise exception 'sign in first'; end if;
  insert into accounts (user_id) values (auth.uid()) on conflict do nothing;
  return query
    select a.balance, p.username, a.last_topup
    from accounts a left join profiles p on p.id = a.user_id
    where a.user_id = auth.uid();
end $$;

create or replace function public.set_username(p_name text)
returns text
language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is null then raise exception 'sign in first'; end if;
  if p_name !~ '^[A-Za-z0-9_]{3,20}$' then
    raise exception 'Usernames are 3-20 letters, numbers or underscores';
  end if;
  if exists (select 1 from profiles where lower(username) = lower(p_name) and id <> auth.uid()) then
    raise exception 'That username is taken';
  end if;
  insert into profiles (id, username) values (auth.uid(), p_name)
  on conflict (id) do update set username = excluded.username;
  return p_name;
end $$;

-- Odds for every market come from the server, never from the browser.
create or replace function public.market_odds(f public.fights, p_market text, p_selection text)
returns numeric
language plpgsql immutable as $$
declare is_mma boolean := f.discipline in ('mma', 'grappling');
begin
  if p_market = 'winner' then
    if p_selection = 'a' then return f.odds_a; elsif p_selection = 'b' then return f.odds_b; end if;
  elsif p_market = 'method' then
    if p_selection in ('KO/TKO', 'Decision') or (is_mma and p_selection = 'Submission') then
      return case when is_mma then 2.8 else 1.9 end;
    end if;
  elsif p_market = 'round' then
    if p_selection ~ '^[1-5]$' and p_selection::int <= f.rounds then
      return case when f.rounds >= 5 then 5.0 else 3.5 end;
    end if;
  elsif p_market = 'distance' then
    if p_selection in ('yes', 'no') then return 1.9; end if;
  end if;
  raise exception 'invalid pick';
end $$;

create or replace function public.place_pick(p_fight text, p_market text, p_selection text, p_stake integer)
returns table (pick_id bigint, balance integer, odds numeric)
language plpgsql security definer set search_path = public as $$
declare f fights; o numeric; b integer; new_id bigint;
begin
  if auth.uid() is null then raise exception 'sign in first'; end if;
  select * into f from fights where fight_key = p_fight;
  if not found then raise exception 'unknown fight'; end if;
  if f.result_winner is not null or (f.starts_at is not null and now() >= f.starts_at)
     or (f.starts_at is null and f.event_date is not null and current_date >= f.event_date) then
    raise exception 'Betting is closed for this fight';
  end if;
  if p_stake is null or p_stake < 1 then raise exception 'invalid stake'; end if;
  o := market_odds(f, p_market, p_selection);
  insert into accounts (user_id) values (auth.uid()) on conflict do nothing;
  update accounts set balance = accounts.balance - p_stake
    where user_id = auth.uid() and accounts.balance >= p_stake
    returning accounts.balance into b;
  if not found then raise exception 'Not enough R'; end if;
  insert into picks (user_id, fight_key, market, selection, odds, stake)
    values (auth.uid(), p_fight, p_market, p_selection, o, p_stake)
    returning id into new_id;
  return query select new_id, b, o;
end $$;

create or replace function public.claim_topup()
returns integer
language plpgsql security definer set search_path = public as $$
declare b integer;
begin
  if auth.uid() is null then raise exception 'sign in first'; end if;
  update accounts set balance = accounts.balance + 300, last_topup = current_date
    where user_id = auth.uid() and accounts.balance < 300
      and (last_topup is null or last_topup < current_date)
    returning accounts.balance into b;
  if not found then raise exception 'No top-up available right now'; end if;
  return b;
end $$;

-- Public leaderboard: usernames and balances only.
create or replace function public.leaderboard(p_limit integer default 25)
returns table (username text, balance integer, won bigint, settled bigint)
language sql stable security definer set search_path = public as $$
  select p.username, a.balance,
         count(*) filter (where k.status = 'won'),
         count(*) filter (where k.status in ('won', 'lost'))
  from accounts a
  join profiles p on p.id = a.user_id
  left join picks k on k.user_id = a.user_id
  group by p.username, a.balance
  order by a.balance desc, p.username
  limit least(greatest(p_limit, 1), 100);
$$;

-- ───────────────────────── People's Scorecard ─────────────────────────
-- Round picks: a108 a109 even b109 b108. Verdict (round 0): a1 a2 a3 b1 b2 b3
-- (close / clear / dominant) or draw. 'clear' removes your pick.
drop function if exists public.submit_score(text, int, uuid, text);
create or replace function public.submit_score(p_fight text, p_round int, p_pick text)
returns void
language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is null then raise exception 'sign in first'; end if;
  if p_round = 0 then
    if p_pick not in ('a1','a2','a3','b1','b2','b3','draw','clear') then raise exception 'invalid verdict'; end if;
  elsif p_round between 1 and 5 then
    if p_pick not in ('a108','a109','even','b109','b108','clear') then raise exception 'invalid round score'; end if;
  else
    raise exception 'invalid round';
  end if;
  if p_pick = 'clear' then
    delete from fan_scores where fight_key = p_fight and round = p_round and user_id = auth.uid();
    return;
  end if;
  insert into fan_scores (fight_key, round, user_id, pick) values (p_fight, p_round, auth.uid(), p_pick)
  on conflict (fight_key, round, user_id) do update set pick = excluded.pick, updated_at = now();
end $$;

create or replace function public.score_summary(p_fights text[])
returns table (fight_key text, round smallint, pick text, n bigint)
language sql stable security definer set search_path = public as $$
  select s.fight_key, s.round, s.pick, count(*)
  from fan_scores s
  where s.fight_key = any (p_fights[1:100])
  group by 1, 2, 3;
$$;

create or replace function public.fan_verdicts(p_fights text[])
returns table (fight_key text, winner text, n bigint)
language sql stable security definer set search_path = public as $$
  with per as (
    select s.fight_key, s.user_id,
      sum(case s.pick when 'a108' then 10 when 'a109' then 10 when 'even' then 10
                      when 'b109' then 9  when 'b108' then 8 end) filter (where s.round > 0) as pa,
      sum(case s.pick when 'b108' then 10 when 'b109' then 10 when 'even' then 10
                      when 'a109' then 9  when 'a108' then 8 end) filter (where s.round > 0) as pb,
      count(*) filter (where s.round > 0) as rounds_scored,
      max(s.pick) filter (where s.round = 0) as verdict
    from fan_scores s
    where s.fight_key = any (p_fights[1:100])
    group by 1, 2
  )
  select fight_key,
         case when verdict is not null then
                case when verdict like 'a%' then 'a' when verdict like 'b%' then 'b' else 'draw' end
              when pa > pb then 'a' when pb > pa then 'b' else 'draw' end,
         count(*)
  from per
  where verdict is not null or rounds_scored >= 2
  group by 1, 2;
$$;

-- ───────────────────────── settlement (sync job only) ─────────────────────────
-- Same rules as the site: winner picks settle on any result (draw = refund);
-- method/round/distance settle once the finish type is known; a finish of
-- unknown type refunds method picks.
create or replace function public.settle_all()
returns integer
language plpgsql security definer set search_path = public as $$
declare k record; outcome text; n integer := 0;
begin
  for k in
    select p.id, p.user_id, p.market, p.selection, p.stake, p.odds,
           f.result_winner w, f.result_method m, f.result_round r, f.result_finish fin
    from picks p join fights f using (fight_key)
    where p.status = 'pending' and f.result_winner is not null
    for update of p
  loop
    outcome := null;
    if k.market = 'winner' then
      outcome := case when k.w = 'draw' then 'void' when k.selection = k.w then 'won' else 'lost' end;
    elsif k.m = 'Draw' or k.w = 'draw' then
      outcome := 'void';
    elsif k.market = 'method' then
      if k.m is not null then outcome := case when k.selection = k.m then 'won' else 'lost' end;
      elsif k.fin is true then outcome := 'void';
      end if;
    elsif k.market = 'round' then
      if k.m = 'Decision' or k.fin is false then outcome := 'lost';
      elsif k.r is not null and (k.fin is true or k.m in ('KO/TKO', 'Submission')) then
        outcome := case when k.selection = k.r::text then 'won' else 'lost' end;
      end if;
    elsif k.market = 'distance' then
      if k.m = 'Decision' or k.fin is false then outcome := case when k.selection = 'yes' then 'won' else 'lost' end;
      elsif k.fin is true or k.m in ('KO/TKO', 'Submission') then outcome := case when k.selection = 'no' then 'won' else 'lost' end;
      end if;
    end if;

    if outcome is not null then
      update picks set status = outcome, settled_at = now(),
        payout = case outcome when 'won' then floor(k.stake * k.odds)::int when 'void' then k.stake else 0 end
      where id = k.id;
      if outcome in ('won', 'void') then
        update accounts set balance = balance +
          case outcome when 'won' then floor(k.stake * k.odds)::int else k.stake end
        where user_id = k.user_id;
      end if;
      n := n + 1;
    end if;
  end loop;
  perform grade_takes();
  return n;
end $$;

-- ───────────────────────── Leagues ─────────────────────────
-- Private groups with a 6-character invite code. Standings = profit on
-- settled picks (won: payout − stake, lost: −stake), this week (Mon 00:00 UTC)
-- or all time. Only members can see a league's table.
create table if not exists public.leagues (
  id         uuid primary key default gen_random_uuid(),
  name       text not null check (char_length(name) between 3 and 30),
  code       text not null unique check (code ~ '^[A-HJ-NP-Z2-9]{6}$'),
  owner      uuid not null references auth.users (id) on delete cascade,
  created_at timestamptz not null default now()
);
create table if not exists public.league_members (
  league_id uuid not null references public.leagues (id) on delete cascade,
  user_id   uuid not null references auth.users (id) on delete cascade,
  joined_at timestamptz not null default now(),
  primary key (league_id, user_id)
);
alter table public.leagues enable row level security;
alter table public.league_members enable row level security;
revoke all on public.leagues, public.league_members from anon, authenticated;

create or replace function public.create_league(p_name text)
returns table (league_id uuid, code text)
language plpgsql security definer set search_path = public as $$
declare c text; lid uuid; tries int := 0;
begin
  if auth.uid() is null then raise exception 'sign in first'; end if;
  p_name := btrim(p_name);
  if char_length(p_name) not between 3 and 30 then raise exception 'League names are 3-30 characters'; end if;
  if (select count(*) from leagues where owner = auth.uid()) >= 10 then raise exception 'You can run up to 10 leagues'; end if;
  loop  -- no 0/O/1/I so codes are easy to read out loud
    c := (select string_agg(substr('ABCDEFGHJKLMNPQRSTUVWXYZ23456789', 1 + floor(random() * 32)::int, 1), '')
          from generate_series(1, 6));
    exit when not exists (select 1 from leagues where leagues.code = c);
    tries := tries + 1; if tries > 20 then raise exception 'try again'; end if;
  end loop;
  insert into leagues (name, code, owner) values (p_name, c, auth.uid()) returning id into lid;
  insert into league_members (league_id, user_id) values (lid, auth.uid());
  return query select lid, c;
end $$;

create or replace function public.join_league(p_code text)
returns table (league_id uuid, name text)
language plpgsql security definer set search_path = public as $$
declare l leagues;
begin
  if auth.uid() is null then raise exception 'sign in first'; end if;
  select * into l from leagues where leagues.code = upper(btrim(p_code));
  if not found then raise exception 'No league with that code'; end if;
  if (select count(*) from league_members where league_members.league_id = l.id) >= 100 then raise exception 'That league is full'; end if;
  if (select count(*) from league_members where user_id = auth.uid()) >= 20 then raise exception 'You can be in up to 20 leagues'; end if;
  insert into league_members (league_id, user_id) values (l.id, auth.uid()) on conflict do nothing;
  return query select l.id, l.name;
end $$;

create or replace function public.leave_league(p_league uuid)
returns void
language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is null then raise exception 'sign in first'; end if;
  delete from league_members where league_id = p_league and user_id = auth.uid();
  -- an empty league disappears
  delete from leagues l where l.id = p_league and not exists (select 1 from league_members m where m.league_id = l.id);
end $$;

create or replace function public.my_leagues()
returns table (league_id uuid, name text, code text, members bigint, is_owner boolean)
language sql stable security definer set search_path = public as $$
  select l.id, l.name, l.code, (select count(*) from league_members x where x.league_id = l.id), l.owner = auth.uid()
  from leagues l join league_members m on m.league_id = l.id and m.user_id = auth.uid()
  order by l.created_at;
$$;

create or replace function public.league_table(p_league uuid, p_period text default 'week')
returns table (username text, profit bigint, won bigint, settled bigint)
language plpgsql stable security definer set search_path = public as $$
begin
  if not exists (select 1 from league_members where league_id = p_league and user_id = auth.uid()) then
    raise exception 'Join this league to see its table';
  end if;
  return query
    select coalesce(p.username, 'player'),
           coalesce(sum(case k.status when 'won' then k.payout - k.stake when 'lost' then -k.stake else 0 end), 0)::bigint,
           count(*) filter (where k.status = 'won'),
           count(*) filter (where k.status in ('won', 'lost'))
    from league_members m
    left join profiles p on p.id = m.user_id
    left join picks k on k.user_id = m.user_id and k.status in ('won', 'lost')
         and (p_period = 'all' or k.settled_at >= date_trunc('week', now()))
    where m.league_id = p_league
    group by m.user_id, p.username
    order by 2 desc, 3 desc, 1;
end $$;

-- ───────────────────────── Hot Takes ─────────────────────────
-- One take per player per fight, posted before it starts: back a side and say
-- why in 140 characters. Fans upvote. When the result lands, settle_all()
-- grades it: aged_well / aged_badly (void on a draw). 3 reports hides a take.
create table if not exists public.takes (
  id         bigint generated always as identity primary key,
  user_id    uuid not null references auth.users (id) on delete cascade,
  fight_key  text not null references public.fights (fight_key) on delete cascade,
  side       text not null check (side in ('a', 'b')),
  body       text not null check (char_length(btrim(body)) between 3 and 140),
  status     text not null default 'open' check (status in ('open', 'aged_well', 'aged_badly', 'void')),
  created_at timestamptz not null default now(),
  unique (user_id, fight_key)
);
create table if not exists public.take_votes (
  take_id bigint not null references public.takes (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  primary key (take_id, user_id)
);
create table if not exists public.take_reports (
  take_id bigint not null references public.takes (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  primary key (take_id, user_id)
);
alter table public.takes enable row level security;
alter table public.take_votes enable row level security;
alter table public.take_reports enable row level security;
revoke all on public.takes, public.take_votes, public.take_reports from anon, authenticated;

create or replace function public.post_take(p_fight text, p_side text, p_body text)
returns bigint
language plpgsql security definer set search_path = public as $$
declare f fights; new_id bigint;
begin
  if auth.uid() is null then raise exception 'sign in first'; end if;
  if not exists (select 1 from profiles where id = auth.uid()) then raise exception 'Pick a username first'; end if;
  select * into f from fights where fight_key = p_fight;
  if not found then raise exception 'unknown fight'; end if;
  if f.result_winner is not null or (f.starts_at is not null and now() >= f.starts_at) then
    raise exception 'Takes close when the event starts';
  end if;
  if p_side not in ('a', 'b') then raise exception 'Pick a side'; end if;
  p_body := btrim(regexp_replace(p_body, '\s+', ' ', 'g'));
  if char_length(p_body) not between 3 and 140 then raise exception 'Takes are 3-140 characters'; end if;
  if p_body ~* '(https?://|www\.)' then raise exception 'No links in takes'; end if;
  insert into takes (user_id, fight_key, side, body) values (auth.uid(), p_fight, p_side, p_body)
  on conflict (user_id, fight_key) do update set side = excluded.side, body = excluded.body, created_at = now()
  returning id into new_id;
  delete from take_votes where take_id = new_id;  -- an edited take starts over
  return new_id;
end $$;

create or replace function public.delete_take(p_take bigint)
returns void
language sql security definer set search_path = public as $$
  delete from takes where id = p_take and user_id = auth.uid() and status = 'open';
$$;

create or replace function public.vote_take(p_take bigint, p_up boolean)
returns void
language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is null then raise exception 'sign in first'; end if;
  if exists (select 1 from takes where id = p_take and user_id = auth.uid()) then raise exception 'You cannot vote on your own take'; end if;
  if p_up then insert into take_votes values (p_take, auth.uid()) on conflict do nothing;
  else delete from take_votes where take_id = p_take and user_id = auth.uid(); end if;
end $$;

create or replace function public.report_take(p_take bigint)
returns void
language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is null then raise exception 'sign in first'; end if;
  insert into take_reports values (p_take, auth.uid()) on conflict do nothing;
end $$;

create or replace function public.fight_takes(p_fights text[])
returns table (id bigint, fight_key text, username text, side text, body text, status text,
               votes bigint, voted boolean, mine boolean, created_at timestamptz)
language sql stable security definer set search_path = public as $$
  select t.id, t.fight_key, p.username, t.side, t.body, t.status,
         (select count(*) from take_votes v where v.take_id = t.id),
         exists (select 1 from take_votes v where v.take_id = t.id and v.user_id = auth.uid()),
         t.user_id = auth.uid(),
         t.created_at
  from takes t join profiles p on p.id = t.user_id
  where t.fight_key = any (p_fights[1:100])
    and (select count(*) from take_reports r where r.take_id = t.id) < 3
  order by 7 desc, t.created_at desc
  limit 500;
$$;

create or replace function public.grade_takes()
returns integer
language sql security definer set search_path = public as $$
  with g as (
    update takes t set status = case when f.result_winner = 'draw' then 'void'
                                     when t.side = f.result_winner then 'aged_well' else 'aged_badly' end
    from fights f
    where f.fight_key = t.fight_key and t.status = 'open' and f.result_winner is not null
    returning 1
  ) select count(*)::int from g;
$$;

-- ───────────────────────── who can call what ─────────────────────────
revoke all on function public.my_account(), public.set_username(text),
  public.place_pick(text, text, text, integer), public.claim_topup(), public.leaderboard(integer),
  public.submit_score(text, int, text), public.score_summary(text[]), public.fan_verdicts(text[]),
  public.settle_all(), public.market_odds(public.fights, text, text)
  from public, anon, authenticated;
grant execute on function public.leaderboard(integer), public.score_summary(text[]), public.fan_verdicts(text[])
  to anon, authenticated;
grant execute on function public.my_account(), public.set_username(text),
  public.place_pick(text, text, text, integer), public.claim_topup(),
  public.submit_score(text, int, text)
  to authenticated;
revoke all on function public.create_league(text), public.join_league(text), public.leave_league(uuid),
  public.my_leagues(), public.league_table(uuid, text), public.post_take(text, text, text),
  public.delete_take(bigint), public.vote_take(bigint, boolean), public.report_take(bigint),
  public.fight_takes(text[]), public.grade_takes()
  from public, anon, authenticated;
grant execute on function public.fight_takes(text[]) to anon, authenticated;
grant execute on function public.create_league(text), public.join_league(text), public.leave_league(uuid),
  public.my_leagues(), public.league_table(uuid, text), public.post_take(text, text, text),
  public.delete_take(bigint), public.vote_take(bigint, boolean), public.report_take(bigint)
  to authenticated;
-- settle_all() and writes to fights: service role only (the GitHub sync job).
grant execute on function public.settle_all() to service_role;
grant all on public.fights to service_role;
