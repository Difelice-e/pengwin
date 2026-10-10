"""
Turno automatico: lo lancia il timer `pengwin-turno` (venerdi' 19:00 per il
weekend, martedi' 14:00 per le infrasettimanali). Nessuna conferma a mano.

    python -m src.reale.automatico            # quello che fa il timer
    python -m src.reale.automatico --prova    # solo anteprime: nessun ordine, nulla scritto

Passi, in quest'ordine:
  1. turno reale con soldi veri (`turno --auto`), con la Live App Key che il
     servizio carica da ~/.betfair/live.env;
  2. Goal/No Goal su carta (`carta_btts --auto`), con la Delayed di
     betfair.env: e' lettura e simulazione, l'uso per cui Betfair la prevede.
     Gira anche se il turno reale e' fermo o rifiutato: non muove denaro;
  3. esito di ciascun passo in `log_esecuzioni` (job `reale_turno` e
     `carta_btts_turno`), con le righe che spiegano cosa e' successo. Da li'
     lo leggono l'attivita' programmata di Claude che manda l'email e la
     pagina privata.

Esiti scritti nel log:
  ok         ordini inviati e accettati
  nessuna    nessuna partita o nessuna giocata che passi le regole
  fermo      interruttore spento dalla pagina privata
  rifiutato  una regola di sicurezza ha bloccato il turno (dati, pre-registrazione, ...)
  incerto    inviato, ma con giocate respinte o incerte: vanno guardate
  errore     eccezione o posizione rifiutata da Betfair
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.db.client import client                      # noqa: E402
from src.reale import api                              # noqa: E402

ESITI_TURNO = {0: "ok", 2: "rifiutato", 3: "errore", 4: "incerto", 5: "fermo"}
SEGNALI = ("[!", "inviate ", "nessuna giocata", "turno automatico:", "=== GIOCATE",
           "registrate ", "finestra del turno", "saldo betfair", "dati:", "Traceback")


class Tee(io.TextIOBase):
    """Scrive sul terminale (journal) e tiene una copia."""

    def __init__(self, a):
        self.a, self.copia = a, io.StringIO()

    def write(self, t):
        self.a.write(t)
        self.copia.write(t)
        return len(t)

    def flush(self):
        self.a.flush()


def esegui(funzione, argv: list[str]) -> tuple[int, str]:
    tee = Tee(sys.stdout)
    try:
        with contextlib.redirect_stdout(tee):
            codice = funzione(argv)
    except SystemExit as e:                                  # argparse
        codice = int(e.code or 0)
    except Exception:                                        # noqa: BLE001
        tee.write(traceback.format_exc())
        codice = 1
    return codice, tee.copia.getvalue()


def riassunto(testo: str, massimo: int = 1800) -> str:
    """Le righe che dicono cosa e' successo, non tutto il rumore del modello."""
    righe = [r.rstrip() for r in testo.splitlines()]
    tenute = [r for r in righe if r.strip().startswith(SEGNALI) or r.startswith(("ven ", "sab ",
              "dom ", "lun ", "mar ", "mer ", "gio ")) or r.strip().startswith(("E0 ", "I1 "))
              or r.startswith("      ")]
    out = "\n".join(tenute) or "\n".join(righe[-15:])
    return out[-massimo:]


def esito_turno(codice: int, testo: str) -> str:
    if codice == 0:
        return "nessuna" if ("nessuna giocata" in testo or "=== GIOCATE (0)" in testo) else "ok"
    return ESITI_TURNO.get(codice, "errore")


def conta(testo: str, parola: str) -> int | None:
    m = re.search(rf"{parola} (\d+)", testo)
    return int(m.group(1)) if m else None


def chiave_delayed() -> str | None:
    """La App Key di betfair.env, senza toccare l'ambiente."""
    if not api.FILE_ENV.is_file():
        return None
    for riga in api.FILE_ENV.read_text(encoding="utf-8").splitlines():
        if riga.strip().startswith("BETFAIR_APP_KEY="):
            return riga.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Turno automatico (timer)")
    ap.add_argument("--prova", action="store_true", help="solo anteprime, nessun ordine")
    a = ap.parse_args(argv)

    from src.reale import carta_btts, turno               # import qui: pesano (modello)

    api.carica_env()
    db = client()

    if a.prova:
        # i due controlli propri del turno automatico, senza giocare
        from src.report import freschezza
        from src.report.dataset import carica
        acceso, motivo = turno.interruttore(db)
        print(f"interruttore: {motivo}")
        motivi = freschezza.controlla(db, carica(db), turno.CAMPIONATI)
        print("dati (controllo strutturale): " + ("ok" if not motivi else "; ".join(motivi)))
        print(f"finestra del turno se partisse adesso: fino a "
              f"{turno.fine_finestra(turno.datetime.now(turno.FUSO)):%a %d/%m %H:%M}\n")

    # 1. soldi veri, con la chiave che c'e' nell'ambiente (la Live dal servizio)
    codice, testo = esegui(turno.main, [] if a.prova else ["--auto"])
    esito = "prova" if a.prova else esito_turno(codice, testo)
    print(f"\n>>> turno reale: {esito} (uscita {codice})")
    if not a.prova:
        db.log("reale_turno", esito, conta(testo, "inviate"), riassunto(testo))

    # 2. carta, con la Delayed
    delayed = chiave_delayed()
    if delayed:
        os.environ["BETFAIR_APP_KEY"] = delayed
    c2, t2 = esegui(carta_btts.main, ["--consenti-dati-vecchi"] if a.prova else ["--auto"])
    e2 = "prova" if a.prova else ({0: "nessuna" if "nessuna giocata" in t2 else "ok",
                                   2: "rifiutato"}.get(c2, "errore"))
    print(f">>> Goal/No Goal su carta: {e2} (uscita {c2})")
    if not a.prova:
        db.log("carta_btts_turno", e2, conta(t2, "registrate"), riassunto(t2))

    return 1 if esito in ("errore", "incerto") or e2 == "errore" else 0


if __name__ == "__main__":
    raise SystemExit(main())
