"""
Verifica del braccio su carta Goal/No Goal, senza rete e senza credenziali.

    python tests/carta_btts.py        # dalla cartella cloud/
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.reale import api, carta_btts, quote, selezione  # noqa: E402

esiti = []


def check(nome, atteso, ottenuto):
    ok = atteso == ottenuto
    esiti.append(ok)
    print(f"{'OK  ' if ok else 'FAIL'} {nome}: atteso {atteso!r}, ottenuto {ottenuto!r}")


ADESSO = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)


def btts(n, casa, trasf, ore, q_si, q_no, comp, nome="Both teams to Score?"):
    inizio = (ADESSO + timedelta(hours=ore)).isoformat().replace("+00:00", "Z")
    c = {"marketId": f"1.{n}", "marketName": nome, "marketStartTime": inizio,
         "event": {"name": f"{casa} v {trasf}"}, "competition": {"id": comp},
         "runners": [{"selectionId": 30246, "runnerName": "Yes"},
                     {"selectionId": 110503, "runnerName": "No"}]}
    b = {"marketId": c["marketId"], "status": "OPEN", "inplay": False, "runners": [
        {"selectionId": 30246, "status": "ACTIVE",
         "ex": {"availableToBack": [{"price": q_si, "size": 40.0}]}},
        {"selectionId": 110503, "status": "ACTIVE",
         "ex": {"availableToBack": [{"price": q_no, "size": 40.0}]}}]}
    return c, b


MODELLO = {"Dortmund": 0.62, "Arsenal": 0.48, "Inter": 0.55}


def prob(lega, casa, trasf):
    if casa not in MODELLO:
        return f"squadra sconosciuta al modello: {casa}"
    return {"BTTS": MODELLO[casa], "H": 0.5, "D": 0.25, "A": 0.25}


cat, book = [], {}
for args in [(1, "Dortmund", "Werder Bremen", 26, 1.62, 2.40, "59"),     # Bundesliga
             (2, "Arsenal", "Leeds", 40, 1.95, 1.95, "10932509"),       # Premier
             (3, "Inter", "Parma", 0.1, 1.80, 2.05, "81"),              # fra 6 minuti
             (4, "Ignota", "Lazio", 30, 1.80, 2.05, "81")]:
    c, b = btts(*args)
    cat.append(c)
    book[c["marketId"]] = b
c5, b5 = btts(5, "Inter", "Parma", 30, 1.8, 2.05, "81", nome="Match Odds")
cat.append(c5)
book[c5["marketId"]] = b5

cand, scartate = carta_btts.candidati_btts(cat, book, prob, ADESSO)
check("candidati: Goal e No Goal di Dortmund e Arsenal (anche Bundesliga)", 4, len(cand))
check("candidati: partita fra 6 minuti esclusa", False, any(c["casa"] == "Inter" for c in cand))
check("candidati: mercato non BTTS ignorato", False, any(c["market_id"] == "1.5" for c in cand))
check("candidati: squadra ignota segnalata", True, any("Ignota" in s for s in scartate))
dort = {c["selezione"]: c for c in cand if c["casa"] == "Dortmund"}
check("Goal: p = P(BTTS) del modello", 0.62, dort["gg"]["p"])
check("No Goal: p = 1 - P(BTTS)", 0.38, round(dort["ng"]["p"], 6))
check("quote lette per nome Yes/No", (1.62, 2.40), (dort["gg"]["quota"], dort["ng"]["quota"]))

# Dortmund Goal 0.62 a 1.62: netta 1.5921 -> edge -1.3% (fuori)
# Dortmund NoGoal 0.38 a 2.40: netta 2.337 -> edge -11% (fuori)
# Arsenal Goal 0.48 a 1.95: netta 1.907 -> edge -8.5% (fuori)
# Arsenal NoGoal 0.52 a 1.95: netta 1.907 -> edge -0.8% (fuori)
check("seleziona: nessuna con questi prezzi", [], selezione.seleziona(cand, 400.0))
c6, b6 = btts(6, "Dortmund", "Mainz", 30, 1.75, 2.2, "59")
cand2, _ = carta_btts.candidati_btts([c6], {c6["marketId"]: b6}, prob, ADESSO)
sc = selezione.seleziona(cand2, 400.0)
# Goal 0.62 a 1.75: netta 1.71625 -> edge +6.4% -> dentro
check("seleziona: Goal di Dortmund-Mainz", ["gg"], [x["selezione"] for x in sc])
check("seleziona: puntata sul bankroll virtuale 400 (tetto 1%)", [4.0], [x["stake"] for x in sc])

# --- esiti e profitti ---------------------------------------------------------
check("1-1: Goal vinta", "vinta", carta_btts.esito("gg", 1, 1))
check("1-0: Goal persa", "persa", carta_btts.esito("gg", 1, 0))
check("0-0: No Goal vinta", "vinta", carta_btts.esito("ng", 0, 0))
check("2-1: No Goal persa", "persa", carta_btts.esito("ng", 2, 1))
check("profitto vinta 4 EUR a 1,75 netto 4,5% (2,865 arrotondato)", 2.86, carta_btts.profitto("vinta", 4.0, 1.75))
check("profitto persa", -4.0, carta_btts.profitto("persa", 4.0, 1.75))
partite = [{"data": "2026-10-10", "gol_casa": 2, "gol_trasferta": 1},
           {"data": "2026-03-01", "gol_casa": 0, "gol_trasferta": 0}]
check("risultato: partita del giorno previsto", 2,
      carta_btts.risultato(partite, "2026-10-10")["gol_casa"])
check("risultato: rinvio oltre 3 giorni non preso", None,
      carta_btts.risultato(partite[:1], "2026-10-20"))
check("risultato: non ancora giocata", None,
      carta_btts.risultato([{"data": "2026-10-10", "gol_casa": None}], "2026-10-10"))


# --- contabilizzazione con ripiego sulla fotografia ---------------------------
class FintoDB:
    def __init__(self, righe):
        self.righe = {r["id"]: dict(r) for r in righe}
        self.logs = []

    def select(self, tabella, colonne="*", filtri=None, ordina=None, limite=None):
        if tabella == "carta_btts":
            return [dict(r) for r in self.righe.values() if r.get("esito") is None]
        if tabella == "partite":
            return {"Inter": [{"data": "2026-10-10", "gol_casa": 1, "gol_trasferta": 1}],
                    "Roma": []}.get(filtri["casa"][3:], [])
        if tabella == "quote_snapshot":
            return [{"il": "2026-10-10T14:07:00+00:00", "back": 1.70}]
        raise AssertionError(tabella)

    def update(self, tabella, filtro, valori):
        self.righe[int(filtro["id"][3:])].update(valori)

    def log(self, *a):
        self.logs.append(a)


riga = {"selezione": "gg", "stake": 4.0, "quota": 1.80, "market_id": "1.9",
        "selection_id": 30246, "lega": "I1", "trasferta": "X", "data_partita": "2026-10-10",
        "inizio": "2026-10-10T16:00:00+00:00", "quota_chiusura": None}
db = FintoDB([dict(riga, id=1, casa="Inter"), dict(riga, id=2, casa="Roma")])
carta_btts.contabilizza(db, datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc))
r1 = db.righe[1]
check("contabilizza: 1-1, Goal vinta", ("vinta", 1, 1),
      (r1["esito"], r1["gol_casa"], r1["gol_trasferta"]))
check("contabilizza: profitto 4 x 0,80 x 0,955", 3.06, r1["profitto_netto"])
check("contabilizza: chiusura dalla fotografia, annotata", (1.70, "fotografia"),
      (r1["quota_chiusura"], r1["chiusura_fonte"]))
check("contabilizza: CLV 1,80 / 1,70 - 1", round(1.80 / 1.70 - 1, 6), r1["clv"])
check("contabilizza: senza risultato resta aperta", None, db.righe[2].get("esito"))


# --- chiusura diretta dal timer -------------------------------------------------
class DBChiusura(FintoDB):
    def select(self, tabella, colonne="*", filtri=None, ordina=None, limite=None):
        if tabella == "giocate_reali":
            return []
        if tabella == "carta_btts":
            return [dict(r) for r in self.righe.values()]
        raise AssertionError(tabella)


inizio = (ADESSO + timedelta(minutes=8)).isoformat()
dbc = DBChiusura([dict(riga, id=7, casa="Inter", inizio=inizio, market_id="1.77")])
api.libri = lambda s, ids: {"1.77": {"status": "OPEN", "inplay": False, "runners": [
    {"selectionId": 30246, "status": "ACTIVE",
     "ex": {"availableToBack": [{"price": 1.90, "size": 50}]}}]}}
quote.chiusura(dbc, lambda: None, ADESSO)
r7 = dbc.righe[7]
check("chiusura diretta: quota e fonte", (1.90, "diretta"),
      (r7.get("quota_chiusura"), r7.get("chiusura_fonte")))
check("chiusura diretta: CLV 1,80 / 1,90 - 1", round(1.80 / 1.90 - 1, 6), r7.get("clv"))


class DBSenzaTabella(DBChiusura):
    def select(self, tabella, colonne="*", filtri=None, ordina=None, limite=None):
        if tabella == "carta_btts":
            raise RuntimeError("PGRST205 tabella assente")
        return []


check("chiusura: tabella carta assente, nessun errore", 0,
      quote.chiusura(DBSenzaTabella([]), lambda: None, ADESSO))

print(f"\n{sum(esiti)}/{len(esiti)} verifiche superate")
sys.exit(0 if all(esiti) else 1)
