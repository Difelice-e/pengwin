-- =====================================================================
-- Pengwin — braccio SU CARTA Goal/No Goal (BTTS) sui prezzi betfair.it
-- (8 ottobre 2026)
--
-- Da lanciare UNA volta nel SQL Editor di Supabase. Idempotente.
--
-- Nessun soldo: le giocate sono registrate e contabilizzate su un bankroll
-- virtuale. Serve a rispondere a una domanda prima di rischiare denaro: il
-- modello ha valore sul Goal/No Goal? Il modello calcola la probabilita' ma
-- non e' MAI stato verificato su questo mercato (nessun backtest: football-data
-- non pubblica quote storiche BTTS).
--
-- Separato dall'esperimento su carta 1X2/Over-Under (`giocate`): mescolarli
-- renderebbe incomparabili ROI, CLV e la banda pre-registrata di quello.
--
-- Accesso: lettura pubblica (e' carta, la dashboard la mostra), scrittura
-- solo con la secret key del server.
-- =====================================================================

create table if not exists carta_btts (
  id                 bigserial primary key,
  chiave             text unique not null,      -- turno|market_id|selection_id
  turno              text not null,
  creata_il          timestamptz not null default now(),
  versione_modello   text not null,

  lega               text not null,
  data_partita       date not null,
  inizio             timestamptz not null,
  casa               text not null,
  trasferta          text not null,
  evento_bf          text,
  market_id          text not null,
  selection_id       bigint not null,
  selezione          text not null check (selezione in ('gg', 'ng')),

  -- DECISIONE (immutabile)
  prob_modello       numeric not null check (prob_modello > 0 and prob_modello < 1),
  quota              numeric not null,          -- miglior back betfair.it alla decisione
  size_disponibile   numeric,
  quota_netta        numeric not null,
  edge_netto         numeric not null,
  bankroll_virtuale  numeric not null,
  stake              numeric not null check (stake >= 2),

  -- CHIUSURA
  quota_chiusura     numeric,                   -- miglior back entro 12 minuti dal via
  chiusura_il        timestamptz,
  chiusura_fonte     text check (chiusura_fonte in ('diretta', 'fotografia')),
  clv                numeric,                   -- quota / quota_chiusura - 1

  -- CONTABILIZZAZIONE (risultato da `partite`)
  gol_casa           int,
  gol_trasferta      int,
  esito              text check (esito in ('vinta', 'persa', 'void')),
  profitto_netto     numeric,                   -- commissione 4,5% sulle vincite
  contabilizzata_il  timestamptz
);
create index if not exists idx_btts_turno  on carta_btts (turno);
create index if not exists idx_btts_inizio on carta_btts (inizio);

create or replace function blocca_decisione_btts()
returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'carta_btts: DELETE non consentito.';
  end if;
  if (new.chiave, new.turno, new.creata_il, new.versione_modello, new.lega,
      new.data_partita, new.inizio, new.casa, new.trasferta, new.market_id,
      new.selection_id, new.selezione, new.prob_modello, new.quota,
      new.quota_netta, new.edge_netto, new.bankroll_virtuale, new.stake)
     is distinct from
     (old.chiave, old.turno, old.creata_il, old.versione_modello, old.lega,
      old.data_partita, old.inizio, old.casa, old.trasferta, old.market_id,
      old.selection_id, old.selezione, old.prob_modello, old.quota,
      old.quota_netta, old.edge_netto, old.bankroll_virtuale, old.stake) then
    raise exception 'carta_btts: le colonne della decisione sono immutabili.';
  end if;
  return new;
end;
$$;

drop trigger if exists trg_decisione_btts on carta_btts;
create trigger trg_decisione_btts
  before update or delete on carta_btts
  for each row execute function blocca_decisione_btts();

alter table carta_btts enable row level security;
drop policy if exists pengwin_anon_read on carta_btts;
create policy pengwin_anon_read on carta_btts
  for select to anon, authenticated using (true);

-- ---------------------------------------------------------------------
-- PRE-REGISTRAZIONE — da rileggere prima di lanciare: dopo non si cambia.
-- ---------------------------------------------------------------------
insert into preregistrazioni (chiave, descrizione, valore)
select 'carta_btts',
       'Braccio su carta Goal/No Goal ai prezzi betfair.it: regole e criterio di '
       'valutazione. Pre-registrato l''8/10/2026, prima della prima registrazione. '
       'Nessun denaro. Non va mai ricalcolato.',
       $json$
{
  "registrata_il": "2026-10-08",
  "versione_modello": "blend35-65_xi0.0018_w3y",
  "domanda": "il modello ha valore sul Goal/No Goal rispetto al prezzo di betfair.it?",
  "campionati": ["E0", "I1", "SP1", "D1", "F1"],
  "mercato": "BOTH_TEAMS_TO_SCORE (Goal = gg, No Goal = ng)",
  "probabilita": "P(gg) = probabilita' del modello che entrambe segnino (markets()['BTTS']); P(ng) = 1 - P(gg)",
  "prezzo": "miglior back su betfair.it letto al momento della registrazione, subito dopo il turno reale",
  "commissione": 0.045,
  "selezione": "stesse regole del braccio reale: edge sulla quota netta fra 2% e 10%, quote fra 1,20 e 15, minimo 8 partite per squadra, Kelly 1/4, tetto 1% per giocata, esposizione 20%, massimo 25 giocate, edge decrescente",
  "bankroll_virtuale_eur": 400,
  "turni": "gli stessi del braccio reale, weekend e infrasettimanali",
  "clv": {
    "definizione": "quota / quota_chiusura - 1",
    "chiusura": "miglior back su betfair.it nell'ultima lettura entro 12 minuti dal via; in mancanza, l'ultima fotografia periodica prima del via (annotata)",
    "valutazioni_a_giocate": [150, 200],
    "si_porta_ai_soldi_veri_se": "CLV medio >= +3% con estremo inferiore dell'IC95 > 0, e solo con una nuova pre-registrazione",
    "si_abbandona_se": "a 200 giocate il CLV medio e' <= 0"
  },
  "pnl": "virtuale, informativo: non e' un criterio"
}
       $json$::jsonb
where not exists (select 1 from preregistrazioni where chiave = 'carta_btts');

select 'carta_btts' as tabella, count(*) from carta_btts
union all select 'preregistrazione carta_btts', count(*) from preregistrazioni
          where chiave = 'carta_btts';
