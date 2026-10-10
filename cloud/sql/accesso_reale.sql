-- =====================================================================
-- Pengwin — accesso privato al braccio reale (10 ottobre 2026)
--
-- Da lanciare UNA volta nel SQL Editor di Supabase. Idempotente.
--
-- Fino a oggi giocate_reali era leggibile solo dalla secret key del server
-- (RLS senza policy). La dashboard privata (docs/reale.html) deve leggerla
-- dal browser, quindi:
--   1. accesso_reale — gli account Supabase Auth autorizzati. Le righe si
--      aggiungono a mano dopo aver creato gli utenti (in fondo). Nessuna email
--      in questo file: il repository e' pubblico.
--   2. saldi_reali   — il saldo Betfair letto dal server a ogni
--      contabilizzazione (src/reale/contabilizza.py).
--   3. policy di SOLA LETTURA su giocate_reali e saldi_reali per gli account
--      in accesso_reale. Nessuna policy di scrittura: scrive solo il server.
--   4. la chiave pubblica (anon) perde ogni permesso su queste tabelle. La RLS
--      la bloccava gia'; cosi' e' bloccata due volte.
-- =====================================================================

-- 1. account autorizzati ----------------------------------------------------
create table if not exists accesso_reale (
  uid          uuid primary key references auth.users (id) on delete cascade,
  aggiunto_il  timestamptz not null default now()
);
alter table accesso_reale enable row level security;
-- ognuno vede solo la propria riga: serve alla policy qui sotto
drop policy if exists accesso_proprio on accesso_reale;
create policy accesso_proprio on accesso_reale
  for select to authenticated using (uid = (select auth.uid()));

-- 2. saldo del conto --------------------------------------------------------
create table if not exists saldi_reali (
  id           bigserial primary key,
  il           timestamptz not null default now(),
  disponibile  numeric not null,
  esposizione  numeric not null,
  totale       numeric not null
);
create index if not exists idx_saldi_reali_il on saldi_reali (il);
alter table saldi_reali enable row level security;

-- 3. sola lettura per gli autorizzati ----------------------------------------
drop policy if exists reale_lettura_autorizzati on giocate_reali;
create policy reale_lettura_autorizzati on giocate_reali
  for select to authenticated
  using (exists (select 1 from accesso_reale a where a.uid = (select auth.uid())));

drop policy if exists saldi_lettura_autorizzati on saldi_reali;
create policy saldi_lettura_autorizzati on saldi_reali
  for select to authenticated
  using (exists (select 1 from accesso_reale a where a.uid = (select auth.uid())));

-- solo lettura anche a livello di permessi: la RLS blocca gia' le scritture,
-- ma TRUNCATE non passa dalla RLS
revoke insert, update, delete, truncate, references, trigger
  on giocate_reali, saldi_reali, accesso_reale from authenticated;
grant select on giocate_reali, saldi_reali, accesso_reale to authenticated;

-- 4. niente alla chiave pubblica ---------------------------------------------
revoke all on giocate_reali, saldi_reali, accesso_reale, ordini_log from anon;

-- Verifica
select tablename, policyname, cmd, roles::text
from pg_policies
where tablename in ('giocate_reali', 'saldi_reali', 'accesso_reale')
order by tablename;

-- ---------------------------------------------------------------------
-- DOPO aver creato i due utenti (Authentication -> Users -> Add user),
-- con le iscrizioni libere spente, si autorizzano cosi':
--
--   insert into accesso_reale (uid) select id from auth.users
--   on conflict do nothing;
--   select u.email, a.aggiunto_il from accesso_reale a join auth.users u on u.id = a.uid;
--
-- L'ultima riga deve elencare esattamente i due account previsti.
-- ---------------------------------------------------------------------
