"""Parser deterministici di prezzi, date e superfici da testo libero (§3.0.2-5).

Nessuna di queste funzioni chiama il modello: sono tutte regole esplicite,
come richiesto dal vincolo "parsing, calcoli... sono codice deterministico".
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from . import util

# --------------------------------------------------------------------------- prezzi


def norma_prezzo(grezzo: Any) -> Optional[int]:
    """Converte un prezzo scritto in vari formati italiani/misti in intero.

    Esempi coperti dalla spec (§3.0.2):
        "176.000,00"  -> 176000   (punto = migliaia, virgola = decimali)
        "€ 125.250"   -> 125250
        "6.000.000€"  -> 6000000  (piu' punti: tutti migliaia)
        "€ 20.800,00" -> 20800
        "895.000"     -> 895000   (un solo punto, 3 cifre dopo: migliaia)
        "31800.00"    -> 31800    (un solo punto, 2 cifre dopo: decimali)
        None / "" / "prezzo su richiesta" -> None
    """
    if grezzo is None:
        return None
    if isinstance(grezzo, bool):
        return None
    if isinstance(grezzo, (int, float)):
        return int(round(grezzo))

    s = str(grezzo).strip()
    if not s:
        return None
    basso = s.lower()
    if "richiesta" in basso or "trattativa" in basso or basso in {
        "null", "none", "n.d.", "nd", "-", "n/a",
    }:
        return None

    s = re.sub(r"[€$£]", "", s).strip()
    if not s:
        return None

    ha_virgola = "," in s
    n_punti = s.count(".")

    if ha_virgola:
        # I punti (se presenti) sono separatori di migliaia; la virgola e'
        # il separatore decimale.
        s2 = s.replace(".", "").replace(",", ".")
    elif n_punti >= 2:
        # Piu' di un punto: non puo' essere un decimale, sono tutti migliaia.
        s2 = s.replace(".", "")
    elif n_punti == 1:
        frazione = s.split(".")[-1]
        if len(frazione) == 3:
            # Punto come separatore di migliaia (es. "895.000").
            s2 = s.replace(".", "")
        else:
            # Punto decimale (es. "31800.00" o "1234.5").
            s2 = s
    else:
        s2 = s

    s2 = re.sub(r"[^0-9.\-]", "", s2)
    if not s2 or s2 in {"-", "."}:
        return None
    try:
        valore = float(s2)
    except ValueError:
        return None
    return int(round(valore))


# --------------------------------------------------------------------------- numeri italiani generici


def norma_numero_it(grezzo: Any) -> Optional[float]:
    """'1.250' -> 1250.0, '233,5' -> 233.5, '12000' -> 12000.0.

    Stessa euristica di norma_prezzo per il punto ambiguo: con un solo punto
    e tre cifre dopo e' separatore di migliaia ("1.250" -> 1250), altrimenti
    e' decimale ("233.5" o "233,5" -> 233.5).
    """
    if grezzo is None:
        return None
    if isinstance(grezzo, (int, float)) and not isinstance(grezzo, bool):
        return float(grezzo)
    s = str(grezzo).strip()
    if not s:
        return None
    s = re.sub(r"[^0-9.,\-]", "", s)
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") >= 2:
        s = s.replace(".", "")
    elif s.count(".") == 1:
        frazione = s.split(".")[-1]
        if len(frazione) == 3:
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------- date


_RE_ISO_TZ = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_RE_DATA_IT = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_RE_ORA_IT = re.compile(r"(\d{1,2}):(\d{2})")


def norma_data(grezzo: Any) -> Optional[datetime]:
    """Legge date in formato "15/09/2026 - 15:30", "17/09/2026 h 12:00",
    "22/10/2026, 15:30" oppure ISO 8601. Ritorna un datetime con fuso
    Europe/Rome, o None se non interpretabile (§3.0.3)."""
    if grezzo is None:
        return None
    if isinstance(grezzo, datetime):
        return util.a_roma(grezzo)

    s = str(grezzo).strip()
    if not s:
        return None

    if _RE_ISO_TZ.match(s):
        candidato = s.replace("Z", "+00:00")
        try:
            return util.a_roma(datetime.fromisoformat(candidato))
        except ValueError:
            pass

    m_data = _RE_DATA_IT.search(s)
    if not m_data:
        return None
    giorno, mese, anno = (int(m_data.group(i)) for i in (1, 2, 3))
    m_ora = _RE_ORA_IT.search(s)
    ora, minuto = (int(m_ora.group(1)), int(m_ora.group(2))) if m_ora else (0, 0)
    try:
        naive = datetime(anno, mese, giorno, ora, minuto)
    except ValueError:
        return None
    return util.a_roma(naive)


# --------------------------------------------------------------------------- superfici da testo (§3.0.5)


_NUM = r"(\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+(?:,\d+)?)"
_ETICHETTE = ("coperta", "scoperta", "commerciale", "catastale")

_PAT_ETICHETTATE = [
    re.compile(
        rf"sup(?:erficie)?\.?\s*({'|'.join(_ETICHETTE)})[^0-9]{{0,20}}{_NUM}\s*(?:mq|m\s*²|m2)\b",
        re.IGNORECASE,
    ),
]
_PAT_GENERICHE = [
    re.compile(rf"{_NUM}\s*(?:mq|m\s*²|m2)\b", re.IGNORECASE),
    re.compile(rf"\bmq\.?\s*{_NUM}\b", re.IGNORECASE),
    re.compile(rf"m\s*²\.?\s*{_NUM}\b", re.IGNORECASE),
    re.compile(rf"{_NUM}\s*metri\s*quadrat\w*", re.IGNORECASE),
]

SUP_MIN_PLAUSIBILE = 5
SUP_MAX_PLAUSIBILE = 100_000


def estrai_superfici_da_testo(testo: Optional[str]) -> list[dict[str, Any]]:
    """Cerca pattern di superficie nel testo libero. Ritorna una lista di
    {valore, etichetta, confidenza, testo}, una per ogni valore distinto
    trovato (§3.0.5: "se compaiono piu' superfici, tenerle tutte").

    Confidenza "bassa" per un valore fuori dal range plausibile [5,100000]
    m², oppure quando il valore viene da un pattern SENZA etichetta esplicita
    e ci sono piu' candidati non etichettati diversi tra loro (ambiguo: non
    si sa quale sia quello giusto). Un valore con etichetta esplicita
    (coperta/scoperta/commerciale/catastale) non e' mai reso ambiguo dalla
    presenza di altre etichette: sono grandezze diverse per definizione.
    Altrimenti "media" (e' comunque testo, mai "alta": quella e' riservata
    ai campi strutturati del JSON).
    """
    if not testo:
        return []

    trovate: list[tuple[float, Optional[str], str]] = []
    for pat in _PAT_ETICHETTATE:
        for m in pat.finditer(testo):
            valore = norma_numero_it(m.group(2))
            if valore is not None:
                trovate.append((valore, m.group(1).lower(), m.group(0).strip()))
    for pat in _PAT_GENERICHE:
        for m in pat.finditer(testo):
            valore = norma_numero_it(m.group(1))
            if valore is not None:
                trovate.append((valore, None, m.group(0).strip()))

    if not trovate:
        return []

    per_valore: dict[float, tuple[float, Optional[str], str]] = {}
    for valore, etichetta, snippet in trovate:
        chiave = round(valore, 1)
        precedente = per_valore.get(chiave)
        if precedente is None or (etichetta and not precedente[1]):
            per_valore[chiave] = (valore, etichetta, snippet)

    # Ambiguita': solo tra i valori SENZA etichetta esplicita (non sappiamo
    # a cosa si riferiscano). Valori con etichette diverse convivono.
    non_etichettati_plausibili = {
        round(v[0])
        for v in per_valore.values()
        if v[1] is None and SUP_MIN_PLAUSIBILE <= v[0] <= SUP_MAX_PLAUSIBILE
    }
    ambiguo = len(non_etichettati_plausibili) > 1

    risultato = []
    for valore, etichetta, snippet in per_valore.values():
        plausibile = SUP_MIN_PLAUSIBILE <= valore <= SUP_MAX_PLAUSIBILE
        e_ambiguo = ambiguo and etichetta is None
        confidenza = "bassa" if (not plausibile or e_ambiguo) else "media"
        risultato.append(
            {
                "valore": valore,
                "etichetta": etichetta or "coperta",
                "confidenza": confidenza,
                "testo": snippet,
            }
        )
    # Ordine stabile: valore decrescente (la piu' "importante", tipicamente
    # la lorda/coperta, in cima).
    risultato.sort(key=lambda r: (-r["valore"]))
    return risultato


# --------------------------------------------------------------------------- altezze


def norma_altezza(grezzo: Any) -> Optional[float]:
    """'7,1 m' -> 7.1 ; '650 m' -> 650.0 (la plausibilita' si valuta a parte,
    con altezza_plausibile_min_m/max_m di config.yaml)."""
    if grezzo is None:
        return None
    if isinstance(grezzo, (int, float)) and not isinstance(grezzo, bool):
        return float(grezzo)
    s = str(grezzo).strip().lower()
    s = s.replace("m", "").replace("²", "").strip()
    s = s.replace(",", ".")
    s = re.sub(r"[^0-9.\-]", "", s)
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None
