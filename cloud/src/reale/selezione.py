"""
Selezione del braccio reale: dal catalogo Betfair e dal modello alle giocate.

Funzioni pure, senza rete e senza database, cosi' si collaudano su dati
sintetici (tests/reale.py). Le regole sono quelle pre-registrate in
`preregistrazioni.braccio_reale`; il modello e' lo stesso del braccio carta
(`build_models` di src/report/predict.py), cosi' i due bracci si confrontano
sulle stesse probabilita'.

Differenze volute rispetto al braccio carta:
  - prezzo: miglior back su betfair.it, non la quota massima di ~17 bookmaker;
  - edge e Kelly sulla quota NETTA della commissione;
  - puntate sul saldo reale del conto, non su 1.000 EUR fissi;
  - con piu' di 25 candidati si tengono gli edge PIU' ALTI (nel braccio carta
    l'ordinamento crescente teneva i piu' bassi: nota 17, par.5).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.ingest.betfair import (COMPETIZIONI, arrotonda_stake,        # noqa: E402
                                quota_netta)
from src.ingest.squadre import da_betfair                            # noqa: E402

# Regole pre-registrate. Cambiarle vuol dire una nuova pre-registrazione.
EDGE_MIN, EDGE_MAX = 0.02, 0.10
KELLY_FRAC = 0.25
TETTO_GIOCATA = 0.01          # frazione del saldo
TETTO_ASSOLUTO = 10.0         # EUR, contro errori di parametro o di saldo letto male
ESPOSIZIONE = 0.20            # frazione del saldo per turno
MAX_GIOCATE = 25
MIN_PARTITE_SQUADRA = 8
QUOTA_MIN, QUOTA_MAX = 1.20, 15.0   # fuori da qui si scarta: prezzi estremi, mercati sottili
MARGINE_INIZIO = timedelta(minutes=15)

# selezione Betfair -> chiave del dizionario di markets() del modello
CHIAVE_MODELLO = {"1": "H", "X": "D", "2": "A", "over": "O2.5", "under": "U2.5"}


def kelly(p: float, o: float) -> float:
    """Frazione di Kelly piena per una quota decimale o (gia' netta)."""
    b = o - 1.0
    return max(0.0, (p * o - 1.0) / b) if b > 0 else 0.0


def _inizio(cat: dict) -> datetime:
    return datetime.fromisoformat(cat["marketStartTime"].replace("Z", "+00:00"))


def _selezione(tipo: str, etichetta: str, casa_bf: str, trasf_bf: str) -> str | None:
    if tipo == "1X2":
        return {casa_bf: "1", trasf_bf: "2", "The Draw": "X"}.get(etichetta)
    e = etichetta.lower()
    if e.startswith("over"):
        return "over"
    if e.startswith("under"):
        return "under"
    return None


def _tipo(nome_mercato: str) -> str | None:
    n = (nome_mercato or "").lower()
    if n.startswith("match odds"):
        return "1X2"
    return "OU25" if "2.5" in n else None


def candidati(cat: list[dict], book: dict[str, dict], probabilita, adesso: datetime,
              ) -> tuple[list[dict], list[str]]:
    """Una riga per ogni selezione quotata di ogni mercato utilizzabile.

    `probabilita(lega, casa, trasferta)` ritorna il dizionario di markets() del
    modello, oppure una stringa col motivo per cui la partita va saltata
    (squadra sconosciuta, storico insufficiente, lega senza modello).

    Ritorna (candidati, partite_scartate_con_motivo).
    """
    righe: list[dict] = []
    scartate: list[str] = []
    gia_valutate: dict[tuple, object] = {}
    for c in cat:
        lega = COMPETIZIONI.get((c.get("competition") or {}).get("id"))
        nome = (c.get("event") or {}).get("name") or ""
        tipo = _tipo(c.get("marketName"))
        if not lega or " v " not in nome or tipo is None:
            continue
        inizio = _inizio(c)
        if inizio - adesso < MARGINE_INIZIO:
            continue
        libro = book.get(c["marketId"])
        if not libro or libro.get("status") != "OPEN" or libro.get("inplay"):
            continue

        casa_bf, trasf_bf = (x.strip() for x in nome.split(" v ", 1))
        casa, trasf = da_betfair(casa_bf), da_betfair(trasf_bf)
        chiave_partita = (lega, casa, trasf, inizio)
        if chiave_partita not in gia_valutate:
            gia_valutate[chiave_partita] = probabilita(lega, casa, trasf)
            if isinstance(gia_valutate[chiave_partita], str):
                scartate.append(f"{lega} {casa} - {trasf}: {gia_valutate[chiave_partita]}")
        mk = gia_valutate[chiave_partita]
        if isinstance(mk, str):
            continue

        per_id = {r["selectionId"]: r for r in libro.get("runners", [])}
        for desc in c.get("runners", []):
            sel = _selezione(tipo, (desc.get("runnerName") or "").strip(), casa_bf, trasf_bf)
            r = per_id.get(desc["selectionId"])
            if sel is None or r is None or r.get("status") != "ACTIVE":
                continue
            offerte = (r.get("ex") or {}).get("availableToBack") or []
            if not offerte or not offerte[0].get("price"):
                continue
            righe.append({
                "lega": lega, "casa": casa, "trasferta": trasf, "evento_bf": nome,
                "inizio": inizio, "mercato": tipo, "selezione": sel,
                "market_id": c["marketId"], "selection_id": desc["selectionId"],
                "p": float(mk[CHIAVE_MODELLO[sel]]),
                "quota": float(offerte[0]["price"]),
                "size": float(offerte[0].get("size") or 0),
            })
    return righe, scartate


def seleziona(cand: list[dict], saldo: float) -> list[dict]:
    """Applica banda di edge, Kelly sul saldo, tetti ed esposizione.

    Ritorna le giocate, ciascuna con quota_netta, edge e stake gia' validi
    per betfair.it (minimo 2 EUR, multipli di 0,50, arrotondati per difetto).
    """
    if saldo <= 0:
        return []
    scelte = []
    for c in cand:
        if not QUOTA_MIN <= c["quota"] <= QUOTA_MAX:
            continue
        o_netta = quota_netta(c["quota"])
        edge = c["p"] * o_netta - 1
        if not EDGE_MIN <= edge <= EDGE_MAX:
            continue
        f = min(kelly(c["p"], o_netta) * KELLY_FRAC, TETTO_GIOCATA)
        stake = arrotonda_stake(min(f * saldo, TETTO_ASSOLUTO))
        if stake <= 0:
            continue
        scelte.append({**c, "quota_netta": o_netta, "edge": edge, "stake": stake})

    scelte.sort(key=lambda x: (-x["edge"], x["inizio"]))
    scelte = scelte[:MAX_GIOCATE]

    totale, limite = sum(x["stake"] for x in scelte), ESPOSIZIONE * saldo
    if totale > limite:
        for x in scelte:
            x["stake"] = arrotonda_stake(x["stake"] * limite / totale)
        scelte = [x for x in scelte if x["stake"] > 0]
    return scelte


def controlla(scelte: list[dict], saldo: float) -> list[str]:
    """Controlli indipendenti dai parametri, prima di qualunque invio.

    Se una sola condizione fallisce il turno intero si ferma: un errore qui
    vuol dire che qualcosa a monte (saldo letto male, regola cambiata per
    sbaglio) produce puntate che la pre-registrazione non prevede.
    """
    problemi = []
    for x in scelte:
        if x["stake"] > min(TETTO_ASSOLUTO, 0.02 * saldo) + 1e-9:
            problemi.append(f"puntata {x['stake']} oltre i tetti fissi: {x['evento_bf']}")
        if x["stake"] < 2 or abs(x["stake"] * 2 - round(x["stake"] * 2)) > 1e-9:
            problemi.append(f"puntata {x['stake']} non valida per betfair.it: {x['evento_bf']}")
        if not QUOTA_MIN <= x["quota"] <= QUOTA_MAX:
            problemi.append(f"quota {x['quota']} fuori dall'intervallo 1,20-15: {x['evento_bf']}")
    if sum(x["stake"] for x in scelte) > ESPOSIZIONE * saldo + 1e-9:
        problemi.append("esposizione del turno oltre il 20% del saldo")
    if len(scelte) > MAX_GIOCATE:
        problemi.append(f"{len(scelte)} giocate, oltre il massimo di {MAX_GIOCATE}")
    return problemi
