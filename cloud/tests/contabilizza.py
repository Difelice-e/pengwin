"""
Verifica della contabilizzazione reale, senza rete e senza credenziali.

    python tests/contabilizza.py        # dalla cartella cloud/
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.reale import api, contabilizza as cz  # noqa: E402

esiti = []


def check(nome, atteso, ottenuto):
    ok = atteso == ottenuto
    esiti.append(ok)
    print(f"{'OK  ' if ok else 'FAIL'} {nome}: atteso {atteso!r}, ottenuto {ottenuto!r}")


ADESSO = datetime(2026, 10, 10, 19, 0, tzinfo=timezone.utc)


def riga(i, bet, market, inizio_ore_fa, stake=4.0, quota=1.6, **kw):
    r = {"id": i, "bet_id": bet, "market_id": market, "customer_order_ref": f"pg26W41-0{i}",
         "casa": f"Casa{i}", "trasferta": f"Trasf{i}", "mercato": "OU25", "selezione": "over",
         "stake_richiesto": stake, "stake_abbinato": stake, "quota_abbinata": quota,
         "stato": "abbinata", "esito": None,
         "inizio": (ADESSO - timedelta(hours=inizio_ore_fa)).isoformat()}
    r.update(kw)
    return r


# --- risposte Betfair finte ---------------------------------------------------
CHIAMATE = []
CLEARED = {
    "SETTLED": [
        {"betId": "1", "marketId": "1.1", "betOutcome": "WON", "profit": 2.40,
         "sizeSettled": 4.0, "priceMatched": 1.6},
        {"betId": "2", "marketId": "1.2", "betOutcome": "LOST", "profit": -4.0,
         "sizeSettled": 4.0, "priceMatched": 1.71},
        {"betId": "5", "marketId": "1.5", "betOutcome": "WON", "profit": 1.00,
         "sizeSettled": 2.0, "priceMatched": 1.5},             # parziale: 2 su 4
    ],
    "VOIDED": [{"betId": "3", "marketId": "1.3", "profit": 0.0}],
    "LAPSED": [{"betId": "4", "marketId": "1.4"},
               {"betId": "5", "marketId": "1.5"}],              # il resto del parziale
    "CANCELLED": [],
}
COMMISSIONI = {"1.1": 0.11}     # 1.5: Betfair non la restituisce -> stima


def rpc_finto(s, metodo, params):
    CHIAMATE.append((metodo, params))
    assert metodo == "listClearedOrders", metodo
    if params.get("groupBy") == "MARKET":
        return {"clearedOrders": [{"marketId": m, "commission": c}
                                  for m, c in COMMISSIONI.items() if m in params["marketIds"]]
                + [{"marketId": "1.5"}], "moreAvailable": False}
    ids = set(params["betIds"])
    return {"clearedOrders": [o for o in CLEARED[params["betStatus"]] if o["betId"] in ids],
            "moreAvailable": False}


api.rpc = rpc_finto
api.saldo = lambda s: {"disponibile": 395.0, "esposizione": 20.5, "totale": 415.5}


class FintoDB:
    def __init__(self, righe, saldi=None):
        self.righe = {r["id"]: dict(r) for r in righe}
        self.saldi = saldi
        self.update_fatti, self.inseriti, self.logs = [], [], []

    def select(self, tabella, colonne="*", filtri=None, ordina=None, limite=None):
        if tabella == "giocate_reali":
            soglia = datetime.fromisoformat(filtri["inizio"][3:])
            stati = filtri["stato"][4:-1].split(",")
            return [dict(r) for r in self.righe.values()
                    if r["esito"] is None and r["bet_id"] and r["stato"] in stati
                    and datetime.fromisoformat(r["inizio"]) < soglia]
        if tabella == "saldi_reali":
            if self.saldi is None:
                raise RuntimeError("PGRST205 tabella assente")
            return self.saldi[-1:]
        raise AssertionError(tabella)

    def update(self, tabella, filtro, valori):
        self.update_fatti.append((tabella, filtro, valori))
        self.righe[int(filtro["id"][3:])].update(valori)

    def insert(self, tabella, righe):
        self.inseriti.append((tabella, righe))
        return len(righe)

    def log(self, *a):
        self.logs.append(a)


righe = [riga(1, "1", "1.1", 3), riga(2, "2", "1.2", 3, quota=1.71),
         riga(3, "3", "1.3", 3), riga(4, "4", "1.4", 3, stato="piazzata"),
         riga(5, "5", "1.5", 3, stato="parziale"),
         riga(6, "6", "1.6", 3),                     # non ancora regolata
         riga(7, "7", "1.7", 1),                     # iniziata da 1 ora: non si chiede
         riga(8, "8", "1.8", 5, esito="vinta", stato="chiusa")]   # gia' contabilizzata
db = FintoDB(righe, saldi=[])

sel = cz.da_regolare(db, ADESSO)
check("da regolare: solo iniziate da oltre 1h45, senza esito", [1, 2, 3, 4, 5, 6],
      sorted(r["id"] for r in sel))

fatte, attesa = cz.contabilizza(None, db, sel, ADESSO)
check("contabilizzate / in attesa", (5, 1), (fatte, attesa))
r = db.righe
check("vinta: esito, stato", ("vinta", "chiusa"), (r[1]["esito"], r[1]["stato"]))
check("vinta: lordo, commissione di Betfair, netto", (2.40, 0.11, 2.29),
      (r[1]["profitto_lordo"], r[1]["commissione"], r[1]["profitto_netto"]))
check("persa: netto -4, nessuna commissione", ("persa", -4.0, 0.0, -4.0),
      (r[2]["esito"], r[2]["profitto_lordo"], r[2]["commissione"], r[2]["profitto_netto"]))
check("void: esito e stato", ("void", "void", 0.0), (r[3]["esito"], r[3]["stato"],
                                                    r[3]["profitto_netto"]))
check("decaduta senza abbinamento: non_abbinata", ("void", "non_abbinata"),
      (r[4]["esito"], r[4]["stato"]))
check("parziale: SETTLED vince su LAPSED", ("vinta", "chiusa", 2.0),
      (r[5]["esito"], r[5]["stato"], r[5]["stake_abbinato"]))
check("parziale: commissione stimata al 4,5% e annotata", (0.04, 0.96, True),
      (r[5]["commissione"], r[5]["profitto_netto"], "stimata" in (r[5].get("note") or "")))
check("non regolata: resta aperta", (None, "abbinata"), (r[6]["esito"], r[6]["stato"]))
check("iniziata da poco: non toccata", None, r[7]["esito"])
check("mai toccate le colonne della decisione", True,
      all(not ({"stake_richiesto", "quota_riferimento", "edge_netto", "prob_modello",
                "saldo_al_turno", "chiave", "turno"} & set(v)) for _, _, v in db.update_fatti))
gruppi = [p for m, p in CHIAMATE if p.get("groupBy") == "MARKET"]
check("commissioni chieste solo per i mercati regolati", [["1.1", "1.2", "1.5"]],
      [g["marketIds"] for g in gruppi])

# --- ripartizione della commissione su due giocate dello stesso mercato --------
v = cz.valori_contabili({}, {"_tipo": "SETTLED", "betOutcome": "WON", "profit": 3.0},
                        0.18, 6.0, ADESSO)
check("commissione ripartita sul profitto (3 su 6 -> meta')", 0.09, v["commissione"])

# --- saldo -------------------------------------------------------------------------
cz.registra_saldo(None, db, {}, ADESSO)
check("saldo: prima riga scritta", [("saldi_reali", 415.5)],
      [(t, x[0]["totale"]) for t, x in db.inseriti])
db.inseriti.clear()
cz.registra_saldo(None, db, {"il": ADESSO.isoformat(), "disponibile": 395.0,
                             "esposizione": 20.5, "totale": 415.5}, ADESSO)
check("saldo invariato: nessuna riga", [], db.inseriti)
cz.registra_saldo(None, db, None, ADESSO)
check("tabella saldi assente: nessun errore, nessuna scrittura", [], db.inseriti)
check("ultimo_saldo con tabella assente -> None", None, cz.ultimo_saldo(FintoDB([], None)))
check("ultimo_saldo con tabella vuota -> {}", {}, cz.ultimo_saldo(FintoDB([], [])))

# --- main: niente da fare -> nessun login --------------------------------------------
login = []
api.sessione = lambda: login.append(1)
api.carica_env = lambda: None
cz.client = lambda: FintoDB([], saldi=[{"il": datetime.now(timezone.utc).isoformat(),
                                         "disponibile": 1, "esposizione": 0, "totale": 1}])
check("main senza giocate e saldo recente: uscita 0 senza login", (0, []), (cz.main([]), login))
cz.client = lambda: FintoDB([], saldi=[{"il": "2026-01-01T00:00:00+00:00",
                                         "disponibile": 1, "esposizione": 0, "totale": 1}])
check("main con saldo vecchio: fa il login", (0, [1]), (cz.main([]), login))

print(f"\n{sum(esiti)}/{len(esiti)} verifiche superate")
sys.exit(0 if all(esiti) else 1)
