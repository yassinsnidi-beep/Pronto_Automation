"""Caricamento di config.yaml, con default di ripiego e hash per il report.

Il file config.yaml e' la fonte di verita' per tutti i parametri tarabili
(§6 della spec). Qui carichiamo il file con PyYAML (l'unica dipendenza non
di libreria standard della skill, dichiarata in requirements.txt) e lo
fondiamo con dei default hardcoded, cosi' un config.yaml parziale o con
qualche chiave mancante non fa crashare la run: si usa il default per quella
sola chiave e si prosegue.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as _err:  # pragma: no cover
    raise ImportError(
        "Manca il pacchetto PyYAML. Installare le dipendenze con: "
        "pip install -r requirements.txt"
    ) from _err


# Default di ripiego, usati solo per le chiavi assenti da config.yaml.
# Tenuti minimi e allineati ai valori di default della spec (§6): il file
# config.yaml consegnato con la skill li scrive gia' tutti esplicitamente.
DEFAULT: dict[str, Any] = {
    "percorsi": {
        "input_aste": "opportunities/INPUT/ASTE",
        "input_immid": "opportunities/INPUT/IMM_ID/current_week",
        "output_dir": "opportunities/OUTPUT",
        "formato_nome_report": "report_{AAAA}_{MM}_{GG}.json",
    },
    "perimetro": {
        "comuni_target": [],
        "comuni_esclusi": ["Roma", "Cagliari", "Ancona"],
        "raggio_km": 20,
    },
    "filtri": {
        "superficie_min_mq": 1000,
        "superficie_max_mq": 3000,
        "tolleranza_soglia_pct": 5,
        "penalita_sopra_max_punti_per_1000mq": 5,
        "penalita_sopra_max_massima": 15,
        "giorni_minimi": 20,
        "giorni_urgenza": 35,
        "soglia_hard_eur_mq_sln": 2500,
        "soglia_prezzo_anomalo_eur": 10000,
        "altezza_plausibile_min_m": 2,
        "altezza_plausibile_max_m": 20,
    },
    "scorecard": {
        "pesi": {
            "iqss_comune": 12,
            "saturazione_bacino": 10,
            "visibilita_traffico": 12,
            "qualita_bacino": 6,
            "altezza_soppalcabilita": 13,
            "efficienza_planimetrica": 11,
            "accessibilita_operativa": 9,
            "antincendio_involucro": 5,
            "percorso_autorizzativo": 12,
            "espandibilita": 5,
            "pulizia_tempi_transazione": 5,
        },
        "soglia_classe_a": 75,
        "soglia_classe_b": 60,
        "soglia_classe_c": 45,
        "ampiezza_range_max_per_classe": 15,
        "soglia_saturazione_mq_per_abitante": 0.05,
        "raggio_bacino_km": 5,
        "raggio_poli_retail_km": 2,
        "altezza_punti": {
            "sotto_3_0": 0,
            "da_3_0_a_4_4": 4,
            "da_4_5_a_5_4": 8,
            "da_5_5": 13,
        },
    },
    "economia": {
        "scenari_tariffa_eur_mq_anno": [261, 320, 380],
        "occupancy_regime": 0.82,
        "margine_ebitda": 0.58,
        "efficienza_sln_default": 0.68,
        "efficienza_sln_min": 0.55,
        "efficienza_sln_max": 0.75,
        "fattore_soppalco_basso_h": 0.0,
        "fattore_soppalco_medio_min": 0.4,
        "fattore_soppalco_medio_max": 0.6,
        "fattore_soppalco_alto_min": 0.7,
        "fattore_soppalco_alto_max": 0.9,
        "capex_baseline_min_eur_mq": 450,
        "capex_baseline_max_eur_mq": 600,
        "capex_soppalco_min_eur_mq": 150,
        "capex_soppalco_max_eur_mq": 220,
        "capex_sismico_min_eur_mq": 80,
        "capex_sismico_max_eur_mq": 200,
        "capex_strip_out_min_eur_mq": 30,
        "capex_strip_out_max_eur_mq": 80,
        "capex_amianto_min_eur_mq": 35,
        "capex_amianto_max_eur_mq": 60,
        "capex_recuperi_min_eur_mq": 0,
        "capex_recuperi_max_eur_mq": 60,
        "oneri_acquisto_pct": 6,
        "mesi_al_go_live_min": 0,
        "mesi_al_go_live_max": 18,
        "margine_mensile_mancato_frazione_ebitda_annuo": 0.06,
        "oneri_finanziari_mensili_pct": 0.3,
        "hurdle_classe_a": 0.09,
        "hurdle_classe_b": 0.11,
        "hurdle_classe_c": 0.14,
        "riferimento_costo_mq_sln_yoc10": 1250,
    },
    "voto": {
        "peso_qualita": 0.70,
        "peso_economico": 0.30,
        "coeff_incertezza": 0.20,
    },
    "selezione": {
        "n_segnalazioni": 10,
        "soglia_ribasso_pct": -3,
        "soglia_ribasso_anomalo_pct": -50,
        "soglia_delta_voto": 5,
        "soglia_restringimento_range": 10,
    },
    "rete": {
        "fetch_abilitato": False,
        "rate_limit_secondi": 2,
        "timeout_secondi": 15,
        "cache_giorni": 7,
    },
    "pannello": {
        "porta": 8765,
        "giorni_promemoria": 7,
        "timeout_valutazione_min": 60,
        "timeout_scraper_min": 30,
        "impedisci_sospensione": True,
        "scraper_aste": [],
        "catena_immid": [],
        "comando_claude": {},
    },
}


def _fondi(base: dict, extra: dict) -> dict:
    """Fonde ricorsivamente extra dentro base (extra vince)."""
    risultato = copy.deepcopy(base)
    for chiave, valore in extra.items():
        if isinstance(valore, dict) and isinstance(risultato.get(chiave), dict):
            risultato[chiave] = _fondi(risultato[chiave], valore)
        else:
            risultato[chiave] = valore
    return risultato


def carica_config(percorso: Path) -> dict[str, Any]:
    """Legge config.yaml e lo fonde con i default. Non solleva se il file
    manca del tutto: in quel caso usa solo i default (con un avviso a carico
    del chiamante, che ha accesso a meta.note)."""
    percorso = Path(percorso)
    dati_file: dict[str, Any] = {}
    if percorso.is_file():
        with open(percorso, "r", encoding="utf-8") as f:
            dati_file = yaml.safe_load(f) or {}
    return _fondi(DEFAULT, dati_file)


def hash_config(percorso: Path) -> str:
    """sha1 del contenuto testuale di config.yaml, per meta.hash_config."""
    percorso = Path(percorso)
    if not percorso.is_file():
        return "assente"
    h = hashlib.sha1()
    with open(percorso, "rb") as f:
        h.update(f.read())
    return h.hexdigest()
