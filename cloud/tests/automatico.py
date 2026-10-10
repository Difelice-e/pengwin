"""
Verifica del turno automatico, senza rete e senza credenziali.

    python tests/automatico.py        # dalla cartella cloud/
"""
import builtins
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.reale import api, automatico, turno  # noqa: E402
from src.report import freschezza  # noqa: E402

FUSO = ZoneInfo("Europe/Rome")
esiti = []


def check(nome, atteso, ottenuto):
    ok = atteso == ottenuto
    esiti.append(ok)
    print(f"{'OK  ' if ok else 'FAIL'} {nome}: atteso {atteso!r}, ottenuto {ottenuto!r}")


# --- finestra del turno -------------------------------------------------------
def ff(s):
    return turno.fine_finestra(datetime.fromisoformat(s).replace(tzinfo=FUSO)).strftime("%a %d/%m %H:%M")


check("venerdi' 19:00 -> lunedi' 23:59", "Mon 19/10 23:59", ff("2026-10-16T19:00"))
check("sabato -> lunedi'", "Mon 19/10 23:59", ff("2026-10-17T10:00"))
check("domenica -> lunedi' dopo", "Mon 19/10 23:59", ff("2026-10-18T22:00"))
check("lunedi' -> giovedi'", "Thu 22/10 23:59", ff("2026-10-19T09:00"))
check("martedi' 14:00 -> giovedi'", "Thu 22/10 23:59", ff("2026-10-20T14:00"))
check("giovedi' sera -> giovedi' stesso", "Thu 22/10 23:59", ff("2026-10-22T20:00"))
check("turno del venerdi' e infrasettimanale successivo non si sovrappongono", True,
      turno.fine_finestra(datetime(2026, 10, 16, 19, tzinfo=FUSO))
      < datetime(2026, 10, 20, 14, tzinfo=FUSO))


# --- interruttore -------------------------------------------------------------
class DBInterruttore:
    def __init__(self, righe=None, errore=None):
        self.r, self.e = righe, errore

    def select(self, tabella, **k):
        if self.e:
            raise RuntimeError(self.e)
        return self.r


check("tabella assente -> spento", False, turno.interruttore(DBInterruttore(errore="PGRST205"))[0])
check("riga mancante -> spento", False, turno.interruttore(DBInterruttore([]))[0])
spento = turno.interruttore(DBInterruttore([{"attivo": False, "modificato_da": "dif@x",
                                             "modificato_il": "2026-10-16T18:00:00"}]))
check("spento dalla pagina: chi e quando", (False, True), (spento[0], "dif@x" in spento[1]))
check("acceso", True, turno.interruttore(DBInterruttore([{"attivo": True}]))[0])


# --- turno --auto end-to-end, con tutto finto -------------------------------------
ADESSO = datetime.now(timezone.utc)


def evento(n, casa, trasf, ore, q1x2, qou, comp="81"):
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
        return {"marketId": m["marketId"], "status": "OPEN", "inplay": False,
                "runners": [{"selectionId": r["selectionId"], "status": "ACTIVE",
                             "ex": {"availableToBack": [{"price": p, "size": 50.0}]}}
                            for r, p in zip(m["runners"], prezzi)]}
    h, d, a = q1x2
    u, o = qou
    return [m1, m2], {m1["marketId"]: libro(m1, (h, a, d)), m2["marketId"]: libro(m2, (u, o))}


CAT, BOOK = evento(1, "Inter", "Parma", 30, (1.5, 6.0, 4.6), (3.35, 1.35))
# Inter-Parma: modello Under 2.5 al 32,6% contro 3,35 -> edge +5,8%; il resto fuori banda
MODELLO = {"H": 0.62, "D": 0.22, "A": 0.16, "O2.5": 0.674, "U2.5": 0.326}


class FintoDB:
    def __init__(self, attivo=True, gia=(), prereg=("braccio_reale", "braccio_reale_automatico")):
        self.attivo, self.gia, self.prereg = attivo, list(gia), set(prereg)
        self.inserite, self.log_ = [], []

    def select(self, tabella, colonne="*", filtri=None, ordina=None, limite=None):
        filtri = filtri or {}
        if tabella == "config_reale":
            return [{"attivo": self.attivo, "modificato_da": "prova", "modificato_il": "x"}]
        if tabella == "giocate_reali":
            if "turno" in filtri:
                return self.gia
            return []
        if tabella == "preregistrazioni":
            k = filtri["chiave"][3:]
            return [{"chiave": k}] if k in self.prereg else []
        raise AssertionError(tabella)

    def insert(self, tabella, righe, ritorna=False):
        assert tabella == "giocate_reali", tabella
        out = [dict(r, id=i) for i, r in enumerate(righe, 1)]
        self.inserite += out
        return out if ritorna else len(out)

    def log(self, *a):
        self.log_.append(a)


STATO = {}


def prepara(db, motivi=(), lista=None):
    STATO.update(login=0, piazzati=[], finestra=None)
    turno.client = lambda: db
    api.carica_env = lambda *a: None
    os.environ["SUPABASE_KEY"] = "sb_secret_finta"

    def sessione():
        STATO["login"] += 1
        return object()
    api.sessione = sessione
    api.saldo = lambda s: {"disponibile": 400.0, "esposizione": 0.0, "totale": 400.0}
    turno.carica = lambda db: pd.DataFrame()
    turno.build_models = lambda d, oggi: {
        "I1": (None, None, 1110, None, pd.Timestamp("2026-09-20"))}
    turno.probabilita_dal_modello = lambda models: (lambda lega, c, t: MODELLO)
    freschezza.controlla = lambda db, d, leghe, adesso=None: list(motivi)

    def catalogo(s, da, a, tipi=None, leghe=None):
        STATO["finestra"] = a
        return CAT if lista is None else lista
    api.catalogo = catalogo
    api.libri = lambda s, ids: {i: BOOK[i] for i in ids if i in BOOK}

    def piazza(s, db_, log, righe, t):
        STATO["piazzati"] = righe
        return {"inviate": len(righe), "gia_presenti": 0, "respinte": 0, "incerte": 0,
                "annullate": 0, "abbinato": sum(r["stake_richiesto"] for r in righe)}
    turno.piazza = piazza
    api.LogOrdini = lambda db, t: type("L", (), {"file": "/dev/null"})()


def mai_chiedere(*a, **k):
    raise AssertionError("input() chiamato in modalita' automatica")


builtins.input = mai_chiedere

prepara(FintoDB(attivo=False))
check("interruttore spento: uscita 5, nessun login, nessun ordine", (5, 0, []),
      (turno.main(["--auto"]), STATO["login"], STATO["piazzati"]))

prepara(FintoDB(), motivi=["I1: 3 partite gia' giocate senza risultato nei dati"])
check("dati con buchi: rifiuto senza ordini", (2, []), (turno.main(["--auto"]), STATO["piazzati"]))

db = FintoDB()
prepara(db)
codice = turno.main(["--auto"])
check("turno automatico: uscita 0", 0, codice)
check("una giocata piazzata senza chiedere conferma", 1, len(STATO["piazzati"]))
g = STATO["piazzati"][0] if STATO["piazzati"] else {}
check("giocata: Inter-Parma Under 2.5 a 3,35", ("OU25", "under", 3.35),
      (g.get("mercato"), g.get("selezione"), g.get("quota_riferimento")))
check("decisione scritta prima dell'ordine", True, bool(db.inserite) and db.inserite[0]["stato"] == "da_piazzare")
attesa = turno.fine_finestra(datetime.now(FUSO)) - datetime.now(FUSO)
check("finestra = fine del turno (entro un minuto)", True,
      STATO["finestra"] is not None and abs((STATO["finestra"] - attesa).total_seconds()) < 60)

prepara(FintoDB(prereg=("braccio_reale",)))
check("senza pre-registrazione dell'automatico: rifiuto", (2, []),
      (turno.main(["--auto"]), STATO["piazzati"]))

prepara(FintoDB(gia=[{"id": 1, "stato": "abbinata"}]))
check("turno gia' giocato: rifiuto", (2, []), (turno.main(["--auto"]), STATO["piazzati"]))

check("--auto con --consenti-dati-vecchi: rifiutato", 2,
      turno.main(["--auto", "--consenti-dati-vecchi"]))


# --- automatico: esiti e riassunto -----------------------------------------------------
check("esito: ordini inviati", "ok", automatico.esito_turno(0, "inviate 8, gia' presenti 0"))
check("esito: nessuna giocata", "nessuna", automatico.esito_turno(0, "\nnessuna giocata da inviare"))
check("esito: interruttore", "fermo", automatico.esito_turno(5, "[!] FERMO"))
check("esito: rifiuto", "rifiutato", automatico.esito_turno(2, "[!] RIFIUTATO"))
check("esito: incerte", "incerto", automatico.esito_turno(4, ""))
check("esito: errore", "errore", automatico.esito_turno(1, "Traceback"))
check("conta: ordini inviati", 8, automatico.conta("inviate 8, gia' presenti 0", "inviate"))


def esplode(argv):
    raise ValueError("boom")


cod, testo = automatico.esegui(esplode, [])
check("eccezione catturata: uscita 1 e traceback nel testo", (1, True), (cod, "ValueError: boom" in testo))
r = automatico.riassunto("modello I1 1110 partite\n[!] RIFIUTATO: dati non aggiornati:\n"
                         "      I1: 3 partite\nrumore\n")
check("riassunto: tiene i motivi, scarta il rumore", (True, False),
      ("I1: 3 partite" in r, "rumore" in r))

print(f"\n{sum(esiti)}/{len(esiti)} verifiche superate")
sys.exit(0 if all(esiti) else 1)
