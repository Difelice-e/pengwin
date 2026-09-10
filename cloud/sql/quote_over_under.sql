-- Le sei colonne Over/Under 2.5 che il parser produceva e lo schema non aveva.
--
-- `src/ingest/football_data.py` mappa da sempre sei colonne CSV su nomi che in
-- `schema.sql` non esistono. `_quote()` aggiunge una chiave solo se la cella
-- del CSV non e' vuota, quindi finche' la fonte lascia quelle celle vuote il
-- payload non le contiene e nessuno se ne accorge. Due conseguenze:
--
--   1. in atto: `dataset.fixtures()` rinomina q_ap_max_over25 -> MaxO25, che
--      quindi non entra mai nel frame; `predict.py` legge None e salta la
--      riga. «Over 2.5» e «Under 2.5» sono in MERCATI ma non sono mai stati
--      giocabili: il sistema ha giocato solo 1X2, per un buco nello schema e
--      non per una scelta;
--   2. latente: il giorno che la fonte pubblica un valore in una di quelle
--      celle, la chiave si forma e PostgREST rifiuta l'INTERO batch con
--      PGRST204. Non degrada, si rompe — e siccome MaxC>2.5 sta nella mappa
--      delle chiusure, si romperebbe anche l'ingest di `partite`.
--
-- Le stesse sei colonne servono su entrambe le tabelle: `_quote()` applica
-- tutti e tre i dizionari sia in `stagione()` (-> partite) sia in
-- `fixtures()` (-> fixtures).
--
-- Additivo e idempotente. ATTENZIONE: applicandolo, `MaxO25`/`MaxU25` si
-- popolano e `predict.py` inizia a selezionare Over/Under al turno successivo.
-- E' l'effetto voluto, non un incidente.

-- apertura: massima e media fra i bookmaker
alter table partite  add column if not exists q_ap_max_over25   numeric;
alter table partite  add column if not exists q_ap_max_under25  numeric;
alter table partite  add column if not exists q_ap_avg_over25   numeric;
alter table partite  add column if not exists q_ap_avg_under25  numeric;
alter table fixtures add column if not exists q_ap_max_over25   numeric;
alter table fixtures add column if not exists q_ap_max_under25  numeric;
alter table fixtures add column if not exists q_ap_avg_over25   numeric;
alter table fixtures add column if not exists q_ap_avg_under25  numeric;

-- chiusura: massima fra i bookmaker (MaxC>2.5 / MaxC<2.5)
alter table partite  add column if not exists q_max_over25      numeric;
alter table partite  add column if not exists q_max_under25     numeric;
alter table fixtures add column if not exists q_max_over25      numeric;
alter table fixtures add column if not exists q_max_under25     numeric;

comment on column partite.q_ap_max_over25 is
  'Over 2.5 in apertura, massima fra i bookmaker (colonna Max>2.5)';
comment on column partite.q_max_over25 is
  'Over 2.5 in chiusura, massima fra i bookmaker (colonna MaxC>2.5)';
