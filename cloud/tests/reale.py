"""
Verifica del braccio reale senza rete, senza credenziali e senza soldi.

Betfair e Supabase sono simulati. Si controlla:
  - selezione: nomi Betfair -> modello, banda di edge sulla quota netta,
    puntate sul saldo, tetti, esposizione, ordinamento per edge;
  - piazzamento: esiti misti, rete persa dopo l'invio, ordini gia' presenti
    (nessun doppio invio), mercato sospeso, errore dell'API;
  - chiusura: quota di chiusura, CLV, aggiornamento dell'abbinato.

    python tests/reale.py        # dalla cartella cloud/
"""
import copy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ingest.betfair import quota_netta          # noqa: E402
from src.reale import api, quote, selezione, turno  # noqa: E402

esiti = []


def check(nome, atteso, ottenuto):
    ok = atteso == ottenuto
    esiti.append(ok)
    print(f"{'OK  ' if ok else 'FAIL'} {nome}: atteso {atteso!r}, ottenuto {ottenuto!r}")


ADESSO = datetime(2026, 10, 9, 17, 0, tzinfo=timezone.utc)


def evento(n, casa, trasf, ore, q1x2, qou, comp="81", stato="OPEN", inplay=False):
    """Catalogo + libri sintetici di una partita: Match Odds e Over/Under 2.5."""
    inizio = (ADESSO + timedelta(hours=ore)).isoformat().replace("+00:00", "Z")
    nome = f"{casa} v {trasf}"
    m1 = {"marketId": f"1.{n}1", "marketName": "Match Odds", "marketStartTime": inizio,
          "event": {"name": nome}, "competition": {"id": comp},
          "runners": [{"selectionId": n * 10 + 1, "runnerName": casa},
                      {"selectionId": n * 10 + 2, "runnerName": trasf},
                      {"selectionId": 58805, "runnerName": "The Draw"}]}
    m2 = {"marketId": f"1.{n}2", "marketName": "Over/Under 2.5 Goals",
          "marketStartTime": inizio, "event": {"name": nome}, "competition": {"id": comp},
          "runners": [{"selectionId": 47972, "runnerName": "Under 2.5 Goals"},
                      {"selectionId": 47973, "runnerName": "Over 2.5 Goals"}]}

    def libro(m, prezzi):
        return {"marketId": m["marketId"], "status": stato, "inplay": inplay,
                "runners": [{"selectionId": r["selectionId"], "status": "ACTIVE",
                             "ex": {"availableToBack": [{"price": p, "size": 50.0}],
                                    "availableToLay": [{"price": round(p + 0.02, 2),
                                                        "size": 40.0}]}}
                            for r, p in zip(m["runners"], prezzi)]}
    h, d, a = q1x2          # quote di 1, X, 2
    u, o = qou
    return [m1, m2], {m1["marketId"]: libro(m1, (h, a, d)), m2["marketId"]: libro(m2, (u, o))}


MODELLO = {
    ("I1", "Inter", "AC Milan"): "squadra sconosciuta al modello: AC Milan",  # mai: nome tradotto
    ("I1", "Inter", "Milan"): {"H": 0.55, "D": 0.25, "A": 0.20, "O2.5": 0.60, "U2.5": 0.40},
    ("I1", "Roma", "Lazio"): {"H": 0.40, "D": 0.30, "A": 0.30, "O2.5": 0.45, "U2.5": 0.55},
}


def prob(lega, casa, trasf):
    return MODELLO.get((lega, casa, trasf), f"squadra sconosciuta al modello: {casa}")


# --- candidati: nomi Betfair, mercati, finestre --------------------------------
cat, book = [], {}
for args in [(1, "Inter", "AC Milan", 26, (1.95, 4.5, 3.6), (2.2, 1.70)),   # AC Milan -> Milan
             (2, "Roma", "Lazio", 0.1, (2.5, 3.0, 3.2), (1.8, 2.1)),         # parte fra 6 minuti
             (3, "Ignota", "Lazio", 27, (2.5, 3.0, 3.2), (1.8, 2.1))]:
    c, b = evento(*args)
    cat += c
    book.update(b)
cand, scartate = selezione.candidati(cat, book, prob, ADESSO)
check("candidati: 5 selezioni di Inter-Milan", 5, len(cand))
check("candidati: nome Betfair 'AC Milan' tradotto in 'Milan'", {"Milan"},
      {c["trasferta"] for c in cand})
check("candidati: partita fra 6 minuti esclusa", False,
      any(c["casa"] == "Roma" for c in cand))
check("candidati: squadra ignota segnalata", True,
      any("Ignota" in s for s in scartate))
sel_1x2 = {c["selezione"]: c["quota"] for c in cand if c["mercato"] == "1X2"}
check("candidati: 1 / X / 2 sulle quote giuste", {"1": 1.95, "2": 3.6, "X": 4.5}, sel_1x2)
check("candidati: over / under", {"over": 1.70, "under": 2.2},
      {c["selezione"]: c["quota"] for c in cand if c["mercato"] == "OU25"})

c3, b3 = evento(5, "Bayern Munich", "Dortmund", 26, (1.6, 4.5, 5.0), (3.0, 1.4), comp="59")
check("candidati: Bundesliga esclusa (solo Premier e Serie A)", 0,
      len(selezione.candidati(c3, b3, lambda *a: MODELLO[("I1", "Inter", "Milan")], ADESSO)[0]))
c4, b4 = evento(6, "Arsenal", "Leeds", 26, (1.4, 5.3, 9.2), (2.5, 1.6), comp="10932509")
check("candidati: Premier inclusa", 5,
      len(selezione.candidati(c4, b4, lambda *a: MODELLO[("I1", "Inter", "Milan")], ADESSO)[0]))

c2, b2 = evento(4, "Inter", "Milan", 26, (1.95, 4.5, 3.6), (2.2, 1.7), inplay=True)
check("candidati: mercato in-play escluso", 0, len(selezione.candidati(c2, b2, prob, ADESSO)[0]))

# --- seleziona: edge netto, Kelly sul saldo, tetti ----------------------------
# Inter (p 0.55) a 1.95: netta 1.90775 -> edge +4.9% -> dentro
# X (p 0.25) a 4.5: netta 4.3425 -> edge +8.6% -> dentro
# Milan (p 0.20) a 3.6: netta 3.483 -> edge -30% -> fuori
# Over (p 0.60) a 1.70: netta 1.6685 -> edge +0.1% -> fuori (sotto 2%)
# Under (p 0.40) a 2.2: netta 2.146 -> edge -14% -> fuori
sc = selezione.seleziona(copy.deepcopy(cand), 400.0)
check("seleziona: due giocate", ["X", "1"], [x["selezione"] for x in sc])
check("seleziona: ordinate per edge decrescente", True, sc[0]["edge"] > sc[1]["edge"])
check("seleziona: edge calcolato sulla quota netta", round(0.55 * quota_netta(1.95) - 1, 6),
      round(next(x for x in sc if x["selezione"] == "1")["edge"], 6))
# X: Kelly/4 = 0,64% di 400 = 2,56 -> 2,50.  1: Kelly/4 = 1,35% -> tetto 1% = 4,00
check("seleziona: Kelly 1/4 sul saldo, tetto 1%, multipli di 0,50", [2.5, 4.0],
      [x["stake"] for x in sc])
check("seleziona: con 250 EUR resta solo l'1 a 2,50 (X sotto il minimo)", [("1", 2.5)],
      [(x["selezione"], x["stake"]) for x in selezione.seleziona(copy.deepcopy(cand), 250.0)])
check("seleziona: con 150 EUR niente (1,50 sotto il minimo)", [],
      selezione.seleziona(copy.deepcopy(cand), 150.0))
check("seleziona: tetto assoluto 10 EUR con saldo 5.000", [10.0, 10.0],
      [x["stake"] for x in selezione.seleziona(copy.deepcopy(cand), 5000.0)])
check("seleziona: saldo zero, niente", [], selezione.seleziona(copy.deepcopy(cand), 0.0))
lontana = [dict(cand[0], quota=21.0, p=0.06)]
check("seleziona: quota oltre 15 scartata", [], selezione.seleziona(lontana, 400.0))

molti = []
for i in range(40):
    molti.append({"lega": "I1", "casa": f"C{i}", "trasferta": f"T{i}", "evento_bf": f"e{i}",
                  "inizio": ADESSO + timedelta(days=1, minutes=i), "mercato": "1X2",
                  "selezione": "1", "market_id": f"1.9{i}", "selection_id": i,
                  "p": 0.5 + i * 0.0005, "quota": 2.2, "size": 100.0})
sm = selezione.seleziona(molti, 400.0)
check("seleziona: al massimo 25 giocate", 25, len(sm))
# edge dentro la banda fino a i=25 (26 candidati): resta fuori il piu' basso, i=0
check("seleziona: tiene gli edge piu' alti", "C0" not in {x["casa"] for x in sm}, True)
check("seleziona: riscalo 4,00 -> 3,00 per stare nel 20%", {3.0}, {x["stake"] for x in sm})
check("seleziona: esposizione entro il 20%", True, sum(x["stake"] for x in sm) <= 80.0)
check("seleziona: puntate valide dopo il riscalo", True,
      all(x["stake"] >= 2 and (x["stake"] * 2).is_integer() for x in sm))
check("controlla: selezione regolare passa", [], selezione.controlla(sm, 400.0))
rotta = [dict(sm[0], stake=25.0)]
check("controlla: puntata oltre i tetti fissi bloccata", True,
      any("tetti" in p for p in selezione.controlla(rotta, 400.0)))


# --- turni infrasettimanali e Goal/No Goal ----------------------------------------
roma = __import__("zoneinfo").ZoneInfo("Europe/Rome")
check("turno: venerdi' 9/10 -> weekend W41", "2026-W41",
      turno.turno_corrente(datetime(2026, 10, 9, 20, 0, tzinfo=roma)))
check("turno: martedi' 13/10 -> infrasettimanale W42", "2026-W42-inf",
      turno.turno_corrente(datetime(2026, 10, 13, 18, 0, tzinfo=roma)))
check("turno: venerdi' 16/10 -> weekend W42, diverso dal martedi'", "2026-W42",
      turno.turno_corrente(datetime(2026, 10, 16, 20, 0, tzinfo=roma)))
check("rif_ordine weekend", "pg26W41-07", turno.rif_ordine("2026-W41", 7))
check("rif_ordine infrasettimanale", "pg26W42i-07", turno.rif_ordine("2026-W42-inf", 7))

btts = {"marketId": "1.88", "marketName": "Both teams to Score?",
        "marketStartTime": cat[0]["marketStartTime"], "event": {"name": "Inter v AC Milan"},
        "competition": {"id": "81"}, "runners": [{"selectionId": 30246, "runnerName": "Yes"},
                                                {"selectionId": 110503, "runnerName": "No"}]}
book_btts = {"1.88": {"marketId": "1.88", "status": "OPEN", "inplay": False, "runners": [
    {"selectionId": 30246, "status": "ACTIVE", "ex": {"availableToBack": [{"price": 1.7, "size": 9}]}},
    {"selectionId": 110503, "status": "ACTIVE", "ex": {"availableToBack": [{"price": 2.2, "size": 9}]}}]}}
check("Goal/No Goal: riconosciuto come BTTS", "BTTS", selezione._tipo("Both teams to Score?"))
check("Goal/No Goal: mai fra i candidati da giocare", 0,
      len(selezione.candidati([btts], book_btts, prob, ADESSO)[0]))
check("Goal/No Goal: presente nelle fotografie", {"BTTS"},
      {r["mercato"] for r in quote.righe_fotografia([btts], book_btts, "periodica")})

gia = [{"market_id": "1.11", "stato": "abbinata"}, {"market_id": "1.12", "stato": "respinta"}]
restano, tolte = selezione.togli_gia_giocate(copy.deepcopy(cand), gia)
# 1.11 = 1X2 di Inter-Milan (3 selezioni, abbinata: esclusa), 1.12 = O/U (2, respinta: resta)
check("mercato gia' giocato escluso, respinto no", (2, 3), (len(restano), tolte))


# --- piazzamento --------------------------------------------------------------
class FintoDB:
    def __init__(self):
        self.righe = {}
        self.inseriti = []

    def insert(self, tabella, righe, ritorna=False):
        if tabella == "giocate_reali":
            out = []
            for r in righe:
                r = dict(r, id=len(self.righe) + 1)
                self.righe[r["id"]] = r
                out.append(r)
            return out if ritorna else len(out)
        self.inseriti.append((tabella, righe))
        return len(righe)

    def update(self, tabella, filtro, valori):
        i = int(filtro["id"].split(".")[1])
        self.righe[i].update(valori)
        return [self.righe[i]]

    def select(self, tabella, colonne="*", filtri=None):
        return list(self.righe.values())

    def log_esecuzione(self, *a):
        pass


class FintoBetfair:
    def __init__(self, presenti=None, rete_persa=False, rifiuta_api=False, respingi=(),
                 sospeso=()):
        self.presenti = presenti or {}
        self.rete_persa = rete_persa
        self.rifiuta_api = rifiuta_api
        self.respingi = set(respingi)
        self.sospeso = set(sospeso)
        self.inviati = []
        self.bet = 100

    def rpc(self, s, metodo, params):
        if metodo == "listCurrentOrders":
            m = params["marketIds"][0]
            return {"currentOrders": [o for o in self.presenti.values() if o["marketId"] == m]}
        if metodo == "placeOrders":
            if self.rifiuta_api:
                raise RuntimeError('placeOrders: {"code": -32099, "INVALID_SESSION"}')
            reports = []
            for ist in params["instructions"]:
                self.inviati.append(ist["customerOrderRef"])
                if ist["customerOrderRef"] in self.respingi:
                    reports.append({"status": "FAILURE", "errorCode": "INSUFFICIENT_FUNDS",
                                    "instruction": ist})
                    continue
                self.bet += 1
                o = {"betId": str(self.bet), "marketId": params["marketId"],
                     "customerOrderRef": ist["customerOrderRef"], "sizeMatched": 1.5,
                     "averagePriceMatched": ist["limitOrder"]["price"],
                     "placedDate": "2026-10-09T17:01:00.000Z"}
                self.presenti[ist["customerOrderRef"]] = o
                reports.append({"status": "SUCCESS", "betId": o["betId"], "instruction": ist,
                                "sizeMatched": 1.5,
                                "averagePriceMatched": ist["limitOrder"]["price"],
                                "placedDate": o["placedDate"]})
            if self.rete_persa:
                raise requests.ConnectionError("risposta persa")
            return {"status": "SUCCESS" if all(r["status"] == "SUCCESS" for r in reports)
                    else "FAILURE", "instructionReports": reports}
        raise AssertionError(metodo)

    def libri(self, s, ids):
        return {m: {"marketId": m, "status": "SUSPENDED" if m in self.sospeso else "OPEN",
                    "inplay": False, "runners": []} for m in ids}


class FintoLog:
    file = "/dev/null"

    def scrivi(self, *a):
        pass


def decisioni(db, n_mercati=2, per_mercato=1):
    righe = []
    for m in range(n_mercati):
        for k in range(per_mercato):
            i = len(righe) + 1
            righe.append({"market_id": f"1.5{m}", "selection_id": 10 + k,
                          "customer_order_ref": turno.rif_ordine("2026-W41", i),
                          "stake_richiesto": 4.0, "quota_riferimento": 2.1,
                          "stato": "da_piazzare"})
    return db.insert("giocate_reali", righe, ritorna=True)


def gira(bf, db, righe):
    api.rpc, api.libri = bf.rpc, bf.libri
    return turno.piazza(None, db, FintoLog(), righe, "2026-W41")


db = FintoDB()
bf = FintoBetfair()
conti = gira(bf, db, decisioni(db))
check("piazza: due ordini inviati", 2, conti["inviate"])
check("piazza: abbinamento parziale registrato", {"parziale"},
      {r["stato"] for r in db.righe.values()})
check("piazza: betId salvato", True, all(r.get("bet_id") for r in db.righe.values()))
check("piazza: ordine LIMIT/LAPSE alla quota letta", 2, len(bf.inviati))

conti = gira(bf, db, list(db.righe.values()))
check("piazza: rilancio, nessun doppio invio", 2, len(bf.inviati))
check("piazza: rilancio, ordini riconosciuti come gia' presenti", 2, conti["gia_presenti"])

db = FintoDB()
bf = FintoBetfair(respingi={turno.rif_ordine("2026-W41", 2)})
conti = gira(bf, db, decisioni(db, n_mercati=1, per_mercato=2))
check("piazza: una respinta e una inviata", (1, 1), (conti["inviate"], conti["respinte"]))
check("piazza: motivo del rifiuto salvato", "INSUFFICIENT_FUNDS", db.righe[2]["errore"])

db = FintoDB()
bf = FintoBetfair(rete_persa=True)
conti = gira(bf, db, decisioni(db))
check("piazza: rete persa, ordini ritrovati su Betfair", 2, conti["inviate"])
check("piazza: rete persa, nessuna incerta", 0, conti["incerte"])

db = FintoDB()
bf = FintoBetfair(rifiuta_api=True)
conti = gira(bf, db, decisioni(db))
check("piazza: errore API, tutte respinte", 2, conti["respinte"])

db = FintoDB()
bf = FintoBetfair(sospeso={"1.50"})
conti = gira(bf, db, decisioni(db))
check("piazza: mercato sospeso annullato, l'altro inviato", (1, 1),
      (conti["annullate"], conti["inviate"]))
check("piazza: nessun ordine sul mercato sospeso", 1, len(bf.inviati))


# --- chiusura -----------------------------------------------------------------
class DBChiusura(FintoDB):
    def __init__(self, righe):
        super().__init__()
        self.righe = {r["id"]: r for r in righe}

    def log(self, *a, **k):
        pass

    def insert(self, tabella, righe, ritorna=False):
        return len(righe)

    def select(self, tabella, colonne="*", filtri=None):
        stati = filtri["stato"][4:-1].split(",")
        entro = datetime.fromisoformat(filtri["inizio"][4:])
        return [dict(r) for r in self.righe.values() if r["stato"] in stati
                and datetime.fromisoformat(r["inizio"]) <= entro]

    def update(self, tabella, filtro, valori):
        self.aggiornate = getattr(self, "aggiornate", []) + [int(filtro["id"][3:])]
        return super().update(tabella, filtro, valori)


inizio = (ADESSO + timedelta(minutes=8)).isoformat()
lontana = (ADESSO + timedelta(hours=3)).isoformat()
dbc = DBChiusura([
    {"id": 1, "stato": "parziale", "inizio": inizio, "bet_id": "B1", "market_id": "1.71",
     "selection_id": 7, "stake_richiesto": 4.0, "quota_abbinata": 2.1},
    {"id": 2, "stato": "piazzata", "inizio": lontana, "bet_id": "B2", "market_id": "1.72",
     "selection_id": 8, "stake_richiesto": 4.0, "quota_abbinata": None},
])


def rpc_chiusura(s, metodo, params):
    if metodo == "listCurrentOrders":
        return {"currentOrders": [{"betId": "B1", "sizeMatched": 4.0,
                                   "averagePriceMatched": 2.1}]}
    raise AssertionError(metodo)


api.rpc = rpc_chiusura
api.libri = lambda s, ids: {"1.71": {"status": "OPEN", "inplay": False, "runners": [
    {"selectionId": 7, "status": "ACTIVE", "ex": {"availableToBack": [{"price": 2.0,
                                                                       "size": 90}]}}]}}
api.catalogo = lambda s, da, a: []
quote.chiusura(dbc, lambda: None, ADESSO)
r1 = dbc.righe[1]
check("chiusura: quota di chiusura registrata", 2.0, r1.get("quota_chiusura_it"))
check("chiusura: CLV = 2,10 / 2,00 - 1", 0.05, r1.get("clv"))
check("chiusura: abbinato aggiornato a 4 EUR", ("abbinata", 4.0),
      (r1.get("stato"), r1.get("stake_abbinato")))
check("chiusura: partita fra 3 ore non toccata", [1], dbc.aggiornate)

print(f"\n{sum(esiti)}/{len(esiti)} verifiche superate")
sys.exit(0 if all(esiti) else 1)
