"""Ripiego euristico per il passo che, in produzione, legge Claude Code
(§7.3). Produce lo STESSO formato di output che ci si aspetterebbe dal
modello (percorso autorizzativo stimato, indizi di rischio, punti di forza,
rischi, cosa verificare), ma con regole esplicite a parole chiave invece che
con un modello linguistico.

Ogni valore prodotto qui porta SEMPRE fonte="stima_euristica" e
confidenza="bassa": non va mai spacciato per una lettura del modello. Serve
solo a rendere la pipeline testabile end-to-end in questo ambiente di build
(vedi anche il comando reale in config.yaml/comando_claude e la spiegazione
in SKILL.md e README.md).
"""

from __future__ import annotations

from typing import Any

from . import util

FONTE = "stima_euristica"
CONFIDENZA = "bassa"

# Scala punti del percorso autorizzativo, esattamente come in §Stadio2.
_SCALA_PERCORSO = [
    (("conforme", "regolare", "agibilita ottenuta", "nessuna difformita", "pienamente conforme"), 12, "già compatibile e conforme"),
    (("cambio di destinazione", "cambio d uso", "mutamento di destinazione"), 10, "cambio d'uso nella stessa categoria funzionale, nessuna opera esplicita nel testo"),
    (("permesso di costruire", "variante", "opere di adeguamento", "ristrutturazione"), 7, "cambio d'uso con opere o permesso di costruire ordinario"),
    (("difformita", "abuso edilizio", "sanatoria", "condono", "sanare"), 5, "difformità da sanare, probabile cambio d'uso"),
    (("deroga", "variante puntuale", "vincolo paesaggistico"), 2, "permesso in deroga o variante puntuale"),
]
_DEFAULT_PUNTI = 7
_DEFAULT_MOTIVO = "nessun indizio esplicito nel testo: si assume prudenzialmente la necessità di un permesso di costruire ordinario (stima di ripiego, non verificata)"


def _classifica_percorso(testo_completo: str) -> dict[str, Any]:
    t = util.norm_testo(testo_completo)
    for parole, punti, motivo in _SCALA_PERCORSO:
        if any(p in t for p in parole):
            return {
                "percorso_autorizzativo_punti": punti,
                "percorso_autorizzativo_motivo": motivo,
                "percorso_autorizzativo_fonte": FONTE,
                "percorso_autorizzativo_confidenza": CONFIDENZA,
            }
    return {
        "percorso_autorizzativo_punti": _DEFAULT_PUNTI,
        "percorso_autorizzativo_motivo": _DEFAULT_MOTIVO,
        "percorso_autorizzativo_fonte": FONTE,
        "percorso_autorizzativo_confidenza": CONFIDENZA,
    }


def _punti_di_forza(item: dict[str, Any]) -> list[str]:
    punti = []
    flag = set(item.get("flag") or [])
    if "URGENZA" not in flag and item.get("tipo_vendita") == "asta":
        punti.append("asta con tempi ragionevoli per preparare l'offerta")
    # una superficie "nella fascia target" e' un punto di forza solo se la
    # superficie e' DAVVERO nota e confermata: con SUPERFICIE_IGNOTA o
    # SUPERFICIE_CONFIDENZA_BASSA non sappiamo nulla, quindi non e' ne' un
    # punto di forza ne' un rischio, e' un dato mancante (vedi _da_verificare)
    superficie_sconosciuta = bool(flag & {"SUPERFICIE_IGNOTA", "SUPERFICIE_CONFIDENZA_BASSA"})
    if not superficie_sconosciuta and "AL_LIMITE_SOGLIA" not in flag and "FUORI_TAGLIA_GRANDE" not in flag:
        punti.append("superficie confermata nella fascia target, senza correzioni")
    if "PREZZO_ANOMALO" not in flag and "PREZZO_IGNOTO" not in flag:
        punti.append("prezzo coerente con la tipologia, nessun'anomalia rilevata nei dati")
    if not punti:
        punti.append("da confermare con un'analisi più approfondita: nessun punto di forza distintivo emerge dai soli dati raccolti")
    return punti


def _rischi_principali(item: dict[str, Any]) -> list[str]:
    rischi = []
    for voce in item.get("checklist_dd") or []:
        rischi.append(f"{voce['voce']} (allerta {voce['allerta']}): indizio testuale '{voce['indizio']}'")
    flag = set(item.get("flag") or [])
    if "FUORI_TAGLIA_GRANDE" in flag:
        rischi.append("superficie sopra la fascia target: valutare se dividere l'immobile o affittarne solo una parte")
    if "AL_LIMITE_SOGLIA" in flag:
        rischi.append("superficie appena sotto la soglia minima: verificare la metratura esatta prima di procedere")
    if "PREZZO_ANOMALO" in flag:
        rischi.append("prezzo molto basso rispetto alla tipologia: probabile pertinenza (cantina, posto auto, quota) più che un intero immobile")
    if not rischi:
        rischi.append("nessun rischio specifico individuato dalle sole parole chiave nel testo: non equivale a nessun rischio reale")
    return rischi


def _da_verificare(item: dict[str, Any]) -> list[str]:
    voci = []
    for dm in item.get("dati_mancanti") or []:
        voci.append(f"{dm['dato']}: {dm['azione']} ({dm['dove_trovarlo']})")
    if not voci:
        voci.append("nessun dato esplicitamente mancante, ma una verifica in loco resta comunque raccomandata prima di un'offerta")
    return voci


def genera_giudizio(item: dict[str, Any]) -> dict[str, Any]:
    """item: una voce del file di lavoro prodotto da 'scout.py prepara'
    (id, titolo, descrizione, flag, checklist_dd, dati_mancanti, ...)."""
    testo_completo = f"{item.get('titolo') or ''} {item.get('descrizione') or ''}"
    risultato = _classifica_percorso(testo_completo)
    risultato["indizi_rischio"] = item.get("checklist_dd") or []
    risultato["punti_di_forza"] = _punti_di_forza(item)
    risultato["rischi_principali"] = _rischi_principali(item)
    risultato["da_verificare"] = _da_verificare(item)
    risultato["fonte"] = FONTE
    risultato["confidenza"] = CONFIDENZA
    return risultato


def genera_tutti(lavoro_items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["id"]: genera_giudizio(item) for item in lavoro_items}
