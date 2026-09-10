"""
Verifica delle parti pure di src/ingest/betfair.py: commissione,
arrotondamento degli stake ai vincoli di betfair.it, costruzione dei fixtures
dal catalogo Betfair.

Nessuna credenziale e nessuna rete: catalogo e quote sono sintetici. Serve a
poter cambiare `fixtures_da_betfair()` senza scoprire in produzione che una
partita e' stata inserita col nome sbagliato — cioe' duplicata.

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

# --- la tabella vera --------------------------------------------------------
# Paris SG e Paris FC: due squadre della stessa Ligue 1 con nomi vicini, di cui
# una sola va tradotta. Se qualcuno le allinea entrambe, le quote finiscono
# sulla partita sbagliata senza dare nessun errore.
check("Paris SG -> Paris St-G", "Paris St-G", squadre.a_betfair("Paris SG"))
check("Paris FC resta Paris FC", "Paris FC", squadre.a_betfair("Paris FC"))
check("Paris St-G -> Paris SG (inverso)", "Paris SG", squadre.da_betfair("Paris St-G"))

# L'inversione dei nomi regge solo se la tabella e' iniettiva: due squadre
# sullo stesso nome Betfair ne farebbero perdere una in silenzio.
check("ALIAS_BETFAIR iniettivo", [], squadre.verifica_inverso())

# --- costruzione dei fixtures dal catalogo ----------------------------------
# Usa la mappatura vera («AC Milan» -> «Milan») invece di mutare la tabella:
# `_INVERSO` si calcola all'import, quindi una mutazione non avrebbe effetto.
bf.COMPETIZIONI = {"81": "I1"}

APERTURA = "2026-09-13T18:45:00.000Z"    # 20:45 a Roma


def mercato(mid, nome, evento, runners, comp="81", apertura=APERTURA):
    return {"marketId": mid, "marketName": nome, "competition": {"id": comp},
            "event": {"name": evento, "openDate": apertura},
            "runners": [{"selectionId": s, "runnerName": n} for s, n in runners]}


cat = [
    mercato("1.100", "Match Odds", "AC Milan v Inter",
            [(1, "AC Milan"), (2, "Inter"), (3, "The Draw")]),
    mercato("1.200", "Over/Under 2.5 Goals", "AC Milan v Inter",
            [(10, "Over 2.5 Goals"), (11, "Under 2.5 Goals")]),
    # squadra che football-data non conosce: va scartata, non inserita
    mercato("1.400", "Match Odds", "Squadra Ignota v Inter",
            [(30, "Squadra Ignota"), (31, "Inter"), (32, "The Draw")]),
    # competizione fuori da COMPETIZIONI: ignorata
    mercato("1.300", "Match Odds", "Foo v Bar", [(20, "Foo")], comp="999"),
]


def runner(sid, prezzo, size, stato="ACTIVE"):
    return {"selectionId": sid, "status": stato,
            "ex": {"availableToBack": [{"price": prezzo, "size": size}]}}


book = {
    "1.100": {"marketId": "1.100", "runners": [
        runner(1, 2.42, 310.0), runner(2, 3.05, 120.0), runner(3, 3.60, 88.0)]},
    "1.200": {"marketId": "1.200", "runners": [
        runner(10, 1.94, 540.0), runner(11, 2.06, 410.0)]},
    "1.400": {"marketId": "1.400", "runners": [
        runner(30, 2.00, 50.0), runner(31, 4.00, 50.0), runner(32, 3.50, 50.0)]},
}

valide = {"I1": {"Milan", "Inter"}}
righe, scartati = bf.fixtures_da_betfair(cat, book, valide)

check("fixtures costruiti", 1, len(righe))
check("partite scartate", 1, len(scartati))
check("lo scarto e' la squadra ignota", True, "Squadra Ignota" in scartati[0])

r = righe[0]
check("nome casa riportato a football-data", "Milan", r["casa"])
check("nome trasferta invariato", "Inter", r["trasferta"])
check("lega", "I1", r["lega"])
check("data locale", "2026-09-13", r["data"])
check("ora locale (20:45 a Roma)", "20:45:00", r["ora"])
check("q_bf_1 (casa)", 2.42, r["q_bf_1"])
check("q_bf_2 (trasferta)", 3.05, r["q_bf_2"])
check("q_bf_x (pareggio)", 3.60, r["q_bf_x"])
check("q_bf_over25", 1.94, r["q_bf_over25"])
check("q_bf_under25", 2.06, r["q_bf_under25"])
check("market id 1x2", "1.100", r["bf_market_1x2"])
check("market id ou25", "1.200", r["bf_market_ou25"])
check("size in bf_raw", 310.0, r["bf_raw"]["q_bf_1"]["size"])
check("competizione estranea ignorata", False,
      any("Foo" in s for s in scartati))

# --- selezione sospesa: quota None, non zero --------------------------------
book_sosp = {"1.100": {"marketId": "1.100", "runners": [
    runner(1, 2.42, 310.0), {"selectionId": 2, "status": "REMOVED", "ex": {}},
    runner(3, 3.60, 88.0)]}}
righe2, _ = bf.fixtures_da_betfair([cat[0]], book_sosp, valide)
check("selezione rimossa -> None", None, righe2[0]["q_bf_2"])

# --- nessuna squadra valida: niente viene inserito --------------------------
righe3, scartati3 = bf.fixtures_da_betfair(cat, book, {})
check("senza elenco squadre non inserisce nulla", 0, len(righe3))
check("e segnala tutte le partite", 2, len(scartati3))

print()
print(f"{sum(esiti)}/{len(esiti)} verifiche passate")
sys.exit(0 if all(esiti) else 1)
