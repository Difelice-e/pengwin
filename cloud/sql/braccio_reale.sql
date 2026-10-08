-- =====================================================================
-- Pengwin — braccio REALE su betfair.it (8 ottobre 2026): Premier e Serie A
--
-- Da lanciare UNA volta nel SQL Editor di Supabase. Idempotente: rieseguirlo
-- non duplica nulla e non tocca le righe gia' scritte.
--
-- Cosa crea:
--   1. giocate_reali    — una riga per ogni giocata reale. Le colonne della
--                         DECISIONE non si modificano dopo l'inserimento;
--                         esecuzione, chiusura e contabilizzazione si'.
--   2. ordini_log       — richiesta e risposta grezze di ogni chiamata che
--                         scrive su Betfair (placeOrders, cancelOrders).
--   3. quote_snapshot   — fotografie periodiche delle quote betfair.it, per
--                         il CLV e per studiare QUANDO conviene piazzare.
--   4. la pre-registrazione del braccio reale in `preregistrazioni`.
--
-- Accesso: RLS attiva e NESSUNA policy. Saldo e giocate con soldi veri non
-- si leggono con la chiave pubblica (che sta in un repo pubblico): solo la
-- secret key del server li legge e li scrive.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. GIOCATE REALI
-- ---------------------------------------------------------------------
create table if not exists giocate_reali (
  id                 bigserial primary key,
  chiave             text unique not null,      -- turno|market_id|selection_id
  turno              text not null,             -- es. '2026-W41'
  creata_il          timestamptz not null default now(),
  versione_modello   text not null,

  -- partita (nomi football-data, come nel resto del sistema)
  lega               text not null,
  data_partita       date not null,
  ora                time,                      -- ora italiana del calcio d'inizio
  inizio             timestamptz not null,
  casa               text not null,
  trasferta          text not null,
  evento_bf          text,                      -- nome evento su Betfair
  mercato            text not null check (mercato in ('1X2', 'OU25')),
  selezione          text not null check (selezione in ('1', 'X', '2', 'over', 'under')),
  market_id          text not null,
  selection_id       bigint not null,

  -- DECISIONE (immutabile)
  prob_modello       numeric not null check (prob_modello > 0 and prob_modello < 1),
  quota_riferimento  numeric not null,          -- miglior back letto prima dell'invio
  size_disponibile   numeric,                   -- importo offerto a quella quota
  quota_netta        numeric not null,          -- al netto della commissione
  edge_netto         numeric not null,
  saldo_al_turno     numeric not null,          -- disponibile + esposizione, da Betfair
  stake_richiesto    numeric not null check (stake_richiesto >= 2),

  -- ESECUZIONE
  stato              text not null default 'da_piazzare'
                     check (stato in ('da_piazzare', 'piazzata', 'parziale', 'abbinata',
                                      'non_abbinata', 'respinta', 'incerta', 'annullata',
                                      'chiusa', 'void')),
  customer_order_ref text unique,
  bet_id             text unique,
  piazzata_il        timestamptz,
  quota_abbinata     numeric,
  stake_abbinato     numeric,
  errore             text,

  -- CHIUSURA (CLV)
  quota_chiusura_it  numeric,                   -- miglior back betfair.it a ridosso del via
  chiusura_il        timestamptz,
  quota_chiusura_bfe numeric,                   -- BFEC* di football-data, di confronto
  clv                numeric,

  -- CONTABILIZZAZIONE (fa fede Betfair: listClearedOrders)
  esito              text check (esito in ('vinta', 'persa', 'void')),
  profitto_lordo     numeric,
  commissione        numeric,
  profitto_netto     numeric,
  contabilizzata_il  timestamptz,
  note               text
);
create index if not exists idx_reali_turno  on giocate_reali (turno);
create index if not exists idx_reali_stato  on giocate_reali (stato);
create index if not exists idx_reali_inizio on giocate_reali (inizio);

-- La decisione si scrive prima dell'ordine e non si tocca piu': e' lo stesso
-- principio delle previsioni immutabili (00-specifica-sistema.md par.3).
-- Nessun DELETE: una giocata non partita resta, con stato 'annullata'.
create or replace function blocca_decisione_reale()
returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'giocate_reali: DELETE non consentito (stato ''annullata'' al suo posto).';
  end if;
  if (new.chiave, new.turno, new.creata_il, new.versione_modello, new.lega,
      new.data_partita, new.inizio, new.casa, new.trasferta, new.mercato,
      new.selezione, new.market_id, new.selection_id, new.prob_modello,
      new.quota_riferimento, new.quota_netta, new.edge_netto,
      new.saldo_al_turno, new.stake_richiesto)
     is distinct from
     (old.chiave, old.turno, old.creata_il, old.versione_modello, old.lega,
      old.data_partita, old.inizio, old.casa, old.trasferta, old.mercato,
      old.selezione, old.market_id, old.selection_id, old.prob_modello,
      old.quota_riferimento, old.quota_netta, old.edge_netto,
      old.saldo_al_turno, old.stake_richiesto) then
    raise exception 'giocate_reali: le colonne della decisione sono immutabili.';
  end if;
  return new;
end;
$$;

drop trigger if exists trg_decisione_reale on giocate_reali;
create trigger trg_decisione_reale
  before update or delete on giocate_reali
  for each row execute function blocca_decisione_reale();

-- ---------------------------------------------------------------------
-- 2. LOG DEGLI ORDINI
-- ---------------------------------------------------------------------
create table if not exists ordini_log (
  id         bigserial primary key,
  il         timestamptz not null default now(),
  operazione text not null,                     -- placeOrders | cancelOrders | ...
  turno      text,
  market_id  text,
  richiesta  jsonb,
  risposta   jsonb,
  esito      text
);
create index if not exists idx_ordini_log_il on ordini_log (il);

-- ---------------------------------------------------------------------
-- 3. FOTOGRAFIE DELLE QUOTE
-- ---------------------------------------------------------------------
create table if not exists quote_snapshot (
  id           bigserial primary key,
  il           timestamptz not null default now(),
  motivo       text not null default 'periodica',  -- periodica | chiusura
  market_id    text not null,
  mercato      text not null,                       -- 1X2 | OU25
  lega         text,
  evento       text,
  inizio       timestamptz,
  selection_id bigint not null,
  runner       text,
  back         numeric,
  back_size    numeric,
  lay          numeric,
  lay_size     numeric,
  stato        text,
  inplay       boolean,
  ritardato    boolean                              -- prezzi della chiave Delayed
);
create index if not exists idx_snapshot_market on quote_snapshot (market_id, il);
create index if not exists idx_snapshot_inizio on quote_snapshot (inizio);

-- ---------------------------------------------------------------------
-- Accesso: solo la secret key (che scavalca la RLS). Nessuna policy.
-- ---------------------------------------------------------------------
alter table giocate_reali  enable row level security;
alter table ordini_log     enable row level security;
alter table quote_snapshot enable row level security;

-- ---------------------------------------------------------------------
-- 4. PRE-REGISTRAZIONE DEL BRACCIO REALE
--    Va scritta PRIMA della prima giocata: il turno reale rifiuta di
--    piazzare se questa riga non c'e'. Rileggerla prima di lanciare lo
--    script: dopo, `preregistrazioni` non si modifica.
-- ---------------------------------------------------------------------
insert into preregistrazioni (chiave, descrizione, valore)
select 'braccio_reale',
       'Braccio reale su betfair.it: regole, criterio di valutazione sul CLV e attese '
       'nell''ipotesi che il mercato abbia ragione. Pre-registrato l''8/10/2026, prima '
       'della prima giocata. Non va mai ricalcolato.',
       $json$
{
  "registrata_il": "2026-10-08",
  "versione_modello": "blend35-65_xi0.0018_w3y",
  "campionati": ["E0", "I1"],
  "campionati_motivazione": "scelta dell'utente dell'8/10/2026, dopo i primi 4 turni su carta (Premier ROI +45%, Serie A +30%, altri tre negativi). E' una selezione a posteriori: nella nota 17 il migliore dei 10 segmenti aveva p = 0,165 dopo la correzione per confronti multipli. Quei turni NON contano come evidenza: l'ipotesi 'Premier + Serie A' si valuta solo sulle giocate reali da qui in avanti",
  "mercati": ["1X2", "OU25"],
  "prezzo": "miglior back su betfair.it letto subito prima dell'invio; ordine LIMIT a quella quota, persistenza LAPSE (cio' che non e' abbinato decade al fischio d'inizio); mai in-play",
  "commissione": 0.045,
  "selezione": "edge calcolato sulla quota NETTA della commissione, banda 2%-10%; solo quote fra 1,20 e 15; minimo 8 partite per squadra nella finestra di stima",
  "staking": {
    "kelly": 0.25,
    "tetto_per_giocata": 0.01,
    "tetto_assoluto_eur": 10,
    "esposizione_per_turno": 0.20,
    "max_giocate_per_turno": 25,
    "puntata_minima_eur": 2.0,
    "passo_eur": 0.5,
    "arrotondamento": "per difetto",
    "ordinamento_se_troppe": "edge decrescente"
  },
  "bankroll": {
    "iniziale_indicativo_eur": 400,
    "definitivo": false,
    "base_delle_puntate": "saldo Betfair (disponibile + esposizione) letto all'inizio di ogni turno: le puntate crescono se il saldo cresce e calano se cala",
    "versamenti": "ammessi durante la stagione; non entrano nel P&L, calcolato dalle giocate chiuse",
    "stop_automatico": null,
    "controllo": "manuale dell'utente, che puo' fermare l'esperimento in qualunque momento"
  },
  "turni": {
    "weekend": true,
    "infrasettimanali": "no finche' il piazzamento richiede conferma manuale; si attivano col piazzamento automatico, annotandolo",
    "momento": "all'invio confermato dall'utente, il venerdi' sera; una regola diversa sul momento va decisa coi dati di quote_snapshot e pre-registrata a parte"
  },
  "clv": {
    "definizione": "quota_abbinata / quota_chiusura_it - 1",
    "chiusura_it": "miglior back su betfair.it nell'ultima fotografia entro 10 minuti dal via; in mancanza, BFEC* di football-data",
    "valutazioni_a_giocate": [150, 200],
    "si_prosegue_se": "CLV medio >= +3% con estremo inferiore dell'IC95 > 0",
    "ci_si_ferma_se": "a 200 giocate il CLV medio e' <= 0",
    "casi_intermedi": "si prosegue fino a 300 giocate, poi si decide"
  },
  "attese_se_ha_ragione_il_mercato": {
    "valore_atteso_per_euro_giocato": -0.018,
    "fonte": "nota 17 par.6: valore atteso Betfair netto Premier -2,95% (28 giocate) e Serie A -0,62% (29), media pesata",
    "giocate_per_turno_attese": "circa 10-14, contro le 25 dei cinque campionati",
    "nota": "la simulazione del saldo a fine stagione della nota 17 par.7 era sui cinque campionati e non e' stata ricalcolata: con meno giocate la dispersione del saldo e' minore"
  },
  "pnl": "non e' un criterio: servono circa 4.000 giocate per distinguere un vantaggio del 2% da zero"
}
       $json$::jsonb
where not exists (select 1 from preregistrazioni where chiave = 'braccio_reale');

-- Verifica
select 'giocate_reali' as tabella, count(*) from giocate_reali
union all select 'ordini_log', count(*) from ordini_log
union all select 'quote_snapshot', count(*) from quote_snapshot
union all select 'preregistrazione braccio_reale', count(*) from preregistrazioni
          where chiave = 'braccio_reale';
