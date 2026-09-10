"""
Quote dall'exchange Betfair (betfair.it) — lettura, in parallelo a football-data.

Perche'. La selezione oggi usa `MaxH`: la quota massima fra ~20 bookmaker, in
apertura. E' il massimo di un campione — distorto all'insu' per costruzione —
di un allibratore qualsiasi, e non e' il prezzo dove la giocata verra'
eseguita. Leggere da Betfair allinea la misura all'esecuzione: l'edge diventa
«il modello batte il prezzo dove gioco» e il CLV diventa Betfair-presa contro
Betfair-chiusura, invece di un confronto fra due mercati diversi.

Il modulo e' anche la fonte del **calendario**: Betfair pubblica le partite
giorni prima di football-data (misurato il 10/9/2026 durante la pausa per le
nazionali: 55 partite contro 0), quindi il turno non deve piu' aspettare che
esca `fixtures.csv`. L'upsert usa la stessa chiave dell'ingest football-data,
percio' le due fonti confluiscono in una riga sola e `q_ap_max_*` resta
disponibile accanto a `q_bf_*` per confrontare i due prezzi.

SOLA LETTURA verso l'exchange: `listCompetitions`, `listMarketCatalogue`,
`listMarketBook`. Nessun `placeOrders` — le giocate restano registrate nel
ledger, la piazza reale e' una fase successiva da abilitare deliberatamente.

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
from src.ingest.squadre import (a_betfair, da_betfair,         # noqa: E402
                                verifica_betfair, verifica_inverso)

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
# Letti dall'API con --competizioni il 10/9/2026, non indovinati: un id
# sbagliato non da' errore, restituisce le partite di un altro campionato.
# Attenzione alle seconde divisioni, che hanno nomi quasi identici e id
# vicini: Bundesliga 2 e' 61, Ligue 2 e' 57, Segunda Division e' 12204313.
COMPETIZIONI: dict[str, str] = {
    "10932509": "E0",    # English Premier League
    "81": "I1",          # Italian Serie A
    "117": "SP1",        # Spanish La Liga
    "59": "D1",          # German Bundesliga
    "55": "F1",          # French Ligue 1
}

CREDENZIALI = ("BETFAIR_APP_KEY", "BETFAIR_USERNAME", "BETFAIR_PASSWORD",
               "BETFAIR_CERT", "BETFAIR_KEY")


class LocazioneVietata(RuntimeError):
    """Login rifiutato per la posizione geografica di chi chiama.

    Betfair verifica la provenienza al momento del login, non della giocata, e
    rifiuta le giurisdizioni proibite. I runner GitHub stanno in datacentre
    Azure prevalentemente negli Stati Uniti e la regione non e' selezionabile:
    da li' il login non passera' mai, con nessuna credenziale. Serve un host
    in Italia. Non e' una condizione da riprovare: e' un fatto della posizione.
    """


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
    stato = d.get("loginStatus")
    if stato == "BETTING_RESTRICTED_LOCATION":
        raise LocazioneVietata(stato)
    if stato != "SUCCESS":
        raise RuntimeError(f"login rifiutato: {stato}")

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


def _ora_locale(cat: dict) -> str | None:
    """Ora del calcio d'inizio nel fuso di Roma."""
    apertura = (cat.get("event") or {}).get("openDate")
    if not apertura:
        return None
    t = datetime.fromisoformat(apertura.replace("Z", "+00:00"))
    return t.astimezone(FUSO).time().isoformat()


def _tipo_mercato(nome: str) -> str | None:
    n = (nome or "").lower()
    if n.startswith("match odds"):
        return "1x2"
    return "ou25" if "2.5" in n else None


def fixtures_da_betfair(cat: list[dict], book: dict[str, dict],
                        squadre_valide: dict[str, set[str]],
                        ) -> tuple[list[dict], list[str]]:
    """Righe `fixtures` costruite dal catalogo Betfair.

    Betfair pubblica il calendario giorni prima di football-data: misurato il
    10/9/2026 durante la pausa per le nazionali, 55 partite contro 0. Il turno
    non deve piu' aspettare che esca `fixtures.csv`.

    I nomi vengono riportati a football-data e **rifiutati** se non risultano
    fra le squadre della stagione. Non e' pedanteria: la chiave unica della
    tabella e' (lega, data, casa, trasferta), quindi una riga inserita con un
    nome che football-data scrive diversamente non collide — crea una seconda
    riga per la stessa partita, e il turno potrebbe selezionare due volte lo
    stesso match. Meglio una partita mancante e segnalata che una doppia.

    L'upsert usa la stessa chiave dell'ingest football-data, quindi le due
    fonti confluiscono in una riga sola e `q_ap_max_*` resta disponibile per
    il confronto fra i due prezzi.
    """
    eventi: dict[tuple, dict] = {}
    for c in cat:
        ev, data = _evento(c), _data_locale(c)
        if not ev or not data:
            continue
        lega, casa_bf, trasferta_bf = ev
        eventi.setdefault((lega, data, casa_bf, trasferta_bf),
                          {})[c.get("marketName") or ""] = c

    righe: list[dict] = []
    scartati: list[str] = []
    for (lega, data, casa_bf, trasferta_bf), mercati_evento in sorted(eventi.items()):
        casa, trasferta = da_betfair(casa_bf), da_betfair(trasferta_bf)
        valide = squadre_valide.get(lega) or set()
        ignote = [n for n in (casa, trasferta) if n not in valide]
        if ignote:
            scartati.append(f"{lega} {data} {casa_bf} v {trasferta_bf}"
                            f"  -> sconosciuto a football-data: {', '.join(ignote)}")
            continue

        riga = {"lega": lega, "data": data,
                "ora": _ora_locale(next(iter(mercati_evento.values()))),
                "casa": casa, "trasferta": trasferta,
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
                    col = {casa_bf: "q_bf_1", trasferta_bf: "q_bf_2",
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

    return righe, scartati


def nomi_betfair(cat: list[dict]) -> dict[str, set[str]]:
    """Nomi squadra Betfair per lega, estratti dai nomi evento del catalogo."""
    out: dict[str, set[str]] = {}
    for c in cat:
        ev = _evento(c)
        if ev:
            lega, casa, trasferta = ev
            out.setdefault(lega, set()).update((casa, trasferta))
    return out


def nomi_football_data(db, stagione: str) -> dict[str, set[str]]:
    """Nomi squadra football-data per lega, dalle partite della stagione.

    Non dai fixtures: `fixtures.csv` e' vuoto durante le pause per le
    nazionali, mentre le squadre di una stagione sono tutte in `partite`.
    Mappare a partire dai fixtures coprirebbe solo le venti squadre del turno
    in arrivo, e la tabella dei nomi va riempita una volta per stagione.
    """
    out: dict[str, set[str]] = {}
    for r in db.select("partite", colonne="lega,casa,trasferta",
                       filtri={"stagione": f"eq.{stagione}"}):
        out.setdefault(r["lega"], set()).update((r["casa"], r["trasferta"]))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Quote Betfair Exchange sui fixtures")
    ap.add_argument("--competizioni", action="store_true",
                    help="elenca le competizioni di calcio con i loro id e esce")
    ap.add_argument("--nomi", action="store_true",
                    help="elenca i nomi non agganciati da mappare in ALIAS_BETFAIR")
    ap.add_argument("--giorni", type=int, default=10,
                    help="ampiezza in giorni della finestra di mercati da leggere")
    ap.add_argument("--stagione", default="2627",
                    help="stagione da cui leggere i nomi squadra per --nomi")
    ap.add_argument("--carica", action="store_true",
                    help="scrive i fixtures con le quote Betfair su Supabase")
    a = ap.parse_args(argv)

    try:
        s = sessione()
    except LocazioneVietata:
        print("[!] Betfair rifiuta il login da questa posizione "
              "(BETTING_RESTRICTED_LOCATION).")
        print("    La verifica e' sulla provenienza della richiesta, non sulle")
        print("    credenziali. I runner GitHub stanno in datacentre Azure")
        print("    prevalentemente negli Stati Uniti e la regione non si puo'")
        print("    scegliere: serve un host in Italia. Nessuna quota letta.")
        return 3

    if a.competizioni:
        righe = sorted(competizioni(s), key=lambda c: -int(c.get("marketCount") or 0))
        print(f"{'id':>10}  {'mercati':>7}  competizione")
        for c in righe[:60]:
            print(f"{c['competition']['id']:>10}  {c.get('marketCount', 0):>7}  "
                  f"{c['competition']['name']}")
        print("\nInserire i cinque id in COMPETIZIONI dentro src/ingest/betfair.py")
        return 0

    db = client()
    cat = mercati(s, a.giorni)

    if a.nomi:
        # Confronto per lega, non globale: e' l'unico modo di non proporre
        # come candidato un nome di un altro campionato.
        bf = nomi_betfair(cat)
        fd = nomi_football_data(db, a.stagione)
        for lega in sorted(fd):
            da_mappare = verifica_betfair(fd[lega], bf.get(lega, set()))
            liberi_lega = sorted(bf.get(lega, set()) -
                                 {a_betfair(n) for n in fd[lega]})
            print(f"\n=== {lega} — {len(fd[lega])} squadre football-data, "
                  f"{len(bf.get(lega, set()))} nomi Betfair visti ===")
            if not da_mappare:
                print("    tutte agganciate")
                continue
            print(f"    da mappare ({len(da_mappare)}):")
            for n in da_mappare:
                print(f'        "{n}": "",')
            print(f"    nomi Betfair liberi in questa lega ({len(liberi_lega)}):")
            for n in liberi_lega:
                print(f"        {n}")
        return 0

    # L'inversione dei nomi regge solo se ALIAS_BETFAIR e' iniettivo: due
    # squadre sullo stesso nome Betfair ne farebbero perdere una in silenzio.
    collisioni = verifica_inverso()
    if collisioni:
        print("[!] RIFIUTATO: ALIAS_BETFAIR non e' iniettivo, "
              f"nomi con piu' di una corrispondenza: {', '.join(collisioni)}")
        return 2

    squadre_valide = nomi_football_data(db, a.stagione)
    if not squadre_valide:
        print(f"nessuna partita in `partite` per la stagione {a.stagione}: "
              "senza l'elenco squadre non si puo' validare nessun nome")
        return 1

    book = prezzi(s, sorted({c["marketId"] for c in cat}))
    righe, scartati = fixtures_da_betfair(cat, book, squadre_valide)

    print(f"mercati letti: {len(book)} | partite: {len(righe)} "
          f"| scartate: {len(scartati)}")
    if scartati:
        print("\n[!] partite scartate, nome non riconducibile a football-data:")
        for x in scartati:
            print("   ", x)
        print("    Aggiungere la mappatura con `--nomi` e rilanciare. Una riga")
        print("    inserita col nome sbagliato duplicherebbe la partita.")

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
    print(f"\nscritti {n} fixtures con le quote Betfair")
    db.log("betfair", "ok", n, f"{len(scartati)} scartati")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
