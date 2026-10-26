-- Pick A Fight — People's Scorecard database
-- Paste this whole file into Supabase → SQL Editor → Run. Safe to run again.
--
-- Security model: the browser uses the public "publishable" key. Visitors can
-- NOT read or write the table directly (RLS on, no policies, no grants). They
-- can only call two functions:
--   submit_score  → save/replace one device's pick for one round of one fight
--   score_summary → read vote COUNTS (never device ids or individual rows)
--   fan_verdicts  → read how many fans scored each fight for A / B / draw

create table if not exists public.fan_scores (
  fight_key  text     not null check (fight_key ~ '^f[0-9a-z]{1,15}$'),
  round      smallint not null check (round between 0 and 5),   -- 0 = whole-fight verdict (ONE)
  device_id  uuid     not null,
  pick       text     not null,
  updated_at timestamptz not null default now(),
  primary key (fight_key, round, device_id)
);

alter table public.fan_scores enable row level security;
revoke all on public.fan_scores from anon, authenticated;

-- Save one pick. Round picks: a108 a109 even b109 b108. Verdict picks (round 0):
-- a1 a2 a3 b1 b2 b3 (close / clear / dominant) or draw. 'clear' deletes the pick.
create or replace function public.submit_score(p_fight text, p_round int, p_device uuid, p_pick text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_round = 0 then
    if p_pick not in ('a1','a2','a3','b1','b2','b3','draw','clear') then
      raise exception 'invalid verdict';
    end if;
  elsif p_round between 1 and 5 then
    if p_pick not in ('a108','a109','even','b109','b108','clear') then
      raise exception 'invalid round score';
    end if;
  else
    raise exception 'invalid round';
  end if;

  if p_pick = 'clear' then
    delete from fan_scores where fight_key = p_fight and round = p_round and device_id = p_device;
    return;
  end if;

  insert into fan_scores (fight_key, round, device_id, pick)
  values (p_fight, p_round, p_device, p_pick)
  on conflict (fight_key, round, device_id)
  do update set pick = excluded.pick, updated_at = now();
end;
$$;

-- Vote counts per fight / round / pick (max 100 fights per call).
create or replace function public.score_summary(p_fights text[])
returns table (fight_key text, round smallint, pick text, n bigint)
language sql
stable
security definer
set search_path = public
as $$
  select s.fight_key, s.round, s.pick, count(*)
  from fan_scores s
  where s.fight_key = any (p_fights[1:100])
  group by 1, 2, 3;
$$;

-- Who each fan's full card goes to. A device counts once per fight: its
-- whole-fight verdict if it gave one, otherwise its round totals (needs 2+
-- scored rounds so a single early round doesn't decide a fight).
create or replace function public.fan_verdicts(p_fights text[])
returns table (fight_key text, winner text, n bigint)
language sql
stable
security definer
set search_path = public
as $$
  with per as (
    select s.fight_key, s.device_id,
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
         case
           when verdict is not null then
             case when verdict like 'a%' then 'a' when verdict like 'b%' then 'b' else 'draw' end
           when pa > pb then 'a'
           when pb > pa then 'b'
           else 'draw'
         end as winner,
         count(*)
  from per
  where verdict is not null or rounds_scored >= 2
  group by 1, 2;
$$;

revoke all on function public.submit_score(text, int, uuid, text) from public;
revoke all on function public.score_summary(text[]) from public;
revoke all on function public.fan_verdicts(text[]) from public;
grant execute on function public.submit_score(text, int, uuid, text) to anon, authenticated;
grant execute on function public.score_summary(text[]) to anon, authenticated;
grant execute on function public.fan_verdicts(text[]) to anon, authenticated;
