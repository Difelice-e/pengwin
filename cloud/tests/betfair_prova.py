"""
Verifica di cloud/server/betfair_prova.py senza rete e senza credenziali.

Betfair e' simulato da un finto `rpc`: si controlla che la prova d'ordine
scelga il mercato giusto, si rifiuti quando deve, e che in ogni ramo dopo
l'invio (successo, rifiuto, rete persa, annullamento fallito) finisca con il
codice di uscita corretto. Serve perche' quei rami, dal vivo, si vedono una
volta sola e con soldi veri.

    python tests/betfair_prova.py        # dalla cartella cloud/
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import betfair_prova as bp               # noqa: E402

esiti = []


def check(nome, atteso, ottenuto):
    ok = atteso == ottenuto
    esiti.append(ok)
    print(f"{'OK  ' if ok else 'FAIL'} {nome}: atteso {atteso!r}, ottenuto {ottenuto!r}")


ADESSO = datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc)


def mercato(mid, ore, prezzi, stato="OPEN", inplay=False, extra_back=None, comp="81"):
    """Catalogo + libro sintetici. prezzi = miglior back di casa, pareggio, trasferta."""
    inizio = (ADESSO + timedelta(hours=ore)).isoformat().replace("+00:00", "Z")
    runners = [{"selectionId": 100 + i, "runnerName": n, "sortPriority": i + 1}
               for i, n in enumerate(("Casa", "The Draw", "Trasferta"))]
    cat = {"marketId": mid, "marketName": "Match Odds", "marketStartTime": inizio,
           "event": {"name": f"Casa v Trasferta {mid}"}, "competition": {"id": comp},
           "runners": runners}
    libro = {"marketId": mid, "status": stato, "inplay": inplay, "runners": []}
    for r, p in zip(runners, prezzi):
        back = [] if p is None else [{"price": p, "size": 50.0}]
        if extra_back and r["selectionId"] == extra_back[0]:
            back.append({"price": extra_back[1], "size": 2.0})
        libro["runners"].append({"selectionId": r["selectionId"], "status": "ACTIVE",
                                 "ex": {"availableToBack": back}})
    return cat, libro


def scegli(*mercati):
    cat = [m[0] for m in mercati]
    book = {m[1]["marketId"]: m[1] for m in mercati}
    r = bp.scegli_mercato(cat, book, ADESSO)
    return None if r is None else (r[0]["marketId"], r[1]["runnerName"], r[3])


# --- scelta del mercato -------------------------------------------------------
check("favorito in trasferta", ("1.1", "Trasferta", 1.6),
      scegli(mercato("1.1", 30, (5.5, 4.2, 1.6))))
check("salta mercato in partenza fra 1 ora", ("1.2", "Casa", 1.9),
      scegli(mercato("1.1", 1, (1.5, 4, 6)), mercato("1.2", 30, (1.9, 3.6, 4.2))))
check("salta mercato in-play", ("1.2", "Casa", 1.9),
      scegli(mercato("1.1", 30, (1.5, 4, 6), inplay=True), mercato("1.2", 30, (1.9, 3.6, 4.2))))
check("salta mercato sospeso", ("1.2", "Casa", 1.9),
      scegli(mercato("1.1", 30, (1.5, 4, 6), stato="SUSPENDED"),
             mercato("1.2", 30, (1.9, 3.6, 4.2))))
check("salta se il favorito non e' abbastanza favorito", None,
      scegli(mercato("1.1", 30, (3.2, 3.3, 3.4))))
check("salta se una selezione non ha prezzo", None,
      scegli(mercato("1.1", 30, (1.5, None, 6))))
check("salta se qualcuno banca il favorito a quota alta", None,
      scegli(mercato("1.1", 30, (1.5, 4, 6), extra_back=(100, 990.0))))


# --- flusso completo dell'ordine, con Betfair simulato ------------------------
class FintoBetfair:
    """Risponde come l'API. `copione` dice cosa va storto e dove."""

    def __init__(self, **copione):
        self.copione = copione
        self.chiamate = []
        self.aperto = 0.0

    def __call__(self, s, metodo, params):
        self.chiamate.append(metodo)
        c = self.copione
        if metodo == "getAccountFunds":
            return {"availableToBetBalance": c.get("saldo", 50.0), "exposure": -self.aperto,
                    "wallet": "UK"}
        if metodo == "listMarketCatalogue":
            return [mercato("1.9", 30, (1.7, 3.9, 5.0))[0]]
        if metodo == "listMarketBook":
            return [mercato("1.9", 30, (1.7, 3.9, 5.0))[1]]
        if metodo == "placeOrders":
            istr = params["instructions"][0]
            check("ordine: punta", "BACK", istr["side"])
            check("ordine: quota 1000", 1000.0, istr["limitOrder"]["price"])
            check("ordine: 2 EUR", 2.0, istr["limitOrder"]["size"])
            check("ordine: decade al fischio d'inizio", "LAPSE",
                  istr["limitOrder"]["persistenceType"])
            check("ordine: sul favorito", 100, istr["selectionId"])
            if c.get("rifiuta"):
                raise bp.ErroreApi("placeOrders", c["rifiuta"], {"code": -32099})
            self.aperto = 2.0
            if c.get("rete_persa_invio"):
                raise requests.ConnectionError("timeout simulato")
            return {"status": "SUCCESS", "instructionReports": [
                {"status": "SUCCESS", "betId": "B1", "sizeMatched": 0.0,
                 "orderStatus": "EXECUTABLE"}]}
        if metodo == "cancelOrders":
            if c.get("annullamento_fallisce"):
                raise requests.ConnectionError("annullamento simulato fallito")
            annullati, self.aperto = self.aperto, 0.0
            return {"status": "SUCCESS", "instructionReports": [
                {"status": "SUCCESS", "sizeCancelled": annullati}]}
        if metodo == "listCurrentOrders":
            if "marketIds" in params:          # ricerca dopo rete persa
                return {"currentOrders": [{"betId": "B1", "selectionId": 100, "side": "BACK",
                                           "priceSize": {"price": 1000.0, "size": 2.0},
                                           "sizeRemaining": self.aperto}]}
            if self.aperto == 0:
                return {"currentOrders": []}
            return {"currentOrders": [{"betId": "B1", "sizeRemaining": self.aperto,
                                       "sizeMatched": 0.0}]}
        raise AssertionError(f"chiamata inattesa {metodo}")


def gira(finto, esegui=True, conferma="PROVA", tty=True):
    bp.time.sleep = lambda _: None
    bp.rpc = finto
    bp.login = lambda: None
    bp.input = lambda _: conferma
    bp.sys.stdin = SimpleNamespace(isatty=lambda: tty)
    bp.CARTELLA = Path("/tmp/pengwin-test-betfair")
    try:
        return bp.prova_ordine(SimpleNamespace(esegui=esegui, giorni=10))
    except bp.Rifiuto:
        return "rifiuto"


f = FintoBetfair()
check("anteprima: non invia nulla", 0, gira(f, esegui=False))
check("anteprima: nessun placeOrders", False, "placeOrders" in f.chiamate)

f = FintoBetfair()
check("conferma sbagliata -> rifiuto", "rifiuto", gira(f, conferma="si"))
check("conferma sbagliata: nessun placeOrders", False, "placeOrders" in f.chiamate)

f = FintoBetfair()
check("senza terminale -> rifiuto", "rifiuto", gira(f, tty=False))

f = FintoBetfair(saldo=1.5)
check("saldo sotto 2 EUR -> rifiuto", "rifiuto", gira(f))

f = FintoBetfair()
check("percorso felice -> 0", 0, gira(f))
check("percorso felice: annullato", 0.0, f.aperto)

f = FintoBetfair(rifiuta="PERMISSION_DENIED")
check("chiave senza permesso -> 5", 5, gira(f))
check("respinto: nessun annullamento tentato", False, "cancelOrders" in f.chiamate)

f = FintoBetfair(rete_persa_invio=True)
check("rete persa all'invio, ordine ritrovato e annullato -> 0", 0, gira(f))
check("rete persa: ordine annullato", 0.0, f.aperto)

f = FintoBetfair(annullamento_fallisce=True)
check("annullamento fallito -> 6 (controllo a mano)", 6, gira(f))
check("annullamento ritentato 3 volte", 3, f.chiamate.count("cancelOrders"))

print(f"\n{sum(esiti)}/{len(esiti)} verifiche superate")
sys.exit(0 if all(esiti) else 1)
