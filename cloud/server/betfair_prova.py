"""
Prove del collegamento con betfair.it, da lanciare sul server italiano.

    python betfair_prova.py connessione       # sola lettura: login, saldo, mercati
    python betfair_prova.py ordine            # anteprima dell'ordine di prova, NON invia nulla
    python betfair_prova.py ordine --esegui   # invia l'ordine di prova e lo annulla subito

Le due prove rispondono a due domande diverse, in quest'ordine:

1. `connessione` — Betfair accetta il login da QUESTO server? E' la domanda
   sull'IP: dai runner di GitHub la risposta e' BETTING_RESTRICTED_LOCATION.
   Non scrive niente, ne' su Betfair ne' su Supabase.

2. `ordine` — questo conto, con QUESTA chiave, puo' piazzare un ordine
   sull'exchange italiano? Punta 2,00 EUR a quota 1000 sul favorito di una
   partita dei cinque campionati. A quota 1000 sul favorito nessuno banca,
   quindi l'ordine resta non abbinato e viene annullato subito dopo. Rischio
   massimo, nel caso assurdo in cui venisse abbinato: i 2 EUR della puntata.

Indipendente dal resto del repo: serve solo `requests`.

Credenziali: variabili BETFAIR_* nell'ambiente oppure ~/.betfair/betfair.env.
Lo script non le stampa mai e non le scrive nei log.

Log: richiesta e risposta di ogni chiamata che scrive (placeOrders,
cancelOrders) in ~/.betfair/prove/AAAAMMGG-hhmmss.jsonl. Saranno la prima
riga di quello che diventera' `ordini_log`.

Codici di uscita:
  0  prova riuscita
  2  rifiutata dallo script per sicurezza (nessun ordine inviato)
  3  Betfair rifiuta la posizione geografica del server
  4  login non riuscito (credenziali, certificato, chiave)
  5  ordine respinto da Betfair (nessuna esposizione)
  6  ANNULLAMENTO NON CONFERMATO: controllare subito su betfair.it
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

LOGIN = "https://identitysso-cert.betfair.it/api/certlogin"
BETTING = "https://api.betfair.com/exchange/betting/json-rpc/v1"
ACCOUNT = "https://api.betfair.com/exchange/account/json-rpc/v1"
TIMEOUT = 30
FUSO = ZoneInfo("Europe/Rome")
CARTELLA = Path.home() / ".betfair"
FILE_ENV = CARTELLA / "betfair.env"
CREDENZIALI = ("BETFAIR_APP_KEY", "BETFAIR_USERNAME", "BETFAIR_PASSWORD",
               "BETFAIR_CERT", "BETFAIR_KEY")

# Stessi id di src/ingest/betfair.py, letti dall'API il 10/9/2026.
COMPETIZIONI = {"10932509": "E0", "81": "I1", "117": "SP1", "59": "D1", "55": "F1"}

# L'ordine di prova. 2,00 EUR e' il minimo dell'exchange italiano; 1000 e'
# l'ultimo gradino della scala dei prezzi. Vincita potenziale 2.000 EUR, sotto
# il tetto di 10.000 EUR imposto da ADM.
STAKE_PROVA = 2.00
QUOTA_PROVA = 1000.0
# Solo partite che iniziano fra almeno 2 ore: nessun rischio di mercato che
# passa in-play durante la prova.
ANTICIPO = timedelta(hours=2)
# Il favorito deve davvero esserlo, e nessuno deve offrire di bancarlo a
# prezzi vicini a quello della prova: altrimenti l'ordine si abbinerebbe.
FAVORITO_MAX = 3.0
OFFERTA_MAX = 100.0
# EX_BEST_OFFERS pesa 5 punti, il limite e' 200 per richiesta.
MERCATI_PER_RICHIESTA = 40

SPIEGAZIONI = {
    "BETTING_RESTRICTED_LOCATION": "Betfair non accetta richieste dall'IP di questo server. "
        "Non dipende dalle credenziali: serve un server con un IP localizzato in Italia.",
    "INVALID_USERNAME_OR_PASSWORD": "utente o password errati.",
    "CERT_AUTH_REQUIRED": "il certificato non e' stato accettato: controllare che "
        "client.crt sia quello caricato su betfair.it e che client.key sia la sua chiave.",
    "ACCOUNT_NOW_LOCKED": "conto bloccato per troppi tentativi: aspettare e accedere dal sito.",
    "ACCOUNT_ALREADY_LOCKED": "conto bloccato: accedere dal sito per sbloccarlo.",
    "KYC_SUSPEND": "verifica dell'identita' (KYC) non completata sul conto.",
    "SUSPENDED": "conto sospeso.",
    "INVALID_APP_KEY": "App Key non valida per questo conto: va creata sul conto betfair.it.",
    "NO_APP_KEY": "App Key mancante.",
    "PERMISSION_DENIED": "permesso negato: con ogni probabilita' questa chiave o questo conto "
        "non possono piazzare ordini.",
    "INSUFFICIENT_FUNDS": "saldo insufficiente per la puntata di prova.",
    "INVALID_BET_SIZE": "importo non ammesso (minimo 2,00 EUR, multipli di 0,50).",
    "INVALID_ODDS": "quota non valida sulla scala dei prezzi Betfair.",
    "REJECTED_BY_REGULATOR": "respinto dal regolatore (ADM).",
    "REGULATOR_IS_NOT_AVAILABLE": "il sistema del regolatore (ADM) non risponde: riprovare piu' tardi.",
    "MARKET_SUSPENDED": "mercato sospeso in questo momento.",
    "MARKET_NOT_OPEN_FOR_BETTING": "mercato non aperto alle scommesse.",
    "LOSS_LIMIT_EXCEEDED": "superato il limite di perdita impostato sul conto.",
    "INVALID_ACCOUNT_STATE": "stato del conto non valido per scommettere.",
}


class Rifiuto(Exception):
    """Lo script si ferma prima di inviare qualunque ordine."""


class ErroreLogin(Exception):
    def __init__(self, stato: str):
        super().__init__(stato)
        self.stato = stato


class ErroreApi(Exception):
    def __init__(self, metodo: str, codice: str, grezzo: dict):
        super().__init__(f"{metodo}: {codice}")
        self.metodo, self.codice, self.grezzo = metodo, codice, grezzo


GIORNI = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")


def quando(iso: str) -> str:
    """'2026-10-10T16:00:00.000Z' -> 'sab 10/10 18:00' (ora italiana)."""
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(FUSO)
    return f"{GIORNI[t.weekday()]} {t:%d/%m %H:%M}"


def spiega(codice: str | None) -> str:
    return SPIEGAZIONI.get(codice or "", "")


# --------------------------------------------------------------------- setup

def carica_env(percorso: Path = FILE_ENV) -> None:
    """Legge KEY=VALUE da betfair.env senza sovrascrivere l'ambiente."""
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


def controlla_credenziali() -> None:
    mancanti = [v for v in CREDENZIALI if not os.environ.get(v)]
    if mancanti:
        raise Rifiuto(f"mancano {', '.join(mancanti)}: compilare {FILE_ENV}")
    for v in ("BETFAIR_CERT", "BETFAIR_KEY"):
        f = Path(os.environ[v])
        if not f.is_file():
            raise Rifiuto(f"{v}: file non trovato ({f})")
        if f.stat().st_mode & 0o077:
            print(f"[!] {f} e' leggibile da altri utenti: chmod 600 {f}")


def ip_pubblico() -> tuple[str | None, str | None]:
    """IP del server e paese secondo ipinfo. Solo indicativo: Betfair usa MaxMind."""
    try:
        ip = requests.get("https://api.ipify.org", timeout=10).text.strip()
    except requests.RequestException:
        return None, None
    try:
        paese = requests.get(f"https://ipinfo.io/{ip}/country", timeout=10).text.strip()
    except requests.RequestException:
        paese = None
    return ip, paese


# ------------------------------------------------------------------ Betfair

def login() -> requests.Session:
    """Login non interattivo con certificato sul dominio italiano."""
    chiave = os.environ["BETFAIR_APP_KEY"]
    r = requests.post(LOGIN,
                      headers={"X-Application": chiave},
                      data={"username": os.environ["BETFAIR_USERNAME"],
                            "password": os.environ["BETFAIR_PASSWORD"]},
                      cert=(os.environ["BETFAIR_CERT"], os.environ["BETFAIR_KEY"]),
                      timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    if d.get("loginStatus") != "SUCCESS":
        raise ErroreLogin(d.get("loginStatus") or "risposta senza loginStatus")
    s = requests.Session()
    s.headers.update({"X-Application": chiave,
                      "X-Authentication": d["sessionToken"],
                      "Content-Type": "application/json",
                      "Accept": "application/json"})
    return s


def rpc(s: requests.Session, metodo: str, params: dict):
    url, prefisso = ((ACCOUNT, "AccountAPING") if metodo == "getAccountFunds"
                     else (BETTING, "SportsAPING"))
    corpo = {"jsonrpc": "2.0", "method": f"{prefisso}/v1.0/{metodo}",
             "params": params, "id": 1}
    r = s.post(url, data=json.dumps(corpo), timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        err = d["error"]
        codice = ((err.get("data") or {}).get("APINGException") or {}).get("errorCode") \
            or ((err.get("data") or {}).get("AccountAPINGException") or {}).get("errorCode") \
            or str(err.get("message") or err.get("code"))
        raise ErroreApi(metodo, codice, err)
    return d["result"]


def catalogo(s, giorni: int, tipi=("MATCH_ODDS",), massimo: int = MERCATI_PER_RICHIESTA):
    ora = datetime.now(timezone.utc)
    filtro = {"eventTypeIds": ["1"], "competitionIds": sorted(COMPETIZIONI),
              "marketTypeCodes": list(tipi),
              "marketStartTime": {"from": (ora + ANTICIPO).isoformat(),
                                  "to": (ora + timedelta(days=giorni)).isoformat()}}
    return rpc(s, "listMarketCatalogue",
               {"filter": filtro,
                "marketProjection": ["EVENT", "COMPETITION", "MARKET_START_TIME",
                                     "RUNNER_DESCRIPTION"],
                "sort": "FIRST_TO_START", "maxResults": massimo})


def libri(s, market_ids: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(market_ids), MERCATI_PER_RICHIESTA):
        for m in rpc(s, "listMarketBook",
                     {"marketIds": market_ids[i:i + MERCATI_PER_RICHIESTA],
                      "priceProjection": {"priceData": ["EX_BEST_OFFERS"],
                                          "virtualise": True}}):
            out[m["marketId"]] = m
    return out


def miglior_back(runner: dict) -> float | None:
    """Quota piu' alta a cui si puo' puntare adesso (availableToBack[0])."""
    offerte = (runner.get("ex") or {}).get("availableToBack") or []
    prezzi = [o.get("price") for o in offerte if o.get("price")]
    return max(prezzi) if prezzi else None


def scegli_mercato(cat: list[dict], book: dict[str, dict], adesso: datetime):
    """Primo mercato 1X2 adatto alla prova e il suo favorito.

    Requisiti, tutti necessari:
      - mercato OPEN, non in-play, calcio d'inizio fra almeno ANTICIPO;
      - tre selezioni attive, tutte con un prezzo di back;
      - favorito con miglior back <= FAVORITO_MAX;
      - nessuna offerta sul favorito sopra OFFERTA_MAX, cosi' una puntata a
        QUOTA_PROVA non puo' trovare controparte.
    Ritorna (catalogo, runner_catalogo, runner_book, miglior_back) o None.
    """
    for c in cat:
        b = book.get(c["marketId"])
        if not b or b.get("status") != "OPEN" or b.get("inplay"):
            continue
        inizio = datetime.fromisoformat(c["marketStartTime"].replace("Z", "+00:00"))
        if inizio - adesso < ANTICIPO:
            continue
        attivi = [r for r in b.get("runners", []) if r.get("status") == "ACTIVE"]
        if len(attivi) != 3:
            continue
        prezzi = {r["selectionId"]: miglior_back(r) for r in attivi}
        if any(p is None for p in prezzi.values()):
            continue
        fav_id = min(prezzi, key=prezzi.get)
        fav = next(r for r in attivi if r["selectionId"] == fav_id)
        if prezzi[fav_id] > FAVORITO_MAX:
            continue
        if miglior_back(fav) > OFFERTA_MAX:
            continue
        desc = next((r for r in c.get("runners", []) if r["selectionId"] == fav_id), None)
        if desc is None:
            continue
        return c, desc, fav, prezzi[fav_id]
    return None


# --------------------------------------------------------------------- log

class Registro:
    def __init__(self):
        cartella = CARTELLA / "prove"
        cartella.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.file = cartella / datetime.now(FUSO).strftime("%Y%m%d-%H%M%S.jsonl")

    def scrivi(self, operazione: str, richiesta, risposta) -> None:
        with self.file.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"il": datetime.now(timezone.utc).isoformat(),
                                "operazione": operazione, "richiesta": richiesta,
                                "risposta": risposta}, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------- prove

def saldo(s) -> dict:
    f = rpc(s, "getAccountFunds", {})
    return {"disponibile": f.get("availableToBetBalance"),
            "esposizione": f.get("exposure"),
            "wallet": f.get("wallet")}


def prova_connessione(a) -> int:
    ip, paese = ip_pubblico()
    print(f"IP del server: {ip or '?'}  paese secondo ipinfo: {paese or '?'} "
          "(indicativo: Betfair usa MaxMind)")

    s = login()
    print("login betfair.it: OK")

    f = saldo(s)
    print(f"saldo disponibile: {f['disponibile']} EUR | esposizione: {f['esposizione']} "
          f"| wallet: {f['wallet']}")

    visibili = {c["competition"]["id"]: c.get("marketCount", 0)
                for c in rpc(s, "listCompetitions", {"filter": {"eventTypeIds": ["1"]}})}
    print("\ncompetizioni:")
    for cid, lega in COMPETIZIONI.items():
        stato = f"{visibili[cid]} mercati" if cid in visibili else "NON VISIBILE"
        print(f"  {lega:4} id {cid:>9}  {stato}")

    cat = catalogo(s, a.giorni, tipi=("MATCH_ODDS", "OVER_UNDER_25"), massimo=200)
    per_lega: dict[str, int] = {}
    for c in cat:
        lega = COMPETIZIONI.get((c.get("competition") or {}).get("id"), "?")
        per_lega[lega] = per_lega.get(lega, 0) + 1
    print(f"\nmercati 1X2 + O/U 2.5 nei prossimi {a.giorni} giorni: {len(cat)} "
          + " ".join(f"{k}:{v}" for k, v in sorted(per_lega.items())))

    esa = [c for c in cat if (c.get("marketName") or "").lower().startswith("match odds")][:6]
    if esa:
        book = libri(s, [c["marketId"] for c in esa])
        ritardo = {b.get("isMarketDataDelayed") for b in book.values()}
        tipo = {frozenset({True}): "si' -> Delayed App Key",
                frozenset({False}): "no -> Live App Key"}.get(frozenset(ritardo), "misto")
        print(f"prezzi in ritardo: {tipo}")
        print(f"\n{'partita':42} {'inizio':16} {'1':>6} {'X':>6} {'2':>6}")
        for c in esa:
            b = book.get(c["marketId"]) or {}
            per_id = {r["selectionId"]: r for r in b.get("runners", [])}
            # Per nome, non per posizione: su Betfair l'ordine e' casa,
            # trasferta, pareggio.
            casa, trasf = (x.strip() for x in c["event"]["name"].split(" v ", 1))
            per_nome = {r.get("runnerName"): r["selectionId"] for r in c["runners"]}
            q = [miglior_back(per_id.get(per_nome.get(n), {})) or 0
                 for n in (casa, "The Draw", trasf)]
            print(f"{c['event']['name'][:42]:42} {quando(c['marketStartTime']):16} "
                  + " ".join(f"{x:6.2f}" for x in q[:3]))

    print("\nCONNESSIONE OK: da questo server Betfair accetta login e lettura.")
    print("Prossimo passo: python betfair_prova.py ordine   (anteprima, non invia nulla)")
    return 0


def prova_ordine(a) -> int:
    s = login()
    prima = saldo(s)
    print(f"saldo disponibile: {prima['disponibile']} EUR | esposizione: {prima['esposizione']}")
    if (prima["disponibile"] or 0) < STAKE_PROVA:
        raise Rifiuto(f"servono almeno {STAKE_PROVA:.2f} EUR disponibili sul conto")

    adesso = datetime.now(timezone.utc)
    cat = catalogo(s, a.giorni)
    if not cat:
        raise Rifiuto(f"nessun mercato 1X2 dei cinque campionati nei prossimi {a.giorni} "
                      "giorni (pausa per le nazionali?): riprovare con --giorni piu' ampio")
    book = libri(s, [c["marketId"] for c in cat])
    scelta = scegli_mercato(cat, book, adesso)
    if scelta is None:
        raise Rifiuto("nessun mercato soddisfa i requisiti di sicurezza della prova")
    c, desc, fav, back = scelta

    istruzione = {"selectionId": desc["selectionId"], "handicap": 0, "side": "BACK",
                  "orderType": "LIMIT",
                  "limitOrder": {"size": STAKE_PROVA, "price": QUOTA_PROVA,
                                 "persistenceType": "LAPSE"}}
    rif = "pgw-prova-" + adesso.strftime("%y%m%d%H%M%S")
    richiesta = {"marketId": c["marketId"], "instructions": [istruzione], "customerRef": rif}

    print(f"\nmercato:   {c['event']['name']} — {c.get('marketName')} "
          f"({COMPETIZIONI.get(c['competition']['id'])})")
    print(f"inizio:    {quando(c['marketStartTime'])}")
    print(f"selezione: {desc['runnerName']}  (favorito, miglior back adesso {back})")
    print(f"ordine:    PUNTA {STAKE_PROVA:.2f} EUR a quota {QUOTA_PROVA:.0f}, LIMIT, LAPSE")
    print(f"rischio:   massimo {STAKE_PROVA:.2f} EUR, solo se qualcuno bancasse il "
          f"favorito a {QUOTA_PROVA:.0f}; l'ordine viene annullato subito dopo l'invio")

    if not a.esegui:
        print("\nANTEPRIMA: nessun ordine inviato. Per inviarlo davvero:")
        print("  python betfair_prova.py ordine --esegui")
        return 0

    if not sys.stdin.isatty():
        raise Rifiuto("--esegui richiede una conferma da terminale interattivo")
    if input("\nScrivi PROVA per inviare l'ordine: ").strip() != "PROVA":
        raise Rifiuto("conferma non data: nessun ordine inviato")

    log = Registro()
    # Da qui fino alla verifica, Ctrl-C non interrompe: fermarsi fra l'invio e
    # l'annullamento lascerebbe un ordine aperto sul mercato.
    vecchio_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        bet_id = invia(s, log, richiesta, c["marketId"], desc["selectionId"])
        if bet_id is None:
            return 5
        annulla(s, log, c["marketId"], bet_id, rif)
        return verifica(s, log, bet_id, prima)
    finally:
        signal.signal(signal.SIGINT, vecchio_sigint)


def invia(s, log: Registro, richiesta: dict, market_id: str, selection_id: int):
    """placeOrders. Ritorna il betId, o None se l'ordine non e' stato piazzato."""
    try:
        esito = rpc(s, "placeOrders", richiesta)
    except ErroreApi as e:
        log.scrivi("placeOrders", richiesta, {"errore": e.grezzo})
        print(f"\nORDINE RESPINTO dall'API: {e.codice}. {spiega(e.codice)}")
        return None
    except requests.RequestException as e:
        # Esito incerto: la richiesta puo' essere arrivata anche se la risposta
        # si e' persa. Non si rimanda (customerRef deduplica solo per 60 s e
        # non serve rischiare): si cerca l'ordine sul mercato.
        log.scrivi("placeOrders", richiesta, {"errore_rete": str(e)})
        print(f"\n[!] risposta all'invio non ricevuta ({e}): cerco l'ordine sul mercato")
        bet_id = cerca_ordine_prova(s, market_id, selection_id)
        print(f"    ordine trovato: {bet_id}" if bet_id else "    nessun ordine trovato")
        return bet_id
    log.scrivi("placeOrders", richiesta, esito)

    report = (esito.get("instructionReports") or [{}])[0]
    if esito.get("status") != "SUCCESS":
        codice = report.get("errorCode") or esito.get("errorCode")
        print(f"\nORDINE RESPINTO: {esito.get('status')} / {esito.get('errorCode')} "
              f"/ {report.get('errorCode')}. {spiega(codice)}")
        print(f"risposta completa nel log: {log.file}")
        return None

    abbinato = float(report.get("sizeMatched") or 0)
    print(f"\nORDINE ACCETTATO: betId {report.get('betId')}, stato "
          f"{report.get('orderStatus')}, abbinato {abbinato:.2f} EUR")
    return report.get("betId")


def cerca_ordine_prova(s, market_id: str, selection_id: int):
    """betId dell'ordine di prova ancora eseguibile su quel mercato, se c'e'."""
    try:
        ordini = rpc(s, "listCurrentOrders", {"marketIds": [market_id],
                                               "orderProjection": "EXECUTABLE"})
    except (ErroreApi, requests.RequestException):
        return None
    for o in ordini.get("currentOrders") or []:
        if (o.get("selectionId") == selection_id and o.get("side") == "BACK"
                and float((o.get("priceSize") or {}).get("price") or 0) == QUOTA_PROVA):
            return o.get("betId")
    return None


def annulla(s, log: Registro, market_id: str, bet_id: str, rif: str) -> None:
    """cancelOrders, fino a tre tentativi. L'esito vero lo dice verifica()."""
    richiesta = {"marketId": market_id, "instructions": [{"betId": bet_id}],
                 "customerRef": rif + "-c"}
    for tentativo in range(1, 4):
        try:
            esito = rpc(s, "cancelOrders", richiesta)
        except (ErroreApi, requests.RequestException) as e:
            log.scrivi("cancelOrders", richiesta, {"errore": str(e)})
            print(f"[!] annullamento, tentativo {tentativo}: {e}")
            time.sleep(2)
            continue
        log.scrivi("cancelOrders", richiesta, esito)
        rep = (esito.get("instructionReports") or [{}])[0]
        print(f"annullamento: {esito.get('status')}, annullati "
              f"{float(rep.get('sizeCancelled') or 0):.2f} EUR"
              + (f" ({rep.get('errorCode')})" if rep.get("errorCode") else ""))
        return


def verifica(s, log: Registro, bet_id: str, prima: dict) -> int:
    """Cosa dice Betfair di quell'ordine adesso, indipendentemente dalle risposte."""
    try:
        ordini = rpc(s, "listCurrentOrders", {"betIds": [bet_id]}).get("currentOrders") or []
        dopo = saldo(s)
    except (ErroreApi, requests.RequestException) as e:
        print(f"\n[!!] VERIFICA NON RIUSCITA ({e}). Controlla su betfair.it -> "
              f"Le mie scommesse l'ordine {bet_id}")
        return 6
    log.scrivi("verifica", {"betId": bet_id}, {"currentOrders": ordini, "saldo": dopo})

    residuo = sum(float(o.get("sizeRemaining") or 0) for o in ordini)
    abbinato = sum(float(o.get("sizeMatched") or 0) for o in ordini)
    print(f"verifica: residuo aperto {residuo:.2f} EUR, abbinato {abbinato:.2f} EUR")
    print(f"saldo disponibile: prima {prima['disponibile']} -> dopo {dopo['disponibile']} EUR"
          f" | esposizione: {dopo['esposizione']}")
    print(f"log: {log.file}")

    if residuo > 0:
        print(f"\n[!!] L'ORDINE {bet_id} E' ANCORA APERTO. Apri betfair.it -> Le mie "
              "scommesse -> Non abbinate e annullalo a mano.")
        return 6
    if abbinato > 0:
        print(f"\n[!] {abbinato:.2f} EUR sono stati abbinati: la puntata resta in gioco "
              "fino alla fine della partita. Il piazzamento comunque funziona.")
    print("\nPROVA ORDINE OK: da questo server, con questa chiave, il conto puo' "
          "piazzare e annullare ordini sull'exchange italiano.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Prove del collegamento con betfair.it")
    ap.add_argument("prova", choices=["connessione", "ordine"])
    ap.add_argument("--esegui", action="store_true",
                    help="(solo ordine) invia davvero l'ordine di prova")
    ap.add_argument("--giorni", type=int, default=10,
                    help="finestra di mercati da considerare (default 10 giorni)")
    a = ap.parse_args(argv)

    carica_env()
    try:
        controlla_credenziali()
        if a.prova == "connessione":
            return prova_connessione(a)
        return prova_ordine(a)
    except Rifiuto as e:
        print(f"\nRIFIUTATO (nessun ordine inviato): {e}")
        return 2
    except ErroreLogin as e:
        print(f"\nLOGIN NON RIUSCITO: {e.stato}. {spiega(e.stato)}")
        return 3 if e.stato == "BETTING_RESTRICTED_LOCATION" else 4
    except ErroreApi as e:
        print(f"\nERRORE API in {e.metodo}: {e.codice}. {spiega(e.codice)}")
        return 4
    except requests.RequestException as e:
        print(f"\nERRORE DI RETE: {e}")
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
