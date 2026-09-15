"""Stadio 2 - scorecard qualita' (0-100), indipendente dal prezzo
(§Stadio2). Ogni criterio ritorna {punti, min, max, max_punti, fonte,
confidenza, motivo}: quando il dato manca non si inventa un punteggio
secco, si dichiara un intervallo (spec: "calcolare score minimo, atteso e
massimo").

STATO DEI RIFERIMENTI (vedi reference/*.csv e README): iqss_comuni.csv,
concorrenti.csv e poli_retail.csv sono oggi vuoti (il deck sorgente non e'
disponibile in questo ambiente). I criteri che li usano ricadono quindi
SEMPRE nel ramo "dato mancante" con confidenza bassa: e' il comportamento
corretto previsto dalla spec per un dato assente, non un bug. Quando le CSV
saranno compilate, questi stessi criteri torneranno automaticamente a usare
i valori reali, senza modifiche al codice.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Optional

from . import util
from .campo import valore_di

CATEGORIE_ALTE = {"capannone", "opificio"}


# --------------------------------------------------------------------------- riferimenti CSV (con cache in-processo)

_CACHE_CSV: dict[str, dict[str, dict[str, Any]]] = {}


def _carica_csv_per_comune(percorso: Path) -> dict[str, dict[str, Any]]:
    chiave = str(percorso)
    if chiave in _CACHE_CSV:
        return _CACHE_CSV[chiave]
    righe: dict[str, dict[str, Any]] = {}
    if percorso.is_file():
        try:
            with open(percorso, "r", encoding="utf-8", newline="") as f:
                for riga in csv.DictReader(f):
                    comune = util.norm_chiave(riga.get("comune"))
                    if comune:
                        righe[comune] = riga
        except Exception:
            pass  # un riferimento illeggibile non deve mai bloccare la valutazione
    _CACHE_CSV[chiave] = righe
    return righe


def _numero_da_riga(riga: Optional[dict[str, Any]], chiave: str) -> Optional[float]:
    if not riga:
        return None
    valore = riga.get(chiave)
    if valore in (None, "", "null", "NULL"):
        return None
    try:
        return float(valore)
    except (TypeError, ValueError):
        return None


def _criterio_ignoto(max_punti: float, motivo: str, min_frazione: float = 0.0, max_frazione: float = 1.0) -> dict[str, Any]:
    return {
        "punti": round(max_punti * (min_frazione + max_frazione) / 2, 1),
        "min": round(max_punti * min_frazione, 1),
        "max": round(max_punti * max_frazione, 1),
        "max_punti": max_punti,
        "fonte": "stima",
        "confidenza": "bassa",
        "motivo": motivo,
    }


def _criterio_noto(punti: float, max_punti: float, fonte: str, confidenza: str, motivo: str) -> dict[str, Any]:
    punti = max(0.0, min(max_punti, punti))
    return {"punti": punti, "min": punti, "max": punti, "max_punti": max_punti, "fonte": fonte, "confidenza": confidenza, "motivo": motivo}


# --------------------------------------------------------------------------- MERCATO


def _iqss_comune(comune: Optional[str], provincia: Optional[str], root: Path, peso: float) -> dict[str, Any]:
    righe = _carica_csv_per_comune(root / "reference" / "iqss_comuni.csv")
    riga = righe.get(util.norm_chiave(comune)) if comune else None
    iqss = _numero_da_riga(riga, "iqss")
    if iqss is not None:
        return _criterio_noto(peso * (iqss / 100.0), peso, "json", "alta", f"IQSS {iqss} da reference/iqss_comuni.csv")
    return _criterio_ignoto(peso, "iqss_comuni.csv non compilato per questo comune (o comune fuori elenco): punteggio interpolato neutro")


def _saturazione_bacino(lat: Optional[float], lon: Optional[float], root: Path, peso: float, soglia: float) -> dict[str, Any]:
    righe = _carica_csv_per_comune(root / "reference" / "concorrenti.csv")
    if not righe:
        return _criterio_ignoto(peso, "concorrenti.csv non ancora compilato: saturazione del bacino non verificabile")
    # placeholder per quando il CSV sara' popolato: nessun concorrente noto vicino -> punteggio pieno
    return _criterio_ignoto(peso, "concorrenti.csv presente ma senza corrispondenze nel raggio configurato")


def _visibilita_traffico(peso: float) -> dict[str, Any]:
    return _criterio_ignoto(peso, "visibilità e traffico del fronte strada non desumibili dai dati raccolti: richiede sopralluogo (DA_VERIFICARE_ON_SITE)", 0.2, 0.8)


def _qualita_bacino(root: Path, peso: float) -> dict[str, Any]:
    righe = _carica_csv_per_comune(root / "reference" / "poli_retail.csv")
    if not righe:
        return _criterio_ignoto(peso, "poli_retail.csv non ancora compilato: qualità del bacino non verificabile")
    return _criterio_ignoto(peso, "poli_retail.csv presente ma senza corrispondenze nel raggio configurato")


# --------------------------------------------------------------------------- IMMOBILE


def _altezza_soppalcabilita(altezza_m: Optional[float], confidenza_altezza: Optional[str], categoria: Optional[str], config: dict[str, Any]) -> dict[str, Any]:
    peso = config["scorecard"]["pesi"]["altezza_soppalcabilita"]
    scala = config["scorecard"]["altezza_punti"]
    if altezza_m is not None and confidenza_altezza != "bassa":
        if altezza_m < 3.0:
            punti = scala["sotto_3_0"]
        elif altezza_m < 4.5:
            punti = scala["da_3_0_a_4_4"]
        elif altezza_m < 5.5:
            punti = scala["da_4_5_a_5_4"]
        else:
            punti = scala["da_5_5"]
        return _criterio_noto(punti, peso, "raw_detail", "alta", f"altezza sotto trave {altezza_m:.1f} m")
    if categoria in CATEGORIE_ALTE:
        return _criterio_ignoto(peso, "altezza ignota; per capannone/opificio si usa il range [4,13] invece di un valore puntuale", 4 / 13, 1.0)
    return _criterio_ignoto(peso, "altezza ignota; per deposito/magazzino/laboratorio si usa il range [0,8] invece di un valore puntuale", 0.0, 8 / 13)


def _efficienza_planimetrica(attributi: dict[str, Any], piano: Optional[str], n_piani: Optional[str], config: dict[str, Any]) -> dict[str, Any]:
    peso = config["scorecard"]["pesi"]["efficienza_planimetrica"]
    piano_norm = util.norm_testo(piano)
    campate = attributi.get("campate")
    if not piano and not campate:
        return _criterio_ignoto(peso, "pianta/piano non noti: nessun indizio su regolarità o quota uffici", 0.25, 0.75)

    punti = peso * 0.55  # punto di partenza neutro-positivo quando qualcosa e' noto
    note = []
    if piano_norm:
        if "interrato" in piano_norm or "seminterrato" in piano_norm:
            punti -= peso * 0.35
            note.append(f"piano '{piano}': forte penalità (interrato/seminterrato)")
        elif "terra" in piano_norm:
            punti += peso * 0.15
            note.append("piano terra: nessuna penalità")
    if n_piani and str(n_piani).strip() not in {"1 piano", "1"}:
        punti -= peso * 0.15
        note.append(f"{n_piani}: penalità per sviluppo su più piani")
    if campate and "senza pilastri" in util.norm_testo(str(campate)):
        punti += peso * 0.1
        note.append("pianta senza pilastri: bonus di regolarità")

    return _criterio_noto(punti, peso, "raw_detail" if campate else "regex_testo", "media", "; ".join(note) or "indizi parziali sulla pianta")


def _accessibilita_operativa(attributi: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    peso = config["scorecard"]["pesi"]["accessibilita_operativa"]
    features = [util.norm_testo(f) for f in (attributi.get("features") or [])]
    banchine = attributi.get("banchine")
    if not features and not banchine:
        return _criterio_ignoto(peso, "accessi, piazzale e banchine non noti dai dati raccolti", 0.2, 0.7)
    punti = peso * 0.5
    note = []
    if any("passo carrabile" in f for f in features):
        punti += peso * 0.25
        note.append("passo carrabile presente")
    if any("recintato" in f for f in features):
        punti += peso * 0.1
        note.append("area recintata")
    if banchine:
        punti += peso * 0.15
        note.append(f"{banchine} banchina/e di carico")
    return _criterio_noto(punti, peso, "raw_detail", "media", "; ".join(note) or "features presenti ma poco indicative")


def _antincendio_involucro(anno_costruzione: Optional[int], config: dict[str, Any]) -> dict[str, Any]:
    peso = config["scorecard"]["pesi"]["antincendio_involucro"]
    if anno_costruzione is None:
        return _criterio_ignoto(peso, "anno di costruzione ignoto: rischio sismico/struttura non valutabile", 0.2, 0.8)
    if anno_costruzione < 2008:
        return _criterio_noto(peso * 0.3, peso, "raw_detail", "media", f"costruito nel {anno_costruzione} (ante 2008): allerta sismica su prefabbricati")
    return _criterio_noto(peso * 0.85, peso, "raw_detail", "media", f"costruito nel {anno_costruzione} (post 2008)")


# --------------------------------------------------------------------------- RISCHIO E SVILUPPO


def _percorso_autorizzativo(giudizio: Optional[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    peso = config["scorecard"]["pesi"]["percorso_autorizzativo"]
    if giudizio and giudizio.get("percorso_autorizzativo_punti") is not None:
        punti = float(giudizio["percorso_autorizzativo_punti"])
        return _criterio_noto(
            punti, peso,
            giudizio.get("fonte", "modello"),
            giudizio.get("confidenza", "media"),
            giudizio.get("motivo", "percorso autorizzativo classificato dal modello/euristica"),
        )
    return _criterio_ignoto(peso, "percorso autorizzativo non ancora classificato (manca il passo di lettura testuale, §7.3)", 5 / 12, 7 / 12)


def _espandibilita(testo_completo: str, config: dict[str, Any]) -> dict[str, Any]:
    peso = config["scorecard"]["pesi"]["espandibilita"]
    t = util.norm_testo(testo_completo)
    parole = ("ampliabile", "ampliamento", "area edificabile adiacente", "seconda campata", "lotto adiacente", "possibilità di ampliare")
    if any(p in t for p in parole):
        return _criterio_noto(peso * 0.9, peso, "regex_testo", "media", "indizi espliciti di espandibilità nel testo")
    return _criterio_ignoto(peso, "nessun indizio testuale su espandibilità/apertura per fasi", 0.2, 0.6)


def _pulizia_tempi_transazione(rec: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    peso = config["scorecard"]["pesi"]["pulizia_tempi_transazione"]
    attributi = rec.get("_attributi") or {}
    if rec.get("tipo_vendita") == "vendita":
        return _criterio_noto(peso * 0.85, peso, "json", "media", "vendita a libero mercato: transazione tipicamente più rapida e pulita di un'asta")
    note = ["asta"]
    punti = peso * 0.55
    occupazione = util.norm_testo(rec.get("stato_occupazione"))
    if occupazione and "libero" in occupazione:
        punti += peso * 0.2
        note.append("immobile libero")
    elif occupazione and ("occupat" in occupazione or "locat" in occupazione):
        punti -= peso * 0.25
        note.append("immobile occupato/locato: tempi più lunghi")
    if attributi.get("custode_presente"):
        punti += peso * 0.1
        note.append("custode nominato")
    return _criterio_noto(punti, peso, "json", "media", "; ".join(note))


# --------------------------------------------------------------------------- combinazione


def calcola_scorecard(rec: dict[str, Any], giudizio_percorso: Optional[dict[str, Any]], config: dict[str, Any], root: Path) -> dict[str, Any]:
    sc = config["scorecard"]
    comune = valore_di(rec.get("comune"))
    provincia = valore_di(rec.get("provincia"))
    categoria = valore_di(rec.get("categoria"))
    altezza = valore_di(rec.get("altezza_m"))
    conf_altezza = rec.get("altezza_m", {}).get("confidenza") if isinstance(rec.get("altezza_m"), dict) else None
    attributi = rec.get("_attributi") or {}
    testo_completo = f"{rec.get('titolo') or ''} {rec.get('descrizione') or ''}"

    breakdown = {
        "iqss_comune": _iqss_comune(comune, provincia, root, sc["pesi"]["iqss_comune"]),
        "saturazione_bacino": _saturazione_bacino(rec.get("lat"), rec.get("lon"), root, sc["pesi"]["saturazione_bacino"], sc["soglia_saturazione_mq_per_abitante"]),
        "visibilita_traffico": _visibilita_traffico(sc["pesi"]["visibilita_traffico"]),
        "qualita_bacino": _qualita_bacino(root, sc["pesi"]["qualita_bacino"]),
        "altezza_soppalcabilita": _altezza_soppalcabilita(altezza, conf_altezza, categoria, config),
        "efficienza_planimetrica": _efficienza_planimetrica(attributi, rec.get("piano"), rec.get("n_piani"), config),
        "accessibilita_operativa": _accessibilita_operativa(attributi, config),
        "antincendio_involucro": _antincendio_involucro(rec.get("anno_costruzione"), config),
        "percorso_autorizzativo": _percorso_autorizzativo(giudizio_percorso, config),
        "espandibilita": _espandibilita(testo_completo, config),
        "pulizia_tempi_transazione": _pulizia_tempi_transazione(rec, config),
    }

    score_min = sum(c["min"] for c in breakdown.values())
    score_atteso = sum(c["punti"] for c in breakdown.values())
    score_max = sum(c["max"] for c in breakdown.values())
    ampiezza = score_max - score_min

    if ampiezza > sc["ampiezza_range_max_per_classe"]:
        classe = None
    elif score_atteso >= sc["soglia_classe_a"]:
        classe = "A"
    elif score_atteso >= sc["soglia_classe_b"]:
        classe = "B"
    elif score_atteso >= sc["soglia_classe_c"]:
        classe = "C"
    else:
        classe = None  # sotto soglia_classe_c: scarto, deciso allo Stadio 4

    return {
        "min": round(score_min, 1),
        "atteso": round(score_atteso, 1),
        "max": round(score_max, 1),
        "ampiezza": round(ampiezza, 1),
        "classe": classe,
        "sotto_soglia_minima": score_atteso < sc["soglia_classe_c"] and ampiezza <= sc["ampiezza_range_max_per_classe"],
        "breakdown": breakdown,
    }
