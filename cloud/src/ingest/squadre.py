"""
Ponte fra i nomi squadra di football-data.co.uk e quelli di Understat.

Perche' serve: i due dataset vanno uniti per partita, e i nomi differiscono
(«Man City» vs «Manchester City»). Un join su nomi non allineati non da'
errore: scarta silenziosamente le righe e il modello perde xG senza dirlo.
`verifica()` fallisce a voce alta se compare un nome non mappato.

Attenzione al caso «Paris SG»: il fuzzy matching lo aggancia a «Paris FC»,
che in Ligue 1 e' un'altra squadra. Le mappature qui sono manuali apposta.
"""
from __future__ import annotations

# football-data -> Understat (solo i nomi che differiscono)
ALIAS: dict[str, str] = {
    # Premier League
    "Man City": "Manchester City",
    "Man United": "Manchester United",
    "Newcastle": "Newcastle United",
    "Nott'm Forest": "Nottingham Forest",
    "Wolves": "Wolverhampton Wanderers",
    "Sheffield United": "Sheffield United",
    # Serie A
    "Milan": "AC Milan",
    "Parma": "Parma Calcio 1913",
    "Verona": "Verona",
    # La Liga
    "Ath Bilbao": "Athletic Club",
    "Ath Madrid": "Atletico Madrid",
    "Betis": "Real Betis",
    "Celta": "Celta Vigo",
    "Espanol": "Espanyol",
    "La Coruna": "Deportivo La Coruna",
    "Santander": "Racing Santander",
    "Sociedad": "Real Sociedad",
    "Vallecano": "Rayo Vallecano",
    "Oviedo": "Real Oviedo",
    "Valladolid": "Real Valladolid",
    "Sp Gijon": "Sporting Gijon",
    "Almeria": "Almeria",
    # Bundesliga
    "Dortmund": "Borussia Dortmund",
    "Ein Frankfurt": "Eintracht Frankfurt",
    "FC Koln": "FC Cologne",
    "Hamburg": "Hamburger SV",
    "Leverkusen": "Bayer Leverkusen",
    "M'gladbach": "Borussia M.Gladbach",
    "Mainz": "Mainz 05",
    "RB Leipzig": "RasenBallsport Leipzig",
    "Stuttgart": "VfB Stuttgart",
    "Bayern Munich": "Bayern Munich",
    "Hertha": "Hertha Berlin",
    "Bochum": "Bochum",
    "Darmstadt": "Darmstadt",
    "Heidenheim": "FC Heidenheim",
    "St Pauli": "St. Pauli",
    "Holstein Kiel": "Holstein Kiel",
    # Ligue 1  — 'Paris SG' NON e' 'Paris FC'
    "Paris SG": "Paris Saint Germain",
    "Paris FC": "Paris FC",
    "St Etienne": "Saint-Etienne",
    "Clermont": "Clermont Foot",
}


def a_understat(nome_fd: str) -> str:
    """Nome Understat corrispondente (identita' se non serve traduzione)."""
    return ALIAS.get(nome_fd, nome_fd)


def verifica(nomi_fd, nomi_understat) -> list[str]:
    """Ritorna i nomi football-data che non trovano corrispondenza."""
    us = set(nomi_understat)
    return sorted({n for n in nomi_fd if a_understat(n) not in us})


def righe_anagrafica(nomi_fd, lega: str) -> list[dict]:
    return [{"nome_canonico": a_understat(n), "nome_football_data": n,
             "nome_understat": a_understat(n), "lega": lega} for n in sorted(set(nomi_fd))]


# football-data -> Betfair (solo i nomi che differiscono).
#
# Letti dall'API, non indovinati: `python -m src.ingest.betfair --nomi`
# confronta lega per lega le squadre della stagione con i nomi evento Betfair e
# stampa le righe da aggiungere qui. Va rilanciato a ogni nuova stagione, come
# `verifica()` per Understat: promosse e retrocesse cambiano l'elenco.
#
# Il confronto e' per lega e non globale, e le mappature restano manuali, per
# la stessa ragione detta sopra: «Paris SG» -> «Paris St-G», mentre «Paris FC»
# su Betfair si chiama identico e si aggancia da solo. Un fuzzy matching
# globale li scambierebbe senza dare errore.
#
# Verificato il 10/9/2026 sulla stagione 2026/27: 96 squadre, 17 differenze,
# nessun nome rimasto libero in nessuna delle cinque leghe.
ALIAS_BETFAIR: dict[str, str] = {
    # Premier League
    "Man United": "Man Utd",
    "Nott'm Forest": "Nottm Forest",
    # Serie A
    "Milan": "AC Milan",
    "Monza": "AC Monza",
    # La Liga
    "Ath Bilbao": "Athletic Bilbao",
    "Ath Madrid": "Atletico Madrid",
    "Celta": "Celta Vigo",
    "Espanol": "Espanyol",
    "La Coruna": "Deportivo",
    "Santander": "Racing Santander",
    "Sociedad": "Real Sociedad",
    "Vallecano": "Rayo Vallecano",
    # Bundesliga
    "Ein Frankfurt": "Eintracht Frankfurt",
    "Hamburg": "Hamburger SV",
    "M'gladbach": "Mgladbach",
    # Ligue 1  — 'Paris SG' NON e' 'Paris FC', che su Betfair si chiama uguale
    "Paris SG": "Paris St-G",
    "Troyes": "ESTAC Troyes",
}


def a_betfair(nome_fd: str) -> str:
    """Nome Betfair corrispondente (identita' se non serve traduzione)."""
    return ALIAS_BETFAIR.get(nome_fd, nome_fd)


def verifica_betfair(nomi_fd, nomi_betfair) -> list[str]:
    """Ritorna i nomi football-data che non trovano corrispondenza su Betfair."""
    bf = set(nomi_betfair)
    return sorted({n for n in nomi_fd if a_betfair(n) not in bf})


_INVERSO = {v: k for k, v in ALIAS_BETFAIR.items()}


def da_betfair(nome_bf: str) -> str:
    """Nome football-data corrispondente (identita' se non serve traduzione).

    Serve perche' il calendario si legge da Betfair, che lo pubblica giorni
    prima di football-data, mentre il modello lavora sui nomi football-data.
    """
    return _INVERSO.get(nome_bf, nome_bf)


def verifica_inverso() -> list[str]:
    """Nomi Betfair a cui corrisponderebbe piu' di un nome football-data.

    L'inversione regge solo se ALIAS_BETFAIR e' iniettivo. Se qualcuno
    aggiunge una mappatura che manda due squadre sullo stesso nome Betfair,
    `da_betfair()` ne perderebbe una in silenzio.
    """
    visti: dict[str, list[str]] = {}
    for fd, bf in ALIAS_BETFAIR.items():
        visti.setdefault(bf, []).append(fd)
    return sorted(bf for bf, fds in visti.items() if len(fds) > 1)
