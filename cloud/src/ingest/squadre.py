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
