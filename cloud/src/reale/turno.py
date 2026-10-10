"""
Turno del braccio reale: seleziona sui prezzi di betfair.it e, se confermato,
piazza gli ordini con soldi veri. Gira sul server italiano.

    python -m src.reale.turno                       # anteprima: legge e calcola, non scrive nulla
    python -m src.reale.turno --saldo-simulato 400  # anteprima con un saldo ipotetico
    python -m src.reale.turno --invia               # scrive le giocate e piazza, dopo conferma
    python -m src.reale.turno --invia --riprendi    # completa un invio interrotto
    python -m src.reale.turno --auto                # turno automatico (timer): niente GIOCA

Con --auto (dal 10/10/2026, pre-registrazione `braccio_reale_automatico`):
  - gioca solo se l'interruttore `config_reale.attivo` e' acceso;
  - i dati vecchi si giudicano con il controllo strutturale
    (src/report/freschezza.py), non con i giorni dall'ultima partita, e non
    c'e' modo di scavalcarlo;
  - la finestra di partite e' quella del turno: da adesso a lunedi' 23:59 per
    il turno del weekend (venerdi'-domenica), a giovedi' 23:59 per quello
    infrasettimanale (lunedi'-giovedi');
  - tutto il resto (pre-registrazione, turno gia' giocato, controlli fissi,
    validita' dei prezzi, ordine delle scritture) e' identico a --invia.

Ordine delle operazioni con --invia, e perche':
  1. rifiuta se manca la pre-registrazione `braccio_reale`, se il turno ha gia'
     giocate, se i dati del modello sono vecchi, se un controllo fisso fallisce;
  2. mostra le giocate e chiede di scrivere GIOCA. I prezzi letti valgono
     10 minuti: oltre, si rilancia;
  3. scrive le DECISIONI in `giocate_reali` (stato da_piazzare) PRIMA di
     inviare: una decisione registrata e poi non eseguita si vede, un ordine
     partito senza registro no;
  4. per ogni mercato controlla su Betfair se l'ordine c'e' gia' (stesso
     customerOrderRef), invia gli altri come LIMIT alla quota letta, LAPSE;
  5. aggiorna ogni riga con betId, importo e quota abbinati, o con l'errore.

Durante i passi 3-5 Ctrl-C e' ignorato: fermarsi a meta' lascerebbe decisioni
e ordini disallineati. Ogni richiesta e risposta va in `ordini_log` e in una
copia locale (~/.betfair/ordini/), che regge anche se Supabase non risponde.

Codici di uscita: 0 ok, 1 errore, 2 rifiuto per regola o sicurezza,
3 posizione geografica vietata, 4 invio completato con giocate respinte o
incerte (vanno guardate), 5 interruttore spento (solo --auto).
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.db.client import client                                  # noqa: E402
from src.ingest.betfair import LocazioneVietata                    # noqa: E402
from src.model.dixon_coles_xg import markets, score_matrix         # noqa: E402
from src.reale import api                                          # noqa: E402
from src.reale.selezione import (CAMPIONATI, MARGINE_INIZIO,      # noqa: E402
                                 MIN_PARTITE_SQUADRA, candidati, controlla,
                                 seleziona, togli_gia_giocate)
from src.report import freschezza                                 # noqa: E402
from src.report.dataset import carica                              # noqa: E402
from src.report.predict import MAX_ETA, VERSIONE, build_models      # noqa: E402

FUSO = ZoneInfo("Europe/Rome")
VALIDITA_PREZZI = timedelta(minutes=10)
PREREGISTRAZIONE = "braccio_reale"


def fine_finestra(ora: datetime) -> datetime:
    """Ultimo istante delle partite del turno che contiene `ora` (ora italiana).

    Weekend (venerdi'-domenica): fino a lunedi' 23:59. Infrasettimanale
    (lunedi'-giovedi'): fino a giovedi' 23:59. Cosi' i due turni non si
    sovrappongono mai, e un turno del venerdi' non gioca martedi'.
    """
    ora = ora.astimezone(FUSO)
    giorno = ora.isoweekday()                     # 1 lunedi' ... 7 domenica
    avanti = (4 - giorno) if giorno <= 4 else (8 - giorno)
    fine = (ora + timedelta(days=avanti)).replace(hour=23, minute=59, second=0, microsecond=0)
    return fine


def interruttore(db) -> tuple[bool, str]:
    """(acceso, motivo). Tabella assente o riga mancante = spento."""
    try:
        r = db.select("config_reale", colonne="attivo,modificato_il,modificato_da",
                      filtri={"id": "eq.1"})
    except Exception as e:                                   # noqa: BLE001
        return False, f"interruttore non leggibile ({str(e)[:80]}): sql/automatico.sql lanciato?"
    if not r:
        return False, "interruttore non configurato (config_reale vuota)"
    if not r[0]["attivo"]:
        da = r[0].get("modificato_da") or "?"
        return False, f"interruttore spento ({da}, {str(r[0].get('modificato_il'))[:16]})"
    return True, "acceso"


def turno_corrente(ora: datetime) -> str:
    """'2026-W42' per il turno del weekend (da venerdi' a domenica),
    '2026-W42-inf' per quello infrasettimanale (da lunedi' a giovedi').

    La settimana ISO va da lunedi' a domenica: un martedi' e il weekend
    successivo cadono nella stessa settimana, quindi senza il suffisso il
    secondo turno verrebbe rifiutato come gia' giocato.
    """
    anno, settimana, giorno = ora.isocalendar()
    return f"{anno}-W{settimana:02d}" + ("-inf" if giorno <= 4 else "")


def rif_ordine(turno: str, i: int) -> str:
    """customerOrderRef: unico per giocata, al massimo 32 caratteri.

    '2026-W41' -> 'pg26W41-07', '2026-W42-inf' -> 'pg26W42i-07'.
    """
    settimana = turno[5:8]
    return f"pg{turno[2:4]}{settimana}{'i' if turno.endswith('-inf') else ''}-{i:02d}"


# ------------------------------------------------------------------ modello

def probabilita_dal_modello(models: dict):
    def prob(lega: str, casa: str, trasf: str):
        if lega not in models:
            return "campionato senza modello"
        par, ix, _, cnt, _ = models[lega]
        ignote = [t for t in (casa, trasf) if t not in ix]
        if ignote:
            return "squadra sconosciuta al modello: " + ", ".join(ignote)
        magre = [f"{t} ({cnt.get(t, 0)})" for t in (casa, trasf)
                 if cnt.get(t, 0) < MIN_PARTITE_SQUADRA]
        if magre:
            return "storico insufficiente: " + ", ".join(magre)
        return markets(score_matrix(par, ix[casa], ix[trasf]))
    return prob


def dati_vecchi(models: dict, oggi: pd.Timestamp) -> list[tuple]:
    return [(k, v[4].date(), (oggi - v[4]).days) for k, v in models.items()
            if (oggi - v[4]).days > MAX_ETA]


# ---------------------------------------------------------------- piazzamento

def _stato(size: float, abbinato: float) -> str:
    if abbinato >= size - 0.005:
        return "abbinata"
    return "parziale" if abbinato > 0 else "piazzata"


def _aggiorna(db, riga: dict, valori: dict) -> None:
    try:
        db.update("giocate_reali", {"id": f"eq.{riga['id']}"}, valori)
    except Exception as e:                                   # noqa: BLE001
        print(f"[!!] riga {riga['id']} NON aggiornata su Supabase ({e}): {valori}")


def _da_ordine(o: dict, size: float) -> dict:
    abbinato = float(o.get("sizeMatched") or 0)
    return {"stato": _stato(size, abbinato), "bet_id": o.get("betId"),
            "piazzata_il": o.get("placedDate"), "stake_abbinato": abbinato,
            "quota_abbinata": o.get("averagePriceMatched") or None}


def ordini_presenti(s, market_id: str) -> dict[str, dict]:
    """Ordini gia' su Betfair per quel mercato, per customerOrderRef."""
    res = api.rpc(s, "listCurrentOrders", {"marketIds": [market_id]})
    return {o["customerOrderRef"]: o for o in res.get("currentOrders") or []
            if o.get("customerOrderRef")}


def piazza(s, db, log: api.LogOrdini, righe: list[dict], turno: str) -> dict:
    """Invia le giocate in stato da_piazzare, un mercato per volta.

    Idempotente: prima di inviare chiede a Betfair quali ordini con lo stesso
    customerOrderRef esistono gia', e non li rimanda.
    """
    conti = {"inviate": 0, "gia_presenti": 0, "respinte": 0, "incerte": 0,
             "annullate": 0, "abbinato": 0.0}
    per_mercato: dict[str, list[dict]] = {}
    for r in righe:
        per_mercato.setdefault(r["market_id"], []).append(r)

    libro = api.libri(s, list(per_mercato))
    for market_id, gruppo in per_mercato.items():
        b = libro.get(market_id) or {}
        if b.get("status") != "OPEN" or b.get("inplay"):
            for r in gruppo:
                _aggiorna(db, r, {"stato": "annullata",
                                  "errore": f"mercato {b.get('status')} inplay={b.get('inplay')}"})
                conti["annullate"] += 1
            continue

        try:
            presenti = ordini_presenti(s, market_id)
        except Exception as e:                               # noqa: BLE001
            # Niente e' stato inviato: le giocate restano da_piazzare e
            # `--invia --riprendi` le ritenta.
            print(f"[!] {market_id}: impossibile verificare gli ordini esistenti ({e}), salto")
            for r in gruppo:
                _aggiorna(db, r, {"errore": f"verifica non riuscita, non inviata: {e}"})
                conti["incerte"] += 1
            continue

        da_inviare = []
        for r in gruppo:
            o = presenti.get(r["customer_order_ref"])
            if o:
                _aggiorna(db, r, _da_ordine(o, float(r["stake_richiesto"])))
                conti["gia_presenti"] += 1
            else:
                da_inviare.append(r)
        if not da_inviare:
            continue

        istruzioni = [{"selectionId": int(r["selection_id"]), "handicap": 0, "side": "BACK",
                       "orderType": "LIMIT", "customerOrderRef": r["customer_order_ref"],
                       "limitOrder": {"size": float(r["stake_richiesto"]),
                                      "price": float(r["quota_riferimento"]),
                                      "persistenceType": "LAPSE"}}
                      for r in da_inviare]
        richiesta = {"marketId": market_id, "instructions": istruzioni,
                     "customerRef": f"{turno}-{market_id}"[:32]}
        per_rif = {r["customer_order_ref"]: r for r in da_inviare}

        try:
            esito = api.rpc(s, "placeOrders", richiesta)
        except requests.RequestException as e:
            # Esito incerto: puo' essere arrivato. Si chiede a Betfair.
            log.scrivi("placeOrders", market_id, richiesta, {"errore_rete": str(e)}, "incerto")
            try:
                presenti = ordini_presenti(s, market_id)
            except Exception:                                # noqa: BLE001
                presenti = None
            for rif, r in per_rif.items():
                if presenti and rif in presenti:
                    _aggiorna(db, r, _da_ordine(presenti[rif], float(r["stake_richiesto"])))
                    conti["inviate"] += 1
                else:
                    _aggiorna(db, r, {"stato": "incerta", "errore": f"rete: {e}"})
                    conti["incerte"] += 1
            continue
        except RuntimeError as e:
            # Errore dell'API: la richiesta e' stata rifiutata per intero.
            log.scrivi("placeOrders", market_id, richiesta, {"errore": str(e)}, "errore_api")
            for r in per_rif.values():
                _aggiorna(db, r, {"stato": "respinta", "errore": str(e)[:500]})
                conti["respinte"] += 1
            continue

        log.scrivi("placeOrders", market_id, richiesta, esito, esito.get("status", "?"))
        for rep in esito.get("instructionReports") or []:
            rif = (rep.get("instruction") or {}).get("customerOrderRef")
            r = per_rif.pop(rif, None)
            if r is None:
                continue
            if rep.get("status") == "SUCCESS":
                abbinato = float(rep.get("sizeMatched") or 0)
                _aggiorna(db, r, {"stato": _stato(float(r["stake_richiesto"]), abbinato),
                                  "bet_id": rep.get("betId"),
                                  "piazzata_il": rep.get("placedDate"),
                                  "stake_abbinato": abbinato,
                                  "quota_abbinata": rep.get("averagePriceMatched") or None})
                conti["inviate"] += 1
                conti["abbinato"] += abbinato
            else:
                codice = rep.get("errorCode") or esito.get("errorCode")
                _aggiorna(db, r, {"stato": "respinta", "errore": codice})
                conti["respinte"] += 1
        for r in per_rif.values():          # nessun report per questa istruzione
            _aggiorna(db, r, {"stato": "respinta",
                              "errore": f"nessun report ({esito.get('status')} "
                                        f"{esito.get('errorCode')})"})
            conti["respinte"] += 1
    return conti


# --------------------------------------------------------------------- main

def stampa(scelte: list[dict], saldo: float) -> None:
    print(f"\n=== GIOCATE ({len(scelte)}) — saldo {saldo:.2f} EUR, "
          f"esposizione {sum(x['stake'] for x in scelte):.2f} EUR ===")
    for x in sorted(scelte, key=lambda x: x["inizio"]):
        quando = x["inizio"].astimezone(FUSO)
        giorno = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")[quando.weekday()]
        etichetta = f"{x['casa'][:15]} - {x['trasferta'][:15]}"
        print(f"{giorno} {quando:%d/%m %H:%M} {x['lega']:4} {etichetta:33} "
              f"{x['mercato']:4} {x['selezione']:5} q {x['quota']:5.2f} "
              f"(netta {x['quota_netta']:.3f})  p {x['p']:.3f}  edge {x['edge']:+5.1%}  "
              f"{x['stake']:5.2f} EUR  [disp. {x['size']:.0f}]")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Turno del braccio reale su betfair.it")
    ap.add_argument("--invia", action="store_true", help="piazza davvero, dopo conferma")
    ap.add_argument("--riprendi", action="store_true",
                    help="con --invia: invia le giocate del turno rimaste da_piazzare")
    ap.add_argument("--saldo-simulato", type=float, dest="saldo_simulato",
                    help="solo anteprima: calcola le puntate su questo saldo")
    ap.add_argument("--consenti-dati-vecchi", action="store_true", dest="consenti_dati_vecchi",
                    help=f"accetta campionati fermi da oltre {MAX_ETA} giorni (es. dopo una sosta)")
    ap.add_argument("--giorni", type=float, default=4,
                    help="finestra di partite, in giorni da adesso (default 4)")
    ap.add_argument("--auto", action="store_true",
                    help="turno automatico: senza conferma, interruttore, controllo strutturale")
    a = ap.parse_args(argv)
    if a.auto:
        if a.riprendi or a.saldo_simulato is not None or a.consenti_dati_vecchi:
            print("--auto non si combina con --riprendi, --saldo-simulato o --consenti-dati-vecchi")
            return 2
        a.invia = True

    if a.invia and a.saldo_simulato is not None:
        print("--saldo-simulato vale solo per l'anteprima")
        return 2

    api.carica_env()
    if a.invia and os.environ.get("SUPABASE_KEY", "").startswith("sb_publishable_"):
        # Con la chiave pubblica la RLS nasconde giocate_reali invece di dare
        # errore: il controllo sul turno gia' giocato vedrebbe zero righe.
        print("[!] RIFIUTATO: per giocare serve la secret key di Supabase in "
              "SUPABASE_KEY (~/.betfair/betfair.env), non la publishable.")
        return 2
    db = client()
    if a.auto:
        acceso, motivo = interruttore(db)
        if not acceso:
            print(f"[!] FERMO: {motivo}. Nessuna giocata.")
            return 5
    try:
        s = api.sessione()
    except LocazioneVietata:
        print("[!] Betfair rifiuta la posizione di questo server (BETTING_RESTRICTED_LOCATION).")
        return 3

    ora = datetime.now(FUSO)
    turno = turno_corrente(ora)
    try:
        gia = db.select("giocate_reali", colonne="*", filtri={"turno": f"eq.{turno}"})
    except Exception as e:                                   # noqa: BLE001
        if "PGRST205" not in str(e):
            raise
        if a.invia:
            print("[!] RIFIUTATO: la tabella giocate_reali non esiste. "
                  "Lanciare sql/braccio_reale.sql nel SQL Editor di Supabase.")
            return 2
        print("(giocate_reali non esiste ancora: lanciare sql/braccio_reale.sql "
              "prima di giocare)")
        gia = []

    # --- ripresa di un invio interrotto: niente selezione nuova
    if a.invia and a.riprendi:
        # Anche le 'incerte': piazza() chiede prima a Betfair se l'ordine con
        # quel customerOrderRef esiste, e lo rimanda solo se non c'e'.
        sospese = [r for r in gia if r["stato"] in ("da_piazzare", "incerta")]
        print(f"turno {turno}: {len(gia)} giocate registrate, "
              f"{len(sospese)} da piazzare o incerte")
        if not sospese:
            return 0
        if not sys.stdin.isatty() or input("Scrivi GIOCA per inviarle: ").strip() != "GIOCA":
            print("non confermato: nessun ordine inviato")
            return 2
        log = api.LogOrdini(db, turno)
        vecchio = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            conti = piazza(s, db, log, sospese, turno)
        finally:
            signal.signal(signal.SIGINT, vecchio)
        print(f"\n{conti}\nlog locale: {log.file}")
        return 4 if conti["respinte"] or conti["incerte"] else 0

    if a.invia and gia:
        print(f"[!] RIFIUTATO: il turno {turno} ha gia' {len(gia)} giocate registrate. "
              "Per completare un invio interrotto: --invia --riprendi")
        return 2

    # --- saldo
    if a.saldo_simulato is not None:
        conto = {"disponibile": a.saldo_simulato, "esposizione": 0.0,
                 "totale": a.saldo_simulato}
        print(f"saldo SIMULATO: {conto['totale']:.2f} EUR (anteprima)")
    else:
        conto = api.saldo(s)
        print(f"saldo betfair.it: {conto['totale']:.2f} EUR "
              f"(disponibile {conto['disponibile']:.2f}, esposizione {conto['esposizione']:.2f})")

    # --- modello
    oggi = pd.Timestamp(ora.date())
    frame = carica(db)
    models = {k: v for k, v in build_models(frame, oggi).items() if k in CAMPIONATI}
    for k, v in sorted(models.items()):
        eta = (oggi - v[4]).days
        print(f"  modello {k:<4} {v[2]:>4} partite, ultima {v[4].date()} ({eta} giorni fa)"
              + ("  <-- DATI VECCHI" if eta > MAX_ETA else ""))
    vecchi = dati_vecchi(models, oggi)
    if a.auto:
        # controllo strutturale al posto dei giorni: la sosta non e' un buco
        motivi = freschezza.controlla(db, frame, CAMPIONATI)
        if motivi:
            print("\n[!] RIFIUTATO: dati non aggiornati:")
            for m in motivi:
                print(f"      {m}")
            return 2
        print("  dati: controllo strutturale superato (nessuna partita giocata mancante)")
        vecchi = []
    if a.invia and vecchi and not a.consenti_dati_vecchi:
        print(f"\n[!] RIFIUTATO: {len(vecchi)} campionati fermi da oltre {MAX_ETA} giorni. "
              "Se e' una sosta e non un buco nei dati: --consenti-dati-vecchi")
        return 2

    # --- prezzi
    letti_il = datetime.now(timezone.utc)
    fino = (fine_finestra(ora) - ora) if a.auto else timedelta(days=a.giorni)
    if a.auto:
        print(f"  finestra del turno: fino a {fine_finestra(ora):%a %d/%m %H:%M}")
    cat = api.catalogo(s, MARGINE_INIZIO, fino, leghe=CAMPIONATI)
    book = api.libri(s, sorted({c["marketId"] for c in cat}))
    cand, scartate = candidati(cat, book, probabilita_dal_modello(models), letti_il)
    try:
        future = db.select("giocate_reali", colonne="market_id,stato",
                           filtri={"inizio": f"gt.{letti_il.isoformat()}"})
    except Exception as e:                                   # noqa: BLE001
        if a.invia:
            raise
        print(f"(giocate gia' fatte non lette: {str(e)[:80]})")
        future = []
    cand, doppie = togli_gia_giocate(cand, future)
    if doppie:
        print(f"escluse {doppie} selezioni su mercati gia' giocati in un turno precedente")

    eventi = {}
    for c in cat:
        lega = api.COMPETIZIONI.get((c.get("competition") or {}).get("id"), "?")
        eventi.setdefault(lega, set()).add(c["event"]["name"])
    usati = {}
    for c in cand:
        usati.setdefault(c["lega"], set()).add(c["evento_bf"])
    print("\npartite per campionato (su Betfair / valutate dal modello): "
          + "  ".join(f"{k} {len(eventi[k])}/{len(usati.get(k, ()))}" for k in sorted(eventi)))
    for x in scartate:
        print(f"  saltata: {x}")

    scelte = seleziona(cand, conto["totale"])
    stampa(scelte, conto["totale"])
    problemi = controlla(scelte, conto["totale"])
    for p in problemi:
        print(f"[!!] {p}")

    if not a.invia:
        print("\nANTEPRIMA: nulla scritto, nessun ordine. Per giocare: --invia")
        return 2 if problemi else 0

    # --- da qui in poi si gioca davvero
    if problemi:
        print("\n[!] RIFIUTATO: controlli fissi non superati, nessun ordine")
        return 2
    if not scelte:
        print("\nnessuna giocata da inviare")
        return 0
    if not db.select("preregistrazioni", colonne="chiave",
                     filtri={"chiave": f"eq.{PREREGISTRAZIONE}"}):
        print(f"\n[!] RIFIUTATO: manca la pre-registrazione '{PREREGISTRAZIONE}' "
              "(sql/braccio_reale.sql). Nessun ordine.")
        return 2
    totale = sum(x["stake"] for x in scelte)
    if a.auto:
        if not db.select("preregistrazioni", colonne="chiave",
                         filtri={"chiave": "eq.braccio_reale_automatico"}):
            print("\n[!] RIFIUTATO: manca la pre-registrazione 'braccio_reale_automatico' "
                  "(sql/automatico.sql). Nessun ordine.")
            return 2
        print(f"\nturno automatico: invio {len(scelte)} ordini, totale {totale:.2f} EUR")
    else:
        if not sys.stdin.isatty():
            print("\n[!] RIFIUTATO: l'invio richiede la conferma da terminale")
            return 2
        risposta = input(f"\nScrivi GIOCA per inviare {len(scelte)} ordini, "
                         f"totale {totale:.2f} EUR: ").strip()
        if risposta != "GIOCA":
            print("non confermato: nessun ordine inviato")
            return 2
    if datetime.now(timezone.utc) - letti_il > VALIDITA_PREZZI:
        print("[!] RIFIUTATO: prezzi letti da oltre 10 minuti. Rilanciare.")
        return 2

    log = api.LogOrdini(db, turno)
    vecchio = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        righe = [{
            "chiave": f"{turno}|{x['market_id']}|{x['selection_id']}",
            "turno": turno, "versione_modello": VERSIONE,
            "lega": x["lega"], "data_partita": str(x["inizio"].astimezone(FUSO).date()),
            "ora": x["inizio"].astimezone(FUSO).strftime("%H:%M"),
            "inizio": x["inizio"].isoformat(), "casa": x["casa"], "trasferta": x["trasferta"],
            "evento_bf": x["evento_bf"], "mercato": x["mercato"], "selezione": x["selezione"],
            "market_id": x["market_id"], "selection_id": x["selection_id"],
            "prob_modello": round(x["p"], 6), "quota_riferimento": x["quota"],
            "size_disponibile": x["size"], "quota_netta": round(x["quota_netta"], 6),
            "edge_netto": round(x["edge"], 6), "saldo_al_turno": conto["totale"],
            "stake_richiesto": x["stake"], "stato": "da_piazzare",
            "customer_order_ref": rif_ordine(turno, i),
        } for i, x in enumerate(sorted(scelte, key=lambda x: x["inizio"]), 1)]
        try:
            scritte = db.insert("giocate_reali", righe, ritorna=True)
        except Exception as e:                               # noqa: BLE001
            print(f"[!] RIFIUTATO: decisioni non scritte su Supabase ({e}). Nessun ordine.")
            return 1
        if len(scritte) != len(righe):
            print(f"[!] scritte {len(scritte)} decisioni su {len(righe)}: mi fermo, "
                  "nessun ordine. Controllare giocate_reali.")
            return 1
        conti = piazza(s, db, log, scritte, turno)
    finally:
        signal.signal(signal.SIGINT, vecchio)

    print(f"\ninviate {conti['inviate']}, gia' presenti {conti['gia_presenti']}, "
          f"respinte {conti['respinte']}, incerte {conti['incerte']}, "
          f"annullate {conti['annullate']} — abbinati subito {conti['abbinato']:.2f} EUR")
    print(f"log locale: {log.file}")
    if conti["respinte"] or conti["incerte"]:
        print("[!] ci sono giocate respinte o incerte: controllare giocate_reali e betfair.it")
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
