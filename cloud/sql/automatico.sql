-- =====================================================================
-- Pengwin — turno automatico, interruttore ed esperimento notizie
-- (10 ottobre 2026)
--
-- Da lanciare UNA volta nel SQL Editor di Supabase, DOPO accesso_reale.sql.
-- Idempotente.
--
-- Cosa crea:
--   1. config_reale         — l'interruttore del turno automatico. Lo
--                             cambiano solo gli account autorizzati, dalla
--                             pagina privata; chi e quando lo scrive il
--                             database, non la pagina. Parte SPENTO.
--   2. config_reale_storico — ogni accensione e spegnimento.
--   3. notizie_giocate      — il giudizio sulle notizie di ogni giocata,
--                             scritto prima del via dall'attivita' programmata
--                             di Claude. Solo su carta: non tocca le giocate.
--   4. le pre-registrazioni `braccio_reale_automatico` e `notizie_carta`.
-- =====================================================================

-- 1. interruttore -------------------------------------------------------------
create table if not exists config_reale (
  id             int primary key default 1 check (id = 1),
  attivo         boolean not null default false,
  modificato_il  timestamptz not null default now(),
  modificato_da  text
);
insert into config_reale (id, attivo, modificato_da) values (1, false, 'creazione')
on conflict (id) do nothing;

create table if not exists config_reale_storico (
  id      bigserial primary key,
  il      timestamptz not null default now(),
  attivo  boolean not null,
  da      text
);

-- chi e quando li decide il database: la pagina manda solo `attivo`
create or replace function config_reale_firma()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  new.id := 1;
  new.modificato_il := now();
  new.modificato_da := coalesce(nullif(auth.jwt() ->> 'email', ''), current_user);
  if new.attivo is distinct from old.attivo then
    insert into config_reale_storico (attivo, da) values (new.attivo, new.modificato_da);
  end if;
  return new;
end;
$$;
drop trigger if exists trg_config_reale on config_reale;
create trigger trg_config_reale before update on config_reale
  for each row execute function config_reale_firma();

alter table config_reale enable row level security;
alter table config_reale_storico enable row level security;

drop policy if exists config_lettura on config_reale;
create policy config_lettura on config_reale for select to authenticated
  using (exists (select 1 from accesso_reale a where a.uid = (select auth.uid())));
drop policy if exists config_interruttore on config_reale;
create policy config_interruttore on config_reale for update to authenticated
  using (exists (select 1 from accesso_reale a where a.uid = (select auth.uid())))
  with check (exists (select 1 from accesso_reale a where a.uid = (select auth.uid())));
drop policy if exists storico_lettura on config_reale_storico;
create policy storico_lettura on config_reale_storico for select to authenticated
  using (exists (select 1 from accesso_reale a where a.uid = (select auth.uid())));

-- gli autorizzati possono cambiare SOLO la colonna attivo
revoke all on config_reale, config_reale_storico from anon, authenticated;
grant select on config_reale, config_reale_storico to authenticated;
grant update (attivo) on config_reale to authenticated;

-- 2. notizie ----------------------------------------------------------------
create table if not exists notizie_giocate (
  id               bigserial primary key,
  creata_il        timestamptz not null default now(),
  braccio          text not null check (braccio in ('reale', 'btts')),
  riferimento      text not null,               -- chiave della giocata (giocate_reali / carta_btts)
  turno            text not null,
  lega             text not null,
  casa             text not null,
  trasferta        text not null,
  inizio           timestamptz not null,
  mercato          text not null,
  selezione        text not null,
  giudizio         text not null check (giudizio in ('contro', 'neutre', 'a_favore')),
  forza            smallint not null check (forza between 1 and 3),
  motivazione      text not null,
  fonti            text,
  versione_prompt  text not null,
  unique (braccio, riferimento),
  -- il giudizio vale solo se scritto prima del via
  check (creata_il < inizio)
);
create index if not exists idx_notizie_turno on notizie_giocate (turno);

create or replace function blocca_notizie()
returns trigger language plpgsql as $$
begin
  raise exception 'notizie_giocate: un giudizio registrato non si modifica e non si cancella.';
end;
$$;
drop trigger if exists trg_notizie on notizie_giocate;
create trigger trg_notizie before update or delete on notizie_giocate
  for each row execute function blocca_notizie();

alter table notizie_giocate enable row level security;
drop policy if exists notizie_lettura on notizie_giocate;
create policy notizie_lettura on notizie_giocate for select to authenticated
  using (exists (select 1 from accesso_reale a where a.uid = (select auth.uid())));
revoke all on notizie_giocate from anon, authenticated;
grant select on notizie_giocate to authenticated;

-- 3. pre-registrazioni ----------------------------------------------------------
insert into preregistrazioni (chiave, descrizione, valore)
select 'braccio_reale_automatico',
       'Integra braccio_reale (che resta valido in tutto il resto): turno automatico a '
       'orario fisso, senza conferma manuale. Pre-registrato il 10/10/2026, prima del '
       'primo turno automatico. Non va mai ricalcolato.',
       $json$
{
  "registrata_il": "2026-10-10",
  "integra": "braccio_reale",
  "momento": "venerdi' 19:00 (turno del weekend: partite fino a lunedi' 23:59) e martedi' 14:00 (turno infrasettimanale: partite fino a giovedi' 23:59), ora italiana, lanciati da un timer sul server senza conferma manuale",
  "primo_turno_automatico": "il timer gira dal 13/10/2026; il primo turno con partite atteso e' 2026-W42, venerdi' 16/10/2026",
  "dati_vecchi": "controllo strutturale (src/report/freschezza.py): rifiuto se un campionato ha piu' di una partita gia' giocata (fra 7 giorni e 8 ore prima) senza risultato nei dati del modello, o se un ingest non e' riuscito nelle ultime 30 ore. Nessuna deroga nel turno automatico",
  "interruttore": "config_reale.attivo, modificabile solo dagli account autorizzati dalla pagina privata. Spento = nessuna giocata con soldi veri; il Goal/No Goal su carta continua",
  "turno_saltato": "un turno non eseguito (server spento, rifiuto, interruttore spento) non si recupera piu' tardi: si salta e lo si annota",
  "w41": "il turno 2026-W41 (sabato 10/10 mattina, a mano) resta l'unico piazzato fuori orario: nelle analisi sul momento di piazzamento va trattato a parte",
  "invariato": "regole di selezione, puntate, tetti, prezzi, ordini LIMIT/LAPSE, criterio sul CLV e valutazioni a 150, 200 e 300 giocate"
}
       $json$::jsonb
where not exists (select 1 from preregistrazioni where chiave = 'braccio_reale_automatico');

insert into preregistrazioni (chiave, descrizione, valore)
select 'notizie_carta',
       'Esperimento su carta: giudizio sulle notizie di ogni giocata, scritto prima del via '
       'da un''attivita'' programmata di Claude con ricerca web. Non cambia nessuna giocata. '
       'Pre-registrato il 10/10/2026, prima del primo giudizio. Non va mai ricalcolato.',
       $json$
{
  "registrata_il": "2026-10-10",
  "domanda": "un giudizio sulle notizie (infortuni, squalifiche, formazioni probabili, turnover, motivazioni) letto prima del via separa le giocate con vantaggio vero da quelle in cui il mercato sa qualcosa che il modello non vede?",
  "effetto_sulle_giocate": "nessuno: il giudizio non cambia, non blocca e non ridimensiona nessuna giocata, ne' reale ne' su carta",
  "giocate": "tutte quelle di giocate_reali e di carta_btts dal turno 2026-W42",
  "chi_e_quando": "attivita' programmata di Claude con ricerca web, subito dopo il turno (venerdi' 19:40, martedi' 14:40). Istruzioni versionate in notizie_giocate.versione_prompt. Il database rifiuta un giudizio scritto dopo il via",
  "giudizio": "contro / neutre / a_favore rispetto alla giocata, forza da 1 a 3, motivazione e fonti",
  "valutazione": {
    "misura": "CLV medio delle giocate 'contro' meno CLV medio di 'neutre' e 'a_favore' insieme; per braccio e in totale",
    "quando": "a 150 giocate con giudizio e quota di chiusura",
    "diventa_filtro_se": "le 'contro' hanno CLV medio inferiore di almeno 2 punti percentuali e l'IC95 della differenza e' tutto sotto zero. Il filtro andra' pre-registrato a parte prima di usarlo",
    "si_abbandona_se": "a 300 giocate la differenza non e' distinguibile da zero"
  }
}
       $json$::jsonb
where not exists (select 1 from preregistrazioni where chiave = 'notizie_carta');

-- Verifica
select 'config_reale' as cosa, attivo::text as valore from config_reale
union all select 'pre-registrazioni', count(*)::text from preregistrazioni
          where chiave in ('braccio_reale_automatico', 'notizie_carta')
union all select 'notizie_giocate', count(*)::text from notizie_giocate;
