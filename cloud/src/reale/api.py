"""
Accesso a Betfair e configurazione del server, per il braccio reale.

Riusa login e chiamate di `src.ingest.betfair` (stesso endpoint, stessi id di
competizione, stessi nomi squadra) e aggiunge le due cose che servono per
giocare: il saldo del conto e le chiamate che scrivono ordini.

Credenziali: ~/.betfair/betfair.env sul server (permessi 600), con
BETFAIR_APP_KEY, BETFAIR_USERNAME, BETFAIR_PASSWORD, BETFAIR_CERT, BETFAIR_KEY
e SUPABASE_KEY (la secret key: la publishable non puo' scrivere le tabelle
del braccio reale). Le variabili gia' presenti nell'ambiente hanno la
precedenza sul file.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.ingest.betfair import COMPETIZIONI, TIMEOUT, rpc, sessione   # noqa: E402,F401

__all__ = ["COMPETIZIONI", "rpc", "sessione", "carica_env", "saldo", "catalogo",
           "libri", "migliore", "LogOrdini"]

ACCOUNT = "https://api.betfair.com/exchange/account/json-rpc/v1"
FILE_ENV = Path.home() / ".betfair" / "betfair.env"
LOG_LOCALE = Path.home() / ".betfair" / "ordini"


def carica_env(percorso: Path = FILE_ENV) -> None:
    """KEY=VALUE da betfair.env, senza sovrascrivere l'ambiente."""
    if not percorso.is_file():
        return
    for riga in percorso.read_text(encoding="utf-8").splitlines():
        riga = riga.strip()
        if not riga or riga.startswith("#") or "=" not in riga:
            continue
        k, v = riga.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v and not os.environ.get(k.strip()):
            os.environ[k.strip()] = v


def saldo(s: requests.Session) -> dict:
    """Saldo del conto: disponibile + esposizione aperta.

    E' la base delle puntate (pre-registrazione: `bankroll.base_delle_puntate`).
    Betfair restituisce l'esposizione come numero negativo.
    """
    corpo = {"jsonrpc": "2.0", "method": "AccountAPING/v1.0/getAccountFunds",
             "params": {}, "id": 1}
    r = s.post(ACCOUNT, data=json.dumps(corpo), timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(f"getAccountFunds: {json.dumps(d['error'])}")
    f = d["result"]
    disponibile = float(f.get("availableToBetBalance") or 0)
    esposizione = abs(float(f.get("exposure") or 0))
    return {"disponibile": disponibile, "esposizione": esposizione,
            "totale": round(disponibile + esposizione, 2)}


def catalogo(s, da: timedelta, a: timedelta, tipi=("MATCH_ODDS", "OVER_UNDER_25")) -> list[dict]:
    """Mercati dei cinque campionati con calcio d'inizio fra adesso+da e adesso+a."""
    ora = datetime.now(timezone.utc)
    filtro = {"eventTypeIds": ["1"], "competitionIds": sorted(COMPETIZIONI),
              "marketTypeCodes": list(tipi),
              "marketStartTime": {"from": (ora + da).isoformat(),
                                  "to": (ora + a).isoformat()}}
    return rpc(s, "listMarketCatalogue",
               {"filter": filtro,
                "marketProjection": ["EVENT", "COMPETITION", "MARKET_START_TIME",
                                     "RUNNER_DESCRIPTION"],
                "sort": "FIRST_TO_START", "maxResults": 1000})


def libri(s, market_ids: list[str]) -> dict[str, dict]:
    """Miglior back e miglior lay, prezzi virtuali come sul sito. 40 mercati per richiesta."""
    out: dict[str, dict] = {}
    for i in range(0, len(market_ids), 40):
        for m in rpc(s, "listMarketBook",
                     {"marketIds": market_ids[i:i + 40],
                      "priceProjection": {"priceData": ["EX_BEST_OFFERS"],
                                          "virtualise": True}}):
            out[m["marketId"]] = m
    return out


def migliore(runner: dict, lato: str = "availableToBack") -> tuple[float | None, float | None]:
    """(quota, importo) della migliore offerta su un lato, solo se la selezione e' attiva."""
    if runner.get("status") != "ACTIVE":
        return None, None
    offerte = (runner.get("ex") or {}).get(lato) or []
    if not offerte:
        return None, None
    return offerte[0].get("price"), offerte[0].get("size")


class LogOrdini:
    """Richiesta e risposta di ogni chiamata che scrive su Betfair.

    Due copie: una su Supabase (`ordini_log`) e una locale in JSONL. Quella
    locale non dipende dalla rete verso il database: se Supabase non risponde
    dopo che un ordine e' partito, la traccia di cosa e' stato inviato resta.
    """

    def __init__(self, db, turno: str):
        self.db, self.turno = db, turno
        LOG_LOCALE.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.file = LOG_LOCALE / f"{datetime.now():%Y%m%d-%H%M%S}-{turno}.jsonl"

    def scrivi(self, operazione: str, market_id: str | None, richiesta, risposta,
               esito: str) -> None:
        riga = {"il": datetime.now(timezone.utc).isoformat(), "operazione": operazione,
                "turno": self.turno, "market_id": market_id, "richiesta": richiesta,
                "risposta": risposta, "esito": esito}
        with self.file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(riga, ensure_ascii=False, default=str) + "\n")
        try:
            self.db.insert("ordini_log", [{k: v for k, v in riga.items() if k != "il"}])
        except Exception as e:                       # noqa: BLE001
            print(f"[!] ordini_log su Supabase non scritto ({e}); copia locale in {self.file}")
