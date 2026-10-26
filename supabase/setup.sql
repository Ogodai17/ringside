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
  return n;
end $$;

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
-- settle_all() and writes to fights: service role only (the GitHub sync job).
grant execute on function public.settle_all() to service_role;
grant all on public.fights to service_role;
