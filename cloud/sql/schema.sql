-- Pengwin — schema Postgres/Supabase
-- Eseguire in Supabase: SQL Editor -> New query -> incollare -> Run
-- Idempotente: si puo' rieseguire senza danni.

-- =========================================================
-- 1. ANAGRAFICA SQUADRE (ponte fra nomi football-data e Understat)
-- =========================================================
create table if not exists squadre (
  id                  bigserial primary key,
  nome_canonico       text not null unique,
  nome_football_data  text,
  nome_understat      text,
  lega                text,
  creato_il           timestamptz not null default now()
);
create index if not exists idx_squadre_fd on squadre (nome_football_data);
create index if not exists idx_squadre_us on squadre (nome_understat);

-- =========================================================
-- 2. PARTITE — risultati storici + quote (football-data.co.uk)
-- =========================================================
create table if not exists partite (
  id                bigserial primary key,
  lega              text not null,          -- E0, I1, SP1, D1, F1
  stagione          text not null,          -- '2627'
  data              date not null,
  ora               time,
  casa              text not null,
  trasferta         text not null,

  gol_casa          smallint,
  gol_trasferta     smallint,
  esito             text,                   -- H / D / A
  gol_casa_ht       smallint,
  gol_trasferta_ht  smallint,

  tiri_casa         smallint,
  tiri_trasferta    smallint,
  tiri_porta_casa   smallint,
  tiri_porta_trasf  smallint,
  corner_casa       smallint,
  corner_trasferta  smallint,

  -- QUOTE DI CHIUSURA (colonne football-data con la C: AvgC*, MaxC*, B365C*, PSC*, BFEC*)
  q_avg_1  numeric, q_avg_x  numeric, q_avg_2  numeric,   -- media mercato
  q_max_1  numeric, q_max_x  numeric, q_max_2  numeric,   -- massima disponibile
  q_b365_1 numeric, q_b365_x numeric, q_b365_2 numeric,
  q_ps_1   numeric, q_ps_x   numeric, q_ps_2   numeric,   -- Pinnacle: assente dal 2026/27
  q_bfe_1  numeric, q_bfe_x  numeric, q_bfe_2  numeric,   -- Betfair Exchange: benchmark attuale
  q_avg_over25  numeric, q_avg_under25 numeric,
  q_bfe_over25  numeric, q_bfe_under25 numeric,
  -- QUOTE DI APERTURA (colonne senza C) — non confondere con le chiusure
  q_ap_avg_1 numeric, q_ap_avg_x numeric, q_ap_avg_2 numeric,
  q_ap_max_1 numeric, q_ap_max_x numeric, q_ap_max_2 numeric,
  -- xG pubblicati da football-data dal 2026/27 (cross-check, non sostituiscono Understat)
  xg_casa_fd numeric, xg_trasferta_fd numeric,

  raw           jsonb,
  aggiornato_il timestamptz not null default now(),

  constraint partite_uniq unique (lega, stagione, data, casa, trasferta)
);
create index if not exists idx_partite_data on partite (data);
create index if not exists idx_partite_lega_stag on partite (lega, stagione);

-- =========================================================
-- 3. XG PARTITE (Understat)
-- =========================================================
create table if not exists xg_partite (
  id              bigserial primary key,
  understat_id    text not null unique,
  lega_us         text not null,            -- EPL, Serie_A, La_liga, Bundesliga, Ligue_1
  stagione        integer not null,         -- 2026 = stagione 2026/27
  datetime        timestamp not null,
  casa            text not null,
  trasferta       text not null,
  gol_casa        smallint,
  gol_trasferta   smallint,
  xg_casa         numeric,
  xg_trasferta    numeric,
  disputata       boolean not null default false,
  aggiornato_il   timestamptz not null default now()
);
create index if not exists idx_xg_datetime on xg_partite (datetime);
create index if not exists idx_xg_lega_stag on xg_partite (lega_us, stagione);

-- =========================================================
-- 4. FIXTURES — partite future con quote correnti
-- =========================================================
create table if not exists fixtures (
  id            bigserial primary key,
  lega          text not null,
  data          date not null,
  ora           time,
  casa          text not null,
  trasferta     text not null,
  q_avg_1  numeric, q_avg_x  numeric, q_avg_2  numeric,
  q_max_1  numeric, q_max_x  numeric, q_max_2  numeric,
  q_b365_1 numeric, q_b365_x numeric, q_b365_2 numeric,
  q_bfe_1  numeric, q_bfe_x  numeric, q_bfe_2  numeric,
  q_avg_over25 numeric, q_avg_under25 numeric,
  q_bfe_over25 numeric, q_bfe_under25 numeric,
  q_ap_avg_1 numeric, q_ap_avg_x numeric, q_ap_avg_2 numeric,
  q_ap_max_1 numeric, q_ap_max_x numeric, q_ap_max_2 numeric,
  raw           jsonb,
  aggiornato_il timestamptz not null default now(),
  constraint fixtures_uniq unique (lega, data, casa, trasferta)
);

-- =========================================================
-- 5. PREVISIONI — immutabili per costruzione
--    (principio 00-specifica-sistema.md par.3: salvate con timestamp
--     PRIMA dell'evento e mai modificate a posteriori)
-- =========================================================
create table if not exists previsioni (
  id             bigserial primary key,
  creata_il      timestamptz not null default now(),
  modello        text not null,          -- es. 'dixon_coles_xg'
  versione       text,                   -- es. 'blend35-65_xi0.0018_w3y'
  lega           text not null,
  data_partita   date not null,
  casa           text not null,
  trasferta      text not null,
  mercato        text not null,          -- '1X2' | 'OU25' | 'GG' | 'DC'
  selezione      text not null,          -- '1','X','2','over','under','gg','ng','1X','X2','12'
  prob           numeric not null check (prob > 0 and prob < 1),
  quota_offerta  numeric,
  bookmaker      text,
  edge           numeric,
  note           text
);
create index if not exists idx_prev_partita on previsioni (data_partita, casa, trasferta);
create index if not exists idx_prev_creata on previsioni (creata_il);

create or replace function blocca_modifica_previsioni()
returns trigger language plpgsql as $$
begin
  raise exception 'Le previsioni sono immutabili: UPDATE/DELETE non consentiti (vedi 00-specifica-sistema.md par.3).';
end;
$$;

drop trigger if exists trg_previsioni_immutabili on previsioni;
create trigger trg_previsioni_immutabili
  before update or delete on previsioni
  for each row execute function blocca_modifica_previsioni();

-- =========================================================
-- 6. GIOCATE — ledger dell'esperimento a bankroll simulato
-- =========================================================
create table if not exists giocate (
  id                  bigserial primary key,
  previsione_id       bigint references previsioni(id),
  piazzata_il         timestamptz not null default now(),
  turno               text,                       -- es. '2026-W36'
  lega                text,
  data_partita        date,
  casa                text,
  trasferta           text,
  mercato             text,
  selezione           text,
  quota               numeric not null,
  stake               numeric not null,
  bankroll_al_momento numeric,
  prob_modello        numeric,
  edge                numeric,
  esito               text not null default 'aperta',  -- aperta|vinta|persa|void
  ritorno             numeric,
  quota_chiusura      numeric,
  clv                 numeric,
  chiusa_il           timestamptz,
  note                text
);
create index if not exists idx_giocate_turno on giocate (turno);
create index if not exists idx_giocate_esito on giocate (esito);

-- =========================================================
-- 7. LOG ESECUZIONI (attivita' pianificate)
-- =========================================================
create table if not exists log_esecuzioni (
  id         bigserial primary key,
  eseguito_il timestamptz not null default now(),
  job        text not null,
  esito      text not null,        -- ok | errore
  righe      integer,
  dettaglio  text
);

-- =========================================================
-- 8. VISTE DI SINTESI
-- =========================================================
create or replace view v_ledger_sintesi as
select
  turno,
  count(*)                                                   as giocate,
  round(sum(stake)::numeric, 2)                              as stake_totale,
  round(sum(coalesce(ritorno,0))::numeric, 2)                as ritorno_totale,
  round((sum(coalesce(ritorno,0)) - sum(stake))::numeric, 2)  as profitto,
  case when sum(stake) > 0
       then round(((sum(coalesce(ritorno,0)) - sum(stake)) / sum(stake) * 100)::numeric, 2)
  end                                                         as roi_pct,
  round(avg(clv)::numeric, 4)                                 as clv_medio,
  count(*) filter (where clv > 0)                             as clv_positivi
from giocate
where esito <> 'aperta'
group by turno
order by turno;

create or replace view v_partite_con_xg as
select p.*, x.xg_casa, x.xg_trasferta
from partite p
left join xg_partite x
  on x.casa = p.casa and x.trasferta = p.trasferta and x.datetime::date = p.data;

-- =========================================================
-- 9. RLS
--    Default: accesso completo con la publishable key.
--    Per irrigidire: eliminare le policy 'pengwin_anon_*' e usare
--    una secret key (sb_secret_...) nel container.
-- =========================================================
do $$
declare t text;
begin
  foreach t in array array['squadre','partite','xg_partite','fixtures','previsioni','giocate','log_esecuzioni']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists pengwin_anon_all on %I', t);
    execute format('create policy pengwin_anon_all on %I for all to anon, authenticated using (true) with check (true)', t);
  end loop;
end $$;

select 'schema pengwin creato' as stato;
