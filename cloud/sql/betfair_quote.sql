-- Quote Betfair Exchange sui fixtures, accanto a quelle football-data.
--
-- Additivo e idempotente: nessuna colonna esistente viene toccata, quindi la
-- selezione in corso (che usa q_ap_max_*) continua a leggere esattamente i
-- dati di prima. Serve a far girare le due fonti in parallelo per qualche
-- turno e misurare di quanto si restringe l'edge prima di cambiare criterio.
--
-- q_bf_* = miglior quota di BACK disponibile al momento della lettura, gia'
-- virtualizzata come la mostra il sito. NON e' una chiusura e non va confusa
-- con q_bfe_* (chiusura Betfair pubblicata da football-data): stessa borsa,
-- momenti diversi. Nessun fallback fra le due.

alter table fixtures add column if not exists bf_market_1x2  text;
alter table fixtures add column if not exists bf_market_ou25 text;

alter table fixtures add column if not exists q_bf_1       numeric;
alter table fixtures add column if not exists q_bf_x       numeric;
alter table fixtures add column if not exists q_bf_2       numeric;
alter table fixtures add column if not exists q_bf_over25  numeric;
alter table fixtures add column if not exists q_bf_under25 numeric;

-- snapshot completo del best-offers per selezione (prezzo + size disponibile).
-- In jsonb e non in dieci colonne: la liquidita' serve a sapere se lo stake
-- e' abbinabile, non entra in nessuna query aggregata.
alter table fixtures add column if not exists bf_raw jsonb;

alter table fixtures add column if not exists bf_letto_il timestamptz;

comment on column fixtures.q_bf_1 is
  'Miglior back Betfair al momento della lettura (bf_letto_il), lordo commissione';
