"""Estrazione di attributi da testo libero, solo per indizi espliciti, mai
per inferenza (§3.0.7). Tutto codice deterministico a parole chiave: nessuna
lettura "di significato" qui, quella e' riservata al modello (§7.3) o al
ripiego euristico dichiarato (giudizi_fallback.py).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from . import util
from .campo import campo

# --------------------------------------------------------------------------- categoria


_PAROLE_GARAGE = ("garage", "box auto", "posto auto", "posto per macchina", "autorimessa")
_PAROLE_MOBILI = ("mobili", "macchinari", "utensili", "materie prime")
_PAROLE_INDUSTRIALI_ORDINATE = (
    ("capannone", "capannone"),
    ("opificio", "opificio"),
    ("laboratorio", "laboratorio"),
    ("deposito", "deposito"),
    ("magazzino", "magazzino"),
)
_PAROLE_RESIDENZIALI = (
    "abitazione", "appartamento", "casa singola", "casa indipendente",
    "villa", "residenziale", "edificio residenziale", "monolocale",
    "bilocale", "trilocale",
)
_PAROLE_INDIZI_INDUSTRIALI = (
    "industriale", "artigianale", "capannone", "laboratorio", "opificio",
    "magazzino", "deposito", "produttivo", "logistic",
)


def classifica_categoria(
    *,
    indizio_fonte: Optional[str] = None,
    titolo: Optional[str] = None,
    descrizione: Optional[str] = None,
    tipologia: Optional[str] = None,
) -> dict[str, Any]:
    """indizio_fonte: hint gia' certo dalla fonte ("garage" per idealista
    vendita-garage, "mobili" per vgi categoria MOBILI). Altrimenti si
    classifica a parole chiave su titolo+descrizione+tipologia."""
    if indizio_fonte == "garage":
        return campo("garage", "json", "alta")
    if indizio_fonte == "mobili":
        return campo("mobili", "json", "alta")

    testo = util.norm_testo(f"{titolo or ''} {tipologia or ''} {descrizione or ''}")
    if not testo.strip():
        return campo("altro", "stima", "bassa")

    for parola, categoria in _PAROLE_INDUSTRIALI_ORDINATE:
        if parola in testo:
            return campo(categoria, "regex_testo", "media")

    for parola in _PAROLE_GARAGE:
        if parola in testo:
            return campo("garage", "regex_testo", "media")

    for parola in _PAROLE_RESIDENZIALI:
        if parola in testo:
            return campo("residenziale", "regex_testo", "media")

    for parola in _PAROLE_MOBILI:
        if parola in testo:
            return campo("mobili", "regex_testo", "media")

    return campo("altro", "stima", "bassa")


def ha_indizi_industriali(testo_completo: Optional[str]) -> bool:
    t = util.norm_testo(testo_completo)
    return any(p in t for p in _PAROLE_INDIZI_INDUSTRIALI)


# --------------------------------------------------------------------------- diritto


_RE_QUOTA = re.compile(r"quota\s+(?:indivisa\s+)?di\s+(\d+)\s*/\s*(\d+)", re.IGNORECASE)
_RE_QUOTA_ALT = re.compile(r"quota\s+di\s+comproprieta\'?\s+di\s+(\d+)\s*/\s*(\d+)", re.IGNORECASE)


def estrai_diritto(testo_completo: Optional[str]) -> dict[str, Any]:
    """'piena proprieta' / 'nuda proprieta' / 'Quota di 2/6' / diritto di
    superficie. Ritorna un Campo; None se non menzionato esplicitamente."""
    if not testo_completo:
        return campo(None, "stima", "bassa")
    t = util.norm_testo(testo_completo)

    m = _RE_QUOTA.search(t) or _RE_QUOTA_ALT.search(t)
    if m:
        return campo(f"quota indivisa {m.group(1)}/{m.group(2)}", "regex_testo", "alta")

    if "nuda proprieta" in t:
        return campo("nuda proprietà", "regex_testo", "alta")

    if "diritto di superficie" in t:
        return campo("diritto di superficie", "regex_testo", "media")

    if "piena proprieta" in t:
        return campo("piena proprietà", "regex_testo", "alta")

    return campo(None, "stima", "bassa")


# --------------------------------------------------------------------------- checklist due diligence (indizi, mai un filtro)


_INDIZI_DD = [
    ("amianto", ("fibrocemento", "eternit", "amianto"), "media"),
    ("bonifica", ("ex galvanica", "ex carrozzeria", "ex distributore", "ex tintoria", "ex conceria"), "media"),
    ("occupazione", ("occupato da terzi", "locato a", "con inquilino", "in affitto a terzi"), "media"),
    ("umidita_risalita", ("umidita di risalita", "infiltrazioni"), "bassa"),
    ("difformita", ("difformita", "abuso edilizio", "sanatoria", "condono"), "media"),
]


def checklist_dd_da_testo(testo_completo: Optional[str], *, piano: Optional[str] = None) -> list[dict[str, Any]]:
    """Indizi testuali per la checklist di due diligence (§Stadio1, knock-out
    non automatizzabili). Non scarta mai: sono solo allerta + indizio."""
    risultati: list[dict[str, Any]] = []
    t = util.norm_testo(testo_completo)
    for voce, parole, allerta in _INDIZI_DD:
        for p in parole:
            if p in t:
                risultati.append({"voce": voce, "allerta": allerta, "indizio": p})
                break
    if piano and any(p in util.norm_testo(piano) for p in ("interrato", "seminterrato")):
        risultati.append({
            "voce": "umidita_risalita",
            "allerta": "media",
            "indizio": f"piano '{piano}': tenere d'occhio l'umidita' di risalita",
        })
    return risultati


# --------------------------------------------------------------------------- attributi puntuali dal testo (solo se espliciti)


_RE_LOTTO = re.compile(r"lotto\s*(?:n(?:umero)?\.?\s*)?[:\-]?\s*([A-Za-z0-9]+)", re.IGNORECASE)


def estrai_lotto(testo_completo: Optional[str]) -> Optional[str]:
    """'Proc. 36/2024, Lotto UNICO' -> 'UNICO'; 'Lotto n. 2: ...' -> '2';
    'LOTTO N. 1: ...' -> '1'. Il 'n.'/'n'/'numero' facoltativo tra 'lotto' e
    il numero e' fondamentale: senza gestirlo, 'Lotto n. 2' verrebbe letto
    come lotto 'N' invece che '2', e lotti diversi della stessa procedura
    finirebbero per sembrare identici (§9.9: i 5 lotti di Ancona 60/2024
    NON vanno uniti)."""
    if not testo_completo:
        return None
    m = _RE_LOTTO.search(testo_completo)
    return m.group(1).upper() if m else None


def normalizza_procedura(tribunale: Optional[str], procedura_grezza: Optional[str], numero: Optional[str] = None, anno: Optional[str] = None) -> Optional[str]:
    """Ritorna 'N/AAAA' quando possibile, altrimenti il testo pulito."""
    if numero and anno:
        return f"{util.pulisci(numero)}/{util.pulisci(anno)}"
    if not procedura_grezza:
        return None
    m = re.search(r"(\d+)\s*/\s*(\d{4})", procedura_grezza)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return util.pulisci(procedura_grezza)
