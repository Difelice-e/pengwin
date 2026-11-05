"""
Verifica del caricamento xG e del ripiego su football-data, senza rete.

    python tests/ripiego_xg.py        # dalla cartella cloud/

Il database e' finto: tre tabelle in memoria, con i filtri che usano
dataset.carica, freschezza e ripiego_xg.
"""
import contextlib
import io
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.report import dataset, freschezza, ripiego_xg  # noqa: E402

FUSO = ZoneInfo("Europe/Rome")
esiti = []


def check(nome, atteso, ottenuto):
    ok = atteso == ottenuto
    esiti.append(ok)
    print(f"{'OK  ' if ok else 'FAIL'} {nome}: atteso {atteso!r}, ottenuto {ottenuto!r}")


def roma(s):
    return datetime.fromisoformat(s).replace(tzinfo=FUSO)


def utc_iso(s):
    return roma(s).astimezone(ZoneInfo("UTC")).isoformat()


class DB:
    def __init__(self, partite, xg, log):
        self.t = {"partite": partite, "xg_partite": xg, "log_esecuzioni": log}

    def select(self, tabella, colonne="*", filtri=None, ordina=None, limite=None):
        righe = list(self.t[tabella])
        for k, v in (filtri or {}).items():
            if k == "gol_casa" and v == "not.is.null":
                righe = [r for r in righe if r.get("gol_casa") is not None]
            elif k == "disputata" and v == "is.true":
                righe = [r for r in righe if r.get("disputata")]
            elif k == "lega_us":
                ammesse = v[4:-1].split(",")
                righe = [r for r in righe if r["lega_us"] in ammesse]
            elif k == "and":                      # (datetime.gte.X,datetime.lt.Y)
                da, a = (p.split(".", 2)[2] for p in v[1:-1].split(","))
                righe = [r for r in righe if da <= r["datetime"][:19].replace(" ", "T") < a]
            elif v.startswith("eq."):
                righe = [r for r in righe if str(r.get(k)) == v[3:]]
            else:
                raise AssertionError(f"filtro non previsto: {k}={v}")
        if ordina:
            campo, verso = ordina.split(".")
            righe.sort(key=lambda r: r[campo], reverse=verso == "desc")
        return righe[:limite] if limite else righe


def partita(lega, data, casa, trasf, gc, gt, fdc=None, fdt=None):
    return {"lega": lega, "data": data, "casa": casa, "trasferta": trasf,
            "gol_casa": gc, "gol_trasferta": gt, "xg_casa_fd": fdc, "xg_trasferta_fd": fdt}


def xg(lega, quando, casa, trasf, xc=None, xt=None, giocata=True):
    return {"lega_us": lega, "datetime": quando, "casa": casa, "trasferta": trasf,
            "xg_casa": xc, "xg_trasferta": xt, "disputata": giocata}


def log(job, quando, esito="ok"):
    return {"job": job, "eseguito_il": utc_iso(quando), "esito": esito}


# Lo scenario del 6/11/2026: Understat fermo dal 1/11 alle 12:55.
PARTITE = [
    partita("I1", "2026-01-25", "Atalanta", "Parma", 4, 0),
    partita("I1", "2026-10-31", "Inter", "Roma", 1, 1, 1.20, 0.90),
    partita("I1", "2026-11-02", "Atalanta", "Parma", 0, 3, 2.80, 0.36),
    partita("I1", "2026-11-01", "Juventus", "Napoli", 2, 0, 0.79, 0.52),
    partita("SP1", "2026-11-01", "Santander", "Real Madrid", 0, 3, 0.97, 3.36),
    partita("I1", "2024-04-25", "Udinese", "Roma", 1, 2),               # ripresa dopo 11 giorni
    partita("I1", "2026-10-30", "Lecce", "Como", 0, 0),                 # Understat e fd senza xG
]
XG = [
    xg("Serie_A", "2026-01-25 14:00:00", "Atalanta", "Parma Calcio 1913", 5.02, 1.87),
    xg("Serie_A", "2026-10-31 20:45:00", "Inter", "Roma", 1.31, 1.05),
    xg("Serie_A", "2026-11-02 18:30:00", "Atalanta", "Parma Calcio 1913", giocata=False),
    xg("Serie_A", "2026-01-26 19:45:00", "Juventus", "Napoli", 1.55, 0.42),
    xg("Serie_A", "2026-11-01 17:00:00", "Juventus", "Napoli", giocata=False),
    xg("La_liga", "2026-11-01 16:15:00", "Racing Santander", "Real Madrid", giocata=False),
    xg("Serie_A", "2024-04-14 18:30:00", "Udinese", "Roma", 1.10, 1.40),
    xg("Serie_A", "2026-10-30 18:30:00", "Lecce", "Como", giocata=False),
]
LOG_FERMO = [log("ingest_understat", "2026-11-01T12:55"),
             log("ingest_understat", "2026-11-06T14:07", "parziale"),
             log("ingest_football_data", "2026-11-06T14:07")]
VENERDI = roma("2026-11-06T19:00")


def riga(d, casa, data):
    r = d[(d.HomeTeam == casa) & (d.MatchDate == pd.Timestamp(data))]
    return None if r.empty else r.iloc[0]


# --- il join non prende piu' gli xG di una stagione passata -------------------
db_fresco = DB(PARTITE, XG, [log("ingest_understat", "2026-11-06T14:07"),
                             log("ingest_football_data", "2026-11-06T14:07")])
d = dataset.carica(db_fresco, adesso=VENERDI)
check("Understat aggiornato: ripiego spento", False, ripiego_xg.stato(db_fresco, VENERDI).attivo)
check("Atalanta-Parma 2/11 senza xG Understat: fuori dal modello (prima: xG di gennaio)",
      None, riga(d, "Atalanta", "2026-11-02"))
check("Atalanta-Parma di gennaio: i suoi xG", 5.02, riga(d, "Atalanta", "2026-01-25").xgh)
check("Inter-Roma 31/10: xG Understat, non football-data", (1.31, "us"),
      (riga(d, "Inter", "2026-10-31").xgh, riga(d, "Inter", "2026-10-31").fonte_xg))
check("partita ripresa 11 giorni dopo: resta agganciata", 1.10, riga(d, "Udinese", "2024-04-25").xgh)
check("colonne del frame", ["Div", "MatchDate", "HomeTeam", "AwayTeam", "FTHG", "FTAG",
                            "xgh", "xga", "fonte_xg"], list(d.columns))

# --- ripiego acceso: venerdi' 6/11, Understat fermo da 5 giorni ----------------
db = DB(PARTITE, XG, LOG_FERMO)
st = ripiego_xg.stato(db, VENERDI)
check("ripiego attivo", True, st.attivo)
check("ripiego: dal giorno dell'ultimo ingest Understat riuscito", "2026-11-01", str(st.dal))
d = dataset.carica(db, adesso=VENERDI)
r = riga(d, "Atalanta", "2026-11-02")
check("Atalanta-Parma 2/11: xG football-data + correzione Serie A",
      (round(2.80 + 0.18, 2), round(0.36 + 0.18, 2), "fd"), (round(r.xgh, 2), round(r.xga, 2), r.fonte_xg))
r = riga(d, "Santander", "2026-11-01")
check("Santander-Real Madrid 1/11: xG football-data + correzione Liga",
      (round(0.97 + 0.14, 2), "fd"), (round(r.xgh, 2), r.fonte_xg))
check("Inter-Roma 31/10: resta Understat", (1.31, "us"),
      (riga(d, "Inter", "2026-10-31").xgh, riga(d, "Inter", "2026-10-31").fonte_xg))
check("Lecce-Como 30/10, prima del giorno del ripiego: non si riempie",
      None, riga(d, "Lecce", "2026-10-30"))
check("partite riempite", 3, int((d.fonte_xg == "fd").sum()))

# --- controllo strutturale con il ripiego --------------------------------------
cal_db = DB(PARTITE, [x for x in XG if x["datetime"] >= "2026-10-31"], LOG_FERMO)
uscita = io.StringIO()
with contextlib.redirect_stdout(uscita):
    motivi = freschezza.controlla(cal_db, d, ["I1", "SP1"], adesso=VENERDI)
check("controllo superato: le partite dell'1-2/11 ci sono, Understat fermo tollerato", [], motivi)
check("riga 'dati: RIPIEGO' per il log del turno", True,
      uscita.getvalue().startswith("dati: RIPIEGO xG football-data (turno degradato"))
check("la riga dice quante partite e dove", True, "3 partite dal 01/11" in uscita.getvalue()
      and "I1 2, SP1 1" in uscita.getvalue())

# --- senza ripiego il controllo rifiuta come prima -----------------------------
d_vecchio = dataset.carica(db, adesso=roma("2026-11-09T10:00"))
check("dopo la scadenza: ripiego spento", (False, "scaduto"),
      (ripiego_xg.stato(db, roma("2026-11-09T10:00")).attivo,
       ripiego_xg.stato(db, roma("2026-11-09T10:00")).motivo.split()[0]))
check("dopo la scadenza: nessuna partita riempita", 0, int((d_vecchio.fonte_xg == "fd").sum()))
with contextlib.redirect_stdout(io.StringIO()):
    motivi = freschezza.controlla(cal_db, d_vecchio, ["I1", "SP1"], adesso=roma("2026-11-09T10:00"))
check("dopo la scadenza, Understat fermo: rifiuta per l'ingest come prima", True,
      any(m.startswith("ingest_understat") for m in motivi))

db_tutto_fermo = DB(PARTITE, XG, [log("ingest_understat", "2026-11-01T12:55"),
                                  log("ingest_football_data", "2026-11-03T13:53")])
check("anche football-data fermo: ripiego spento", False,
      ripiego_xg.stato(db_tutto_fermo, VENERDI).attivo)
with contextlib.redirect_stdout(io.StringIO()):
    motivi = freschezza.controlla(db_tutto_fermo, d, ["I1"], adesso=VENERDI)
check("anche football-data fermo: rifiuta per entrambi gli ingest", 2,
      sum(m.startswith("ingest_") for m in motivi))

# --- il calendario continua a fare il suo lavoro --------------------------------
buco = PARTITE + [partita("I1", "2026-11-01", "Lazio", "Cagliari", 2, 1),     # fd senza xG
                  partita("I1", "2026-11-01", "Lecce", "Genoa", 1, 2)]
xg_buco = XG + [xg("Serie_A", "2026-11-01 12:30:00", "Lazio", "Cagliari", giocata=False),
                xg("Serie_A", "2026-11-01 15:00:00", "Lecce", "Genoa", giocata=False)]
db_buco = DB(buco, xg_buco, LOG_FERMO)
d_buco = dataset.carica(db_buco, adesso=VENERDI)
with contextlib.redirect_stdout(io.StringIO()):
    motivi = freschezza.controlla(DB(buco, [x for x in xg_buco if x["datetime"] >= "2026-10-31"],
                                     LOG_FERMO), d_buco, ["I1"], adesso=VENERDI)
check("due partite senza xG da nessuna fonte: il calendario rifiuta", True,
      any(m.startswith("I1: 2 partite") for m in motivi))

print(f"\n{sum(esiti)}/{len(esiti)} verifiche superate")
sys.exit(0 if all(esiti) else 1)
