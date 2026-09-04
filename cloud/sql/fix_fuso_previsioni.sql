-- Correzione fuso orario delle previsioni importate da ledger.csv
-- I timestamp erano ora locale Europe/Rome (UTC+2) scritti come UTC: 2 ore di scarto.
begin;
alter table previsioni disable trigger trg_previsioni_immutabili;
update previsioni
   set creata_il = creata_il - interval '2 hours'
 where note like 'import ledger.csv%';
alter table previsioni enable trigger trg_previsioni_immutabili;
insert into log_esecuzioni (job, esito, righe, dettaglio)
values ('correzione_fuso_previsioni', 'ok',
        (select count(*) from previsioni where note like 'import ledger.csv%'),
        'Timestamp import ledger.csv riportati da UTC errato a Europe/Rome (-2h).');
commit;
select min(creata_il) as prima, max(creata_il) as ultima, count(*) as righe from previsioni;
