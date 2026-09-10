"""
Quote dall'exchange Betfair (betfair.it) — lettura, in parallelo a football-data.

Perche'. La selezione oggi usa `MaxH`: la quota massima fra ~20 bookmaker, in
apertura. E' il massimo di un campione — distorto all'insu' per costruzione —
di un allibratore qualsiasi, e non e' il prezzo dove la giocata verra'
eseguita. Leggere da Betfair allinea la misura all'esecuzione: l'edge diventa
«il modello batte il prezzo dove gioco» e il CLV diventa Betfair-presa contro
Betfair-chiusura, invece di un confronto fra due mercati diversi.

Questo modulo NON cambia la selezione. Scrive `q_bf_*` accanto alle quote
football-data sui fixtures e si ferma. Il confronto fra le due serie e' il
dato che serve per decidere se cambiare criterio, e va raccolto prima.

Chiave. La **Delayed App Key** basta: e' gratuita, opera sull'exchange reale e
permette anche di scrivere ordini; i prezzi arrivano in snapshot ritardati fra
1 e 180 secondi, irrilevante su mercati pre-match 1X2/Over-Under letti il
venerdi' per il weekend. La Live App Key (tempo reale) costa 499 GBP una
tantum e servirebbe solo scendendo a orizzonti brevi. Con la Delayed manca il
volume scambiato: la size in `bf_raw` e' quella *disponibile*, non lo scambiato.

Prerequisiti sul conto, una volta sola:
  1. betfair.it -> I miei dati -> abilitare l'accesso non interattivo (bot)
     e caricare il certificato self-signed;
  2. creare le App Key con createDeveloperAppKeys (Accounts API Demo Tool).

Ambiente:
  BETFAIR_APP_KEY   la Delayed App Key
  BETFAIR_USERNAME  utenza betfair.it
  BETFAIR_PASSWORD  password
  BETFAIR_CERT      percorso del .crt
  BETFAIR_KEY       percorso del .key

Uso:
  python -m src.ingest.betfair --competizioni   # id delle leghe, per COMPETIZIONI
  python -m src.ingest.betfair --nomi           # nomi non agganciati, per ALIAS_BETFAIR
  python -m src.ingest.betfair                  # anteprima quote del turno
  python -m src.ingest.betfair --carica         # scrive q_bf_* sui fixtures
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.db.client import client                              # noqa: E402
from src.ingest.squadre import a_betfair, verifica_betfair     # noqa: E402

# Login sul dominio italiano: l'exchange italiano ha liquidita' separata da
# quello internazionale. Ottenuto il token, le richieste di betting vanno
# comunque agli endpoint .com, che restituiscono i mercati visibili a un conto
# italiano. (betfair.com non e' un'alternativa: non accetta residenti fiscali
# in Italia ed e' inibito da ADM.)
LOGIN = "https://identitysso-cert.betfair.it/api/certlogin"
RPC = "https://api.betfair.com/exchange/betting/json-rpc/v1"
TIMEOUT = 30
FUSO = ZoneInfo("Europe/Rome")

# Vincoli dell'exchange italiano e costo della piazza.
COMMISSIONE = 0.045        # sulle vincite nette, per mercato, senza sconti
STAKE_MIN = 2.00           # 200 centesimi
STAKE_PASSO = 0.50         # multipli di 50 centesimi

# Il limite e' somma(peso) * n_mercati <= 200 punti per richiesta ed
# EX_BEST_OFFERS pesa 5: oltre 40 mercati l'API risponde TOO_MUCH_DATA.
MERCATI_PER_RICHIESTA = 200 // 5

# competitionId Betfair -> codice lega football-data.
#
# VUOTO APPOSTA: gli id non si indovinano e un id sbagliato non da' errore,
# restituisce le partite di un altro campionato. Si riempie con --competizioni.
COMPETIZIONI: dict[str, str] = {}

CREDENZIALI = ("BETFAIR_APP_KEY", "BETFAIR_USERNAME", "BETFAIR_PASSWORD",
               "BETFAIR_CERT", "BETFAIR_KEY")


def quota_netta(q: float, commissione: float = COMMISSIONE) -> float:
    """Quota equivalente al netto della commissione sulle vincite nette.

    Su un exchange la commissione si paga solo sul profitto, quindi una quota
    2,10 al 4,5% rende come una 2,0505 senza commissione. Usare la quota lorda
    nel calcolo dell'edge sovrastima il vantaggio di (q-1)*commissione.
    """
    return 1.0 + (q - 1.0) * (1.0 - commissione)


def arrotonda_stake(s: float) -> float:
    """Stake valido su betfair.it: multipli di 50 centesimi, minimo 2,00 EUR.

    Arrotonda per DIFETTO. Salire al multiplo successivo gonfierebbe lo stake
    oltre il cap di Kelly, che e' un limite superiore; scendere lo rende solo
    piu' prudente. Sotto il minimo ritorna 0: la giocata non si fa.
    """
    s = math.floor(s / STAKE_PASSO + 1e-9) * STAKE_PASSO
    return round(s, 2) if s >= STAKE_MIN else 0.0


def sessione() -> requests.Session:
    """Login non interattivo con certificato; ritorna la sessione autenticata."""
    mancanti = [v for v in CREDENZIALI if not os.environ.get(v)]
    if mancanti:
        raise RuntimeError("variabili d'ambiente mancanti: " + ", ".join(mancanti))
    cert, chiave = os.environ["BETFAIR_CERT"], os.environ["BETFAIR_KEY"]
    for f in (cert, chiave):
        if not Path(f).is_file():
            raise RuntimeError(f"file del certificato non trovato: {f}")

    r = requests.post(LOGIN,
                      headers={"X-Application": os.environ["BETFAIR_APP_KEY"]},
                      data={"username": os.environ["BETFAIR_USERNAME"],
                            "password": os.environ["BETFAIR_PASSWORD"]},
                      cert=(cert, chiave), timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    if d.get("loginStatus") != "SUCCESS":
        raise RuntimeError(f"login rifiutato: {d.get('loginStatus')}")

    s = requests.Session()
    s.headers.update({"X-Application": os.environ["BETFAIR_APP_KEY"],
                      "X-Authentication": d["sessionToken"],
                      "Content-Type": "application/json"})
    return s


def rpc(s: requests.Session, metodo: str, params: dict):
    corpo = {"jsonrpc": "2.0", "method": f"SportsAPING/v1.0/{metodo}",
             "params": params, "id": 1}
    r = s.post(RPC, data=json.dumps(corpo), timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(f"{metodo}: {json.dumps(d['error'])}")
    return d["result"]


def competizioni(s: requests.Session) -> list[dict]:
    """Tutte le competizioni di calcio visibili al conto, con id e n. mercati."""
    return rpc(s, "listCompetitions", {"filter": {"eventTypeIds": ["1"]}})


def mercati(s: requests.Session, giorni: int) -> list[dict]:
    """Catalogo 1X2 + Over/Under 2.5 delle leghe in COMPETIZIONI."""
    if not COMPETIZIONI:
        raise RuntimeError(
            "COMPETIZIONI e' vuoto: lanciare `--competizioni`, individuare le "
            "cinque leghe e riempire il dizionario in questo file.")
    ora = datetime.now(timezone.utc)
    filtro = {"eventTypeIds": ["1"],
              "competitionIds": sorted(COMPETIZIONI),
              "marketTypeCodes": ["MATCH_ODDS", "OVER_UNDER_25"],
              "marketStartTime": {"from": ora.isoformat(),
                                  "to": (ora + timedelta(days=giorni)).isoformat()}}
    return rpc(s, "listMarketCatalogue",
               {"filter": filtro,
                "marketProjection": ["EVENT", "COMPETITION", "MARKET_START_TIME",
                                     "RUNNER_DESCRIPTION"],
                "sort": "FIRST_TO_START", "maxResults": 1000})


def prezzi(s: requests.Session, market_ids: list[str]) -> dict[str, dict]:
    """Miglior back per mercato, a blocchi per non superare i 200 punti."""
    out: dict[str, dict] = {}
    for i in range(0, len(market_ids), MERCATI_PER_RICHIESTA):
        blocco = market_ids[i:i + MERCATI_PER_RICHIESTA]
        for m in rpc(s, "listMarketBook",
                     {"marketIds": blocco,
                      "priceProjection": {"priceData": ["EX_BEST_OFFERS"],
                                          "virtualise": True}}):
            out[m["marketId"]] = m
    return out


def _back(runner: dict) -> tuple[float | None, float | None]:
    """Prezzo e size del miglior back, solo se la selezione e' attiva."""
    if runner.get("status") != "ACTIVE":
        return None, None
    offerte = (runner.get("ex") or {}).get("availableToBack") or []
    if not offerte:
        return None, None
    return offerte[0].get("price"), offerte[0].get("size")


def _evento(cat: dict) -> tuple[str, str, str] | None:
    """(lega football-data, casa Betfair, trasferta Betfair) dal catalogo."""
    lega = COMPETIZIONI.get((cat.get("competition") or {}).get("id"))
    nome = (cat.get("event") or {}).get("name") or ""
    if not lega or " v " not in nome:
        return None
    casa, trasferta = nome.split(" v ", 1)
    return lega, casa.strip(), trasferta.strip()


def _data_locale(cat: dict) -> str | None:
    """Data del calcio d'inizio nel fuso di Roma, come la scrive football-data."""
    apertura = (cat.get("event") or {}).get("openDate")
    if not apertura:
        return None
    t = datetime.fromisoformat(apertura.replace("Z", "+00:00"))
    return t.astimezone(FUSO).date().isoformat()


def _tipo_mercato(nome: str) -> str | None:
    n = (nome or "").lower()
    if n.startswith("match odds"):
        return "1x2"
    return "ou25" if "2.5" in n else None


def aggancia(cat: list[dict], book: dict[str, dict],
             fx: list[dict]) -> tuple[list[dict], list[dict], list[str]]:
    """Unisce catalogo e quote ai fixtures football-data.

    L'aggancio e' su (lega, data locale, casa, trasferta) con i nomi tradotti
    da ALIAS_BETFAIR: nessun fuzzy matching, per la ragione scritta in
    squadre.py. Ritorna le righe pronte per l'upsert, i fixtures non agganciati
    e i nomi evento Betfair rimasti liberi.
    """
    per_chiave: dict[tuple, dict] = {}
    liberi: dict[tuple, str] = {}
    for c in cat:
        ev, data = _evento(c), _data_locale(c)
        if not ev or not data:
            continue
        lega, casa, trasferta = ev
        chiave = (lega, data, casa, trasferta)
        liberi[chiave] = f"{casa} v {trasferta}"
        per_chiave.setdefault(chiave, {})[c.get("marketName") or ""] = c

    righe: list[dict] = []
    orfani: list[dict] = []
    for f in fx:
        chiave = (f["lega"], str(f["data"]),
                  a_betfair(f["casa"]), a_betfair(f["trasferta"]))
        mercati_evento = per_chiave.get(chiave)
        if not mercati_evento:
            orfani.append(f)
            continue
        liberi.pop(chiave, None)

        riga = {"lega": f["lega"], "data": str(f["data"]),
                "casa": f["casa"], "trasferta": f["trasferta"],
                "bf_letto_il": datetime.now(timezone.utc).isoformat()}
        raw: dict[str, dict] = {}

        for nome, cat_m in mercati_evento.items():
            tipo = _tipo_mercato(nome)
            libro = book.get(cat_m["marketId"])
            if tipo is None or not libro:
                continue
            riga[f"bf_market_{tipo}"] = cat_m["marketId"]
            per_id = {r["selectionId"]: r for r in libro.get("runners", [])}

            for desc in cat_m.get("runners", []):
                r = per_id.get(desc["selectionId"])
                if not r:
                    continue
                etichetta = (desc.get("runnerName") or "").strip()
                if tipo == "1x2":
                    col = {chiave[2]: "q_bf_1", chiave[3]: "q_bf_2",
                           "The Draw": "q_bf_x"}.get(etichetta)
                elif etichetta.lower().startswith("over"):
                    col = "q_bf_over25"
                elif etichetta.lower().startswith("under"):
                    col = "q_bf_under25"
                else:
                    col = None
                if col is None:
                    continue
                q, size = _back(r)
                riga[col] = q
                raw[col] = {"selezione": etichetta, "quota": q, "size": size}

        riga["bf_raw"] = raw
        righe.append(riga)

    return righe, orfani, sorted(liberi.values())


def _etichetta(f: dict) -> str:
    return f"{f['lega']} {f['data']} {f['casa']} - {f['trasferta']}"


def _fixtures_futuri(db) -> list[dict]:
    oggi = datetime.now(FUSO).date().isoformat()
    return db.select("fixtures", colonne="lega,data,casa,trasferta",
                     filtri={"data": f"gte.{oggi}"}, ordina="data.asc")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Quote Betfair Exchange sui fixtures")
    ap.add_argument("--competizioni", action="store_true",
                    help="elenca le competizioni di calcio con i loro id e esce")
    ap.add_argument("--nomi", action="store_true",
                    help="elenca i nomi non agganciati da mappare in ALIAS_BETFAIR")
    ap.add_argument("--giorni", type=int, default=10,
                    help="ampiezza in giorni della finestra di mercati da leggere")
    ap.add_argument("--carica", action="store_true", help="scrive q_bf_* sui fixtures")
    a = ap.parse_args(argv)

    s = sessione()

    if a.competizioni:
        righe = sorted(competizioni(s), key=lambda c: -int(c.get("marketCount") or 0))
        print(f"{'id':>10}  {'mercati':>7}  competizione")
        for c in righe[:60]:
            print(f"{c['competition']['id']:>10}  {c.get('marketCount', 0):>7}  "
                  f"{c['competition']['name']}")
        print("\nInserire i cinque id in COMPETIZIONI dentro src/ingest/betfair.py")
        return 0

    db = client()
    fx = _fixtures_futuri(db)
    if not fx:
        print("nessun fixture futuro in tabella: lanciare prima football_data --fixtures")
        return 1

    cat = mercati(s, a.giorni)
    book = prezzi(s, sorted({c["marketId"] for c in cat}))
    righe, orfani, liberi = aggancia(cat, book, fx)

    if a.nomi:
        print(f"fixtures non agganciati: {len(orfani)}")
        for o in orfani:
            print("   ", _etichetta(o))
        print(f"\nnomi evento Betfair rimasti liberi: {len(liberi)}")
        for nome in liberi:
            print("   ", nome)
        # I nomi da mappare sono solo quelli dei fixtures rimasti orfani: quelli
        # gia' agganciati sono stati rimossi da `liberi`, quindi includerli
        # farebbe segnalare come mancanti anche le mappature corrette.
        nomi_bf = {p.strip() for nome in liberi for p in nome.split(" v ")}
        nomi_fd = {n for o in orfani for n in (o["casa"], o["trasferta"])}
        print("\nrighe da aggiungere ad ALIAS_BETFAIR (il valore va scelto a mano):")
        for n in verifica_betfair(nomi_fd, nomi_bf):
            print(f'    "{n}": "",')
        return 0

    print(f"mercati letti: {len(book)} | fixtures agganciati: {len(righe)} "
          f"| non agganciati: {len(orfani)}")
    if orfani:
        print("\n[!] senza quote Betfair (nome da mappare o partita assente sull'exchange):")
        for o in orfani[:10]:
            print("   ", _etichetta(o))
        print("    `--nomi` stampa le righe da aggiungere ad ALIAS_BETFAIR.")

    print(f"\n{'partita':44} {'1':>6} {'X':>6} {'2':>6} {'O2.5':>6} {'U2.5':>6}")
    for r in righe:
        etichetta = f"{r['casa'][:19]} - {r['trasferta'][:19]}"
        print(f"{etichetta:44} " + " ".join(
            f"{r.get(c) or 0:6.2f}" for c in
            ("q_bf_1", "q_bf_x", "q_bf_2", "q_bf_over25", "q_bf_under25")))

    if not a.carica:
        print("\n(anteprima — rilanciare con --carica per scrivere su Supabase)")
        return 0

    n = db.upsert("fixtures", righe, on_conflict="lega,data,casa,trasferta")
    print(f"\nscritte quote Betfair su {n} fixtures")
    db.log("betfair", "ok", n, f"{len(orfani)} non agganciati")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
