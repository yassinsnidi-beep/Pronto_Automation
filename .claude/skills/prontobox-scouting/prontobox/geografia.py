"""Comune, provincia e perimetro geografico (§3.0.4 e §Stadio1 filtro 1).

Le coordinate dei capoluoghi qui sotto sono conoscenza geografica generica
(centroidi pubblici approssimati), non dati estratti dagli scraper: servono
solo come riferimento per calcolare una distanza aria-aria quando il record
ha lat/lon proprie (oggi solo immobiliare le fornisce quasi sempre) e non
sostituiscono un vero geocoding.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from . import util
from .campo import campo

# comune (chiave normalizzata) -> sigla provincia. Copre le 36 citta' del
# perimetro (§6) piu' i comuni di hinterland visti nei dati reali del
# 16/09/2026. Un comune non presente qui e non riconoscibile da un pattern
# "(SIGLA)" nel testo resta con provincia ignota (mai un errore).
COMUNE_PROVINCIA: dict[str, str] = {
    "bolzano": "BZ", "bologna": "BO", "trento": "TN", "padova": "PD",
    "milano": "MI", "parma": "PR", "modena": "MO", "reggio emilia": "RE",
    "brescia": "BS", "monza": "MB", "bergamo": "BG", "verona": "VR",
    "vicenza": "VI", "firenze": "FI", "pavia": "PV", "como": "CO",
    "piacenza": "PC", "treviso": "TV", "novara": "NO", "cremona": "CR",
    "cesena": "FC", "forli": "FC", "pisa": "PI", "udine": "UD",
    "varese": "VA", "ravenna": "RA", "ancona": "AN",
    "sesto san giovanni": "MI", "cinisello balsamo": "MI",
    "busto arsizio": "VA", "roma": "RM", "legnano": "MI", "prato": "PO",
    "cagliari": "CA", "pistoia": "PT", "lucca": "LU",
    # hinterland / comuni visti nei dati reali di opportunities/INPUT
    "gorgonzola": "MI", "pozzuolo del friuli": "UD",
    "crespina lorenzana": "PI", "san giuliano terme": "PI",
    "montopoli in val d arno": "PI", "binasco": "MI", "treviolo": "BG",
    "luzzara": "RE", "jesi": "AN", "villafranca di verona": "VR",
    "gorle": "BG", "san donato milanese": "MI",
}

# Centroidi approssimati (lat, lon) dei 36 capoluoghi target, per la distanza
# aria-aria quando servono coordinate e manca un comune non-capoluogo nella
# tabella sopra. Precisione da citta', non da indirizzo: sufficiente per un
# filtro a raggio_km, non per altro.
CENTROIDI_TARGET: dict[str, tuple[float, float]] = {
    "bolzano": (46.4983, 11.3548), "bologna": (44.4949, 11.3426),
    "trento": (46.0679, 11.1211), "padova": (45.4064, 11.8768),
    "milano": (45.4642, 9.1900), "parma": (44.8015, 10.3279),
    "modena": (44.6471, 10.9252), "reggio emilia": (44.6989, 10.6297),
    "brescia": (45.5416, 10.2118), "monza": (45.5845, 9.2744),
    "bergamo": (45.6983, 9.6773), "verona": (45.4384, 10.9916),
    "vicenza": (45.5455, 11.5354), "firenze": (43.7696, 11.2558),
    "pavia": (45.1847, 9.1582), "como": (45.8081, 9.0852),
    "piacenza": (45.0526, 9.6930), "treviso": (45.6669, 12.2431),
    "novara": (45.4469, 8.6220), "cremona": (45.1335, 10.0224),
    "cesena": (44.1391, 12.2431), "forli": (44.2226, 12.0408),
    "pisa": (43.7228, 10.4017), "udine": (46.0711, 13.2346),
    "varese": (45.8206, 8.8250), "ravenna": (44.4184, 12.2035),
    "ancona": (43.6158, 13.5189), "sesto san giovanni": (45.5352, 9.2359),
    "cinisello balsamo": (45.5551, 9.2178), "busto arsizio": (45.6112, 8.8515),
    "roma": (41.9028, 12.4964), "legnano": (45.5960, 8.9169),
    "prato": (43.8777, 11.1023), "cagliari": (39.2238, 9.1217),
    "pistoia": (43.9334, 10.9177), "lucca": (43.8429, 10.5027),
}

import re as _re

# Due pattern per riconoscere "Comune (PR)":
# 1. ANCORATO all'inizio del testo: gli indirizzi delle aste iniziano quasi
#    sempre con "<Comune> (<PR>), <via>..." (anche con nomi multi-parola in
#    minuscolo tipo "Crespina lorenzana (PI)"), quindi ancorare a ^ e' sicuro
#    anche senza richiedere maiuscole.
_RE_COMUNE_PR_ANCORATO = _re.compile(r"^\s*(.{2,50}?)\s*\(([A-Za-z]{2})\)")
# 2. LIBERO, per quando "Comune (PR)" compare in mezzo a un testo qualunque
#    (es. un titolo fallcoaste "...sito in Luzzara (RE)..."): qui serve
#    riconoscere solo il nome proprio, altrimenti si rischia di catturare
#    anche le parole del testo circostante. Si richiede quindi che il
#    "comune" sia una sequenza di parole con iniziale maiuscola, intervallate
#    al piu' da connettivi tipici dei toponimi italiani (di/del/san/...).
_CONNETTIVI = r"(?:di|del|della|dei|degli|san|santa|sant|val|d|in|sul|sull|e)"
_PAROLA_CAP = r"[A-ZÀ-Ý][a-zà-ÿ'\-]*"
_RE_COMUNE_PR_LIBERO = _re.compile(
    rf"({_PAROLA_CAP}(?:\s+(?:{_PAROLA_CAP}|{_CONNETTIVI})){{0,3}})\s*\(([A-Za-z]{{2}})\)"
)


_RE_PREFISSO_COMUNE_DI = _re.compile(r"^comune\s+di\s+", _re.IGNORECASE)


def _ripulisci_comune_estratto(comune: str) -> str:
    """'Comune di Silea' -> 'Silea'. La parola 'Comune' e' maiuscola e
    grammaticalmente valida come inizio del pattern libero (es. '...nel
    Comune di Silea (TV)'), quindi va tolta esplicitamente: non e' mai lei
    il nome del paese."""
    return _RE_PREFISSO_COMUNE_DI.sub("", comune).strip()


def _da_pattern_comune_pr(testo: Optional[str], permetti_ancorato: bool = True) -> Optional[tuple[str, str]]:
    """Cerca 'Comune (PR)' in un testo, es. 'Gorgonzola (MI), Via...' oppure,
    dentro un titolo/descrizione piu' lungo, '...sito in Luzzara (RE)...'.

    permetti_ancorato=True e' sicuro SOLO per campi che iniziano con il
    comune (tipicamente 'indirizzo'): su un titolo come 'Capannone a Ravenna
    (RA)' l'ancoraggio permissivo catturerebbe 'Capannone a Ravenna' intero
    come comune. Per titolo/descrizione va sempre usato permetti_ancorato=False,
    cosi' si usa solo il pattern libero (nomi propri maiuscoli)."""
    if not testo:
        return None

    if permetti_ancorato:
        m = _RE_COMUNE_PR_ANCORATO.match(testo)
        if m:
            comune = util.titolo_comune(_ripulisci_comune_estratto(m.group(1).strip(" ,")))
            sigla = m.group(2).upper()
            if comune and len(comune) <= 40:
                return comune, sigla

    m = _RE_COMUNE_PR_LIBERO.search(testo)
    if m:
        comune = util.titolo_comune(_ripulisci_comune_estratto(m.group(1).strip(" ,")))
        sigla = m.group(2).upper()
        if comune and len(comune) <= 40:
            return comune, sigla

    return None


def _da_citta_completa(testo: Optional[str]) -> Optional[str]:
    """vgi: 'Toscana > Pisa > San Giuliano Terme' -> ultimo pezzo."""
    if not testo or ">" not in testo:
        return None
    ultimo = testo.split(">")[-1].strip()
    return util.titolo_comune(ultimo) if ultimo else None


def _da_titolo_idealista(testo: Optional[str]) -> Optional[str]:
    """'Edificio a Isola, Milano' -> 'Milano' (ultimo segmento dopo virgola,
    quando il titolo contiene ' a <Zona>, <Comune>')."""
    if not testo or " a " not in testo.lower():
        return None
    if "," not in testo:
        return None
    ultimo = testo.rsplit(",", 1)[-1].strip()
    return util.titolo_comune(ultimo) if ultimo and 2 <= len(ultimo) <= 40 else None


def sigla_provincia(comune: Optional[str], sigla_da_testo: Optional[str] = None) -> Optional[str]:
    if sigla_da_testo:
        return sigla_da_testo.upper()
    if not comune:
        return None
    return COMUNE_PROVINCIA.get(util.norm_chiave(comune))


def estrai_comune_provincia(
    *,
    comune_esplicito: Optional[str] = None,
    provincia_esplicita: Optional[str] = None,
    indirizzo: Optional[str] = None,
    titolo: Optional[str] = None,
    citta_completa: Optional[str] = None,
    city_fallback: Optional[str] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Applica l'ordine di affidabilita' del §3.0.4 e ritorna (Campo comune,
    Campo provincia). Non solleva mai: se nulla e' riconoscibile, i Campi
    hanno valore None con confidenza bassa (DATI_INSUFFICIENTI a valle,
    mai uno scarto per questo motivo soltanto)."""

    comune_pulito = util.pulisci(comune_esplicito)
    if comune_pulito:
        comune = util.titolo_comune(comune_pulito)
        prov_pulita = util.pulisci(provincia_esplicita)
        sigla = sigla_provincia(prov_pulita or comune, None)
        # provincia esplicita puo' essere nome esteso (immobiliare): se non
        # e' una sigla di 2 lettere, la cerchiamo nella tabella.
        if prov_pulita and len(prov_pulita) == 2 and prov_pulita.isalpha():
            sigla = prov_pulita.upper()
        c_comune = campo(comune, "json", "alta")
        c_prov = campo(sigla, "json" if sigla else "stima", "alta" if sigla else "bassa")
        return c_comune, c_prov

    trovato = _da_pattern_comune_pr(indirizzo, permetti_ancorato=True) or _da_pattern_comune_pr(titolo, permetti_ancorato=False)
    if trovato:
        comune, sigla = trovato
        return campo(comune, "regex_testo", "alta"), campo(sigla, "regex_testo", "alta")

    comune_cc = _da_citta_completa(citta_completa)
    if comune_cc:
        sigla = sigla_provincia(comune_cc)
        return (
            campo(comune_cc, "regex_testo", "media"),
            campo(sigla, "regex_testo" if sigla else "stima", "media" if sigla else "bassa"),
        )

    comune_titolo = _da_titolo_idealista(titolo)
    if comune_titolo:
        sigla = sigla_provincia(comune_titolo)
        return (
            campo(comune_titolo, "regex_testo", "media"),
            campo(sigla, "regex_testo" if sigla else "stima", "media" if sigla else "bassa"),
        )

    city = util.pulisci(city_fallback)
    if city:
        comune = util.titolo_comune(city)
        sigla = sigla_provincia(comune)
        return (
            campo(comune, "json", "bassa", nota="city e' la citta' di ricerca dello scraper, non necessariamente il comune reale"),
            campo(sigla, "stima", "bassa"),
        )

    return campo(None, "stima", "bassa"), campo(None, "stima", "bassa")


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def escluso_per_area(comune: Optional[str], provincia_sigla: Optional[str], comuni_esclusi: list[str]) -> bool:
    """SCELTA DI PROGETTO (documentata anche in config.yaml e nel README):
    l'esclusione vale per nome comune ESATTO e per l'intera provincia dei
    comuni esclusi, per rispettare l'intento dichiarato nella spec
    ("escludi Roma e Centro-Sud/Adriatico"), non solo il capoluogo."""
    if not comune and not provincia_sigla:
        return False
    esclusi_norm = {util.norm_chiave(c) for c in comuni_esclusi}
    if comune and util.norm_chiave(comune) in esclusi_norm:
        return True
    if provincia_sigla:
        sigle_escluse = {COMUNE_PROVINCIA.get(c) for c in esclusi_norm}
        if provincia_sigla.upper() in {s for s in sigle_escluse if s}:
            return True
    return False


def valuta_perimetro(
    *,
    comune: Optional[str],
    provincia_sigla: Optional[str],
    lat: Optional[float],
    lon: Optional[float],
    comuni_target: list[str],
    comuni_esclusi: list[str],
    raggio_km: float,
) -> dict[str, Any]:
    """Ritorna {in_perimetro, motivo, confidenza}. Non scarta mai per
    incertezza: un comune ignoto ritorna in_perimetro=None (da approfondire),
    non False."""
    if escluso_per_area(comune, provincia_sigla, comuni_esclusi):
        return {"in_perimetro": False, "motivo": "comune o provincia in comuni_esclusi", "confidenza": "alta"}

    target_norm = {util.norm_chiave(c) for c in comuni_target}
    if comune and util.norm_chiave(comune) in target_norm:
        return {"in_perimetro": True, "motivo": "comune target esatto", "confidenza": "alta"}

    if lat is not None and lon is not None:
        migliore = None
        for nome_t, coord_t in CENTROIDI_TARGET.items():
            if nome_t in {util.norm_chiave(c) for c in comuni_esclusi}:
                continue
            d = _haversine_km((lat, lon), coord_t)
            if migliore is None or d < migliore[1]:
                migliore = (nome_t, d)
        if migliore and migliore[1] <= raggio_km:
            return {
                "in_perimetro": True,
                "motivo": f"a {migliore[1]:.1f} km da {util.titolo_comune(migliore[0])} (entro raggio_km)",
                "confidenza": "alta",
            }
        if migliore and migliore[1] > raggio_km:
            return {
                "in_perimetro": False,
                "motivo": f"a {migliore[1]:.1f} km dal target piu' vicino ({util.titolo_comune(migliore[0])}), oltre raggio_km",
                "confidenza": "media",
            }

    if provincia_sigla:
        province_target = {
            COMUNE_PROVINCIA.get(util.norm_chiave(c)) for c in comuni_target
        }
        province_target.discard(None)
        if provincia_sigla.upper() in province_target:
            return {
                "in_perimetro": True,
                "motivo": "stessa provincia di un comune target (raggio approssimato per provincia, coordinate assenti)",
                "confidenza": "media",
            }
        return {"in_perimetro": False, "motivo": "provincia fuori dalle province target", "confidenza": "media"}

    if not comune:
        return {"in_perimetro": None, "motivo": "comune non determinabile", "confidenza": "bassa"}

    return {"in_perimetro": None, "motivo": f"comune '{comune}' non riconosciuto tra i target ne' per provincia", "confidenza": "bassa"}
