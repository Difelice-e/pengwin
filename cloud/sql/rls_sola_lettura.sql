-- =========================================================
-- Pengwin — chiusura dei permessi anon in sola lettura
--
-- Perche': la pagina live incorpora la publishable key nel sorgente, quindi
-- quella chiave diventa pubblica. Con la policy attuale (`pengwin_anon_all`,
-- `for all ... using(true) with check(true)`) chiunque potrebbe riscrivere
-- `giocate` — stake, esiti, quote di chiusura, CLV. Cioe' esattamente i numeri
-- su cui l'esperimento viene giudicato.
--
-- Dopo questo script:
--   anon / authenticated  -> SELECT e basta
--   service_role          -> tutto (la RLS non si applica), e' la pipeline
--
-- La pipeline non richiede modifiche al codice: `src/db/client.py` accetta gia'
-- una secret key in SUPABASE_KEY. Va cambiata solo la variabile nelle due
-- attivita' pianificate.
--
-- Eseguire nel SQL editor di Supabase.
-- =========================================================

do $$
declare
  t text;
  tabelle text[] := array[
    'squadre', 'partite', 'xg_partite', 'fixtures',
    'previsioni', 'giocate', 'log_esecuzioni', 'preregistrazioni'
  ];
begin
  foreach t in array tabelle loop
    -- salta le tabelle che non esistono, cosi' lo script resta rilanciabile
    if to_regclass('public.' || t) is null then
      raise notice 'tabella % assente, saltata', t;
      continue;
    end if;

    execute format('alter table public.%I enable row level security', t);

    -- via la policy permissiva di partenza
    execute format('drop policy if exists pengwin_anon_all on public.%I', t);

    -- sola lettura, e nient'altro: nessuna policy di insert/update/delete
    -- significa che quelle operazioni sono negate per anon.
    execute format('drop policy if exists pengwin_anon_read on public.%I', t);
    execute format(
      'create policy pengwin_anon_read on public.%I '
      'for select to anon, authenticated using (true)', t);
  end loop;
end $$;

-- Le viste vanno valutate coi diritti di chi interroga, altrimenti scavalcano
-- la RLS delle tabelle sottostanti.
do $$
declare v text;
begin
  for v in
    select table_name from information_schema.views where table_schema = 'public'
  loop
    execute format('alter view public.%I set (security_invoker = true)', v);
  end loop;
end $$;

-- =========================================================
-- Verifica: elenca le policy rimaste. Devono essere tutte e sole
-- 'pengwin_anon_read' con cmd = SELECT.
-- =========================================================
select tablename, policyname, cmd, roles
from pg_policies
where schemaname = 'public'
order by tablename, policyname;
