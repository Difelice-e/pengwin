"""
Verifica delle parti pure di src/ingest/betfair.py: commissione,
arrotondamento degli stake ai vincoli di betfair.it, aggancio del catalogo
Betfair ai fixtures football-data.

Nessuna credenziale e nessuna rete: catalogo e quote sono sintetici. Serve a
poter cambiare `aggancia()` senza scoprire in produzione che una partita si e'
agganciata all'evento sbagliato — il rischio che ALIAS_BETFAIR esiste per
evitare.

    python tests/betfair.py        # dalla cartella cloud/
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingest import betfair as bf     # noqa: E402
from src.ingest import squadre           # noqa: E402

esiti = []


def check(nome, atteso, ottenuto):
    ok = atteso == ottenuto
    esiti.append(ok)
    print(f"{'OK  ' if ok else 'FAIL'} {nome}: atteso {atteso!r}, ottenuto {ottenuto!r}")


# --- quota netta commissione -------------------------------------------------
check("quota_netta(2.10)", 2.0505, round(bf.quota_netta(2.10), 4))
check("quota_netta(1.00) resta 1", 1.0, round(bf.quota_netta(1.0), 4))
check("quota_netta(3.00)", 2.91, round(bf.quota_netta(3.0), 4))

# --- arrotondamento stake ai 50 cent ----------------------------------------
check("stake 3.47 -> 3.00", 3.0, bf.arrotonda_stake(3.47))
check("stake 4.50 esatto", 4.5, bf.arrotonda_stake(4.50))
check("stake 2.49 -> 2.00", 2.0, bf.arrotonda_stake(2.49))
check("stake 1.99 -> 0 (sotto minimo)", 0.0, bf.arrotonda_stake(1.99))
check("stake 2.00 minimo ammesso", 2.0, bf.arrotonda_stake(2.00))
check("stake 12.999 -> 12.50", 12.5, bf.arrotonda_stake(12.999))

# --- batch entro i 200 punti -------------------------------------------------
check("mercati per richiesta", 40, bf.MERCATI_PER_RICHIESTA)

# --- aggancio catalogo/quote ai fixtures ------------------------------------
bf.COMPETIZIONI = {"81": "I1"}
squadre.ALIAS_BETFAIR.update({"Milan": "AC Milan"})

cat = [
    {"marketId": "1.100", "marketName": "Match Odds",
     "competition": {"id": "81"},
     "event": {"name": "AC Milan v Inter", "openDate": "2026-09-13T18:45:00.000Z"},
     "runners": [{"selectionId": 1, "runnerName": "AC Milan"},
                 {"selectionId": 2, "runnerName": "Inter"},
                 {"selectionId": 3, "runnerName": "The Draw"}]},
    {"marketId": "1.200", "marketName": "Over/Under 2.5 Goals",
     "competition": {"id": "81"},
     "event": {"name": "AC Milan v Inter", "openDate": "2026-09-13T18:45:00.000Z"},
     "runners": [{"selectionId": 10, "runnerName": "Over 2.5 Goals"},
                 {"selectionId": 11, "runnerName": "Under 2.5 Goals"}]},
    # evento di un'altra competizione: deve essere ignorato
    {"marketId": "1.300", "marketName": "Match Odds",
     "competition": {"id": "999"},
     "event": {"name": "Foo v Bar", "openDate": "2026-09-13T18:45:00.000Z"},
     "runners": [{"selectionId": 20, "runnerName": "Foo"}]},
]


def runner(sid, prezzo, size, stato="ACTIVE"):
    return {"selectionId": sid, "status": stato,
            "ex": {"availableToBack": [{"price": prezzo, "size": size}]}}


book = {
    "1.100": {"marketId": "1.100", "runners": [
        runner(1, 2.42, 310.0), runner(2, 3.05, 120.0), runner(3, 3.60, 88.0)]},
    "1.200": {"marketId": "1.200", "runners": [
        runner(10, 1.94, 540.0), runner(11, 2.06, 410.0)]},
}

fx = [{"lega": "I1", "data": "2026-09-13", "casa": "Milan", "trasferta": "Inter"},
      {"lega": "I1", "data": "2026-09-13", "casa": "Roma", "trasferta": "Lazio"}]

righe, orfani, liberi = bf.aggancia(cat, book, fx)

check("righe agganciate", 1, len(righe))
check("orfani", 1, len(orfani))
check("orfano e' Roma-Lazio", ("Roma", "Lazio"),
      (orfani[0]["casa"], orfani[0]["trasferta"]))

r = righe[0]
check("q_bf_1 (casa)", 2.42, r["q_bf_1"])
check("q_bf_2 (trasferta)", 3.05, r["q_bf_2"])
check("q_bf_x (pareggio)", 3.60, r["q_bf_x"])
check("q_bf_over25", 1.94, r["q_bf_over25"])
check("q_bf_under25", 2.06, r["q_bf_under25"])
check("market id 1x2", "1.100", r["bf_market_1x2"])
check("market id ou25", "1.200", r["bf_market_ou25"])
check("size in bf_raw", 310.0, r["bf_raw"]["q_bf_1"]["size"])
check("nomi football-data conservati", ("Milan", "Inter"), (r["casa"], r["trasferta"]))
check("competizione estranea ignorata", False,
      any("Foo" in n for n in liberi))

# --- selezione sospesa: quota None, non zero --------------------------------
book_sosp = {"1.100": {"marketId": "1.100", "runners": [
    runner(1, 2.42, 310.0), {"selectionId": 2, "status": "REMOVED", "ex": {}},
    runner(3, 3.60, 88.0)]}}
righe2, _, _ = bf.aggancia([cat[0]], book_sosp, fx[:1])
check("selezione rimossa -> None", None, righe2[0]["q_bf_2"])

# --- nome non mappato: nessun aggancio silenziosamente sbagliato ------------
squadre.ALIAS_BETFAIR.pop("Milan")
righe3, orfani3, liberi3 = bf.aggancia(cat, book, fx[:1])
check("senza alias non aggancia", 0, len(righe3))
check("senza alias diventa orfano", 1, len(orfani3))
check("nome Betfair resta libero", True, "AC Milan v Inter" in liberi3)

print()
print(f"{sum(esiti)}/{len(esiti)} verifiche passate")
sys.exit(0 if all(esiti) else 1)
