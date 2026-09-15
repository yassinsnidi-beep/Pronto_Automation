"""Orchestrazione dei 3 passi (prepara/seleziona/scrivi, §7.3) e del comando
'tutto' che li incatena in un solo processo. Tutte le funzioni qui sono
pure rispetto al filesystem (non scrivono nulla): la scrittura dei file la
fa scout.py, cosi' --dry-run puo' eseguire l'intera pipeline senza toccare
il disco."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Optional

from . import economico, esito as esito_mod, filtri, giudizi_fallback, normalizza, scorecard, selezione, testo
from .campo import valore_di

MAX_CARATTERI_DESCRIZIONE_LAVORO = 3000


def costruisci_lavoro(records: list[dict[str, Any]], config: dict[str, Any], data_run: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Applica lo Stadio 1 a ogni record. Ritorna (valutazioni_parziali,
    lavoro_items): le valutazioni_parziali hanno solo 'rec' e 'stadio1',
    i lavoro_items sono i soli testi/attributi da interpretare per chi ha
    superato i filtri (quello che 'prepara' scrive su file)."""
    valutazioni: list[dict[str, Any]] = []
    lavoro_items: list[dict[str, Any]] = []
    for rec in records:
        stadio1 = filtri.applica_stadio1(rec, config, data_run)
        valutazioni.append({"rec": rec, "stadio1": stadio1})
        if stadio1["esito"] == "PASSA":
            testo_completo = f"{rec.get('titolo') or ''} {rec.get('descrizione') or ''}"
            lavoro_items.append({
                "id": rec["id"],
                "canale": rec["canale"],
                "categoria": valore_di(rec.get("categoria")),
                "comune": valore_di(rec.get("comune")),
                "tipo_vendita": rec.get("tipo_vendita"),
                "titolo": rec.get("titolo"),
                "descrizione": (rec.get("descrizione") or "")[:MAX_CARATTERI_DESCRIZIONE_LAVORO],
                "flag": stadio1.get("flag") or [],
                "checklist_dd": testo.checklist_dd_da_testo(testo_completo, piano=rec.get("piano")),
                "dati_mancanti": stadio1.get("dati_mancanti") or [],
            })
    return valutazioni, lavoro_items


def valuta_tutto(
    valutazioni: list[dict[str, Any]],
    giudizi: dict[str, dict[str, Any]],
    config: dict[str, Any],
    root: Path,
) -> None:
    """Completa ogni valutazione parziale con scorecard/economico/esito/voto
    (Stadi 2-4) e lo snapshot (§5.1). Muta la lista in place."""
    for v in valutazioni:
        rec = v["rec"]
        stadio1 = v["stadio1"]
        if stadio1["esito"] != "PASSA":
            risultato = esito_mod.calcola_esito_e_voto(stadio1=stadio1, rec=rec, scorecard=None, economico=None, config=config)
            v["scorecard"] = None
            v["economico"] = None
        else:
            giudizio = giudizi.get(rec["id"])
            giudizio_percorso = None
            if giudizio:
                giudizio_percorso = {
                    "percorso_autorizzativo_punti": giudizio.get("percorso_autorizzativo_punti"),
                    "fonte": giudizio.get("percorso_autorizzativo_fonte", giudizio.get("fonte")),
                    "confidenza": giudizio.get("percorso_autorizzativo_confidenza", giudizio.get("confidenza")),
                    "motivo": giudizio.get("percorso_autorizzativo_motivo"),
                }
            sc = scorecard.calcola_scorecard(rec, giudizio_percorso, config, root)
            testo_completo = f"{rec.get('titolo') or ''} {rec.get('descrizione') or ''}"
            checklist = testo.checklist_dd_da_testo(testo_completo, piano=rec.get("piano"))
            indizi_amianto = any(c["voce"] == "amianto" for c in checklist)
            eco = economico.calcola_gate_economico(
                superficie_lorda=valore_di(rec.get("superficie_mq")),
                altezza_m=valore_di(rec.get("altezza_m")),
                categoria=valore_di(rec.get("categoria")),
                prezzo_eur=valore_di(rec.get("prezzo_eur")),
                offerta_minima_eur=valore_di(rec.get("offerta_minima_eur")),
                anno_costruzione=rec.get("anno_costruzione"),
                indizi_amianto=indizi_amianto,
                punteggio_percorso_autorizzativo=sc["breakdown"]["percorso_autorizzativo"]["punti"],
                classe=sc["classe"],
                config=config,
            )
            risultato = esito_mod.calcola_esito_e_voto(stadio1=stadio1, rec=rec, scorecard=sc, economico=eco, config=config)
            v["scorecard"] = sc
            v["economico"] = eco
        v.update(risultato)
        v["snapshot"] = selezione.costruisci_snapshot(rec, stadio1.get("dati_mancanti") or [])


def carica_e_prepara(config: dict[str, Any], root: Path, data_run: date) -> dict[str, Any]:
    dati = normalizza.carica_tutto(config, root)
    valutazioni, lavoro_items = costruisci_lavoro(dati["records"], config, data_run)
    return {
        "valutazioni": valutazioni,
        "lavoro_items": lavoro_items,
        "meta_input": dati["meta_input"],
        "note": dati["note"],
        "record_letti": dati["record_letti"],
        "duplicati_uniti": dati["duplicati_uniti"],
    }


def ottieni_giudizi(lavoro_items: list[dict[str, Any]], giudizi_esterni: Optional[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    """Se giudizi_esterni e' None (nessun --giudizi passato o file assente),
    usa il ripiego euristico e lo dichiara in una nota."""
    note: list[str] = []
    if giudizi_esterni is not None:
        return giudizi_esterni, note
    note.append(
        "valutazione testuale generata con euristiche di ripiego, non da un modello linguistico — "
        "sostituire con Claude Code in produzione (vedi §7.3 e SKILL.md)"
    )
    return giudizi_fallback.genera_tutti(lavoro_items), note


def esegui_seleziona(
    config: dict[str, Any],
    root: Path,
    data_run: date,
    nome_file_run: str,
    hash_config: str,
    giudizi_esterni: Optional[dict[str, Any]],
) -> dict[str, Any]:
    preparato = carica_e_prepara(config, root, data_run)
    giudizi, note_giudizi = ottieni_giudizi(preparato["lavoro_items"], giudizi_esterni)
    valuta_tutto(preparato["valutazioni"], giudizi, config, root)

    output_dir = root / config["percorsi"]["output_dir"]
    risultato = selezione.seleziona(preparato["valutazioni"], config, output_dir, data_run, nome_file_run, hash_config)

    return {
        **preparato,
        "note": preparato["note"] + note_giudizi,
        "giudizi": giudizi,
        "selezione": risultato,
    }


def costruisci_statistiche(preparato_e_selezione: dict[str, Any]) -> dict[str, Any]:
    valutazioni = preparato_e_selezione["valutazioni"]
    sel = preparato_e_selezione["selezione"]

    per_esito: dict[str, int] = {"PROMUOVI": 0, "DA APPROFONDIRE": 0, "MONITORA": 0, "SCARTA": 0}
    scarti_per_motivo: dict[str, int] = {}
    for v in valutazioni:
        per_esito[v["esito"]] = per_esito.get(v["esito"], 0) + 1
        if v["esito"] == "SCARTA" and v.get("motivo"):
            scarti_per_motivo[v["motivo"]] = scarti_per_motivo.get(v["motivo"], 0) + 1

    escluse_per_storico = {"INVARIATA": 0, "PEGGIORATA": 0}
    for e in sel["escluse_storico"]:
        escluse_per_storico[e["esito_confronto"]] = escluse_per_storico.get(e["esito_confronto"], 0) + 1

    nuove = sum(1 for v in sel["segnalate"] if v["stato_segnalazione"] == "NUOVA")
    risegnalate = sum(1 for v in sel["segnalate"] if v["stato_segnalazione"] == "RISEGNALATA")
    da_aste = sum(1 for v in sel["segnalate"] if v["rec"]["canale"] == "ASTE")
    da_immid = sum(1 for v in sel["segnalate"] if v["rec"]["canale"] == "IMM_ID")

    return {
        "record_letti": preparato_e_selezione["record_letti"],
        "duplicati_uniti": preparato_e_selezione["duplicati_uniti"],
        "per_esito": per_esito,
        "scarti_per_motivo": scarti_per_motivo,
        "in_classifica": len(sel["classifica"]),
        "posizioni_scorse": sel["posizioni_scorse"],
        "escluse_per_storico": escluse_per_storico,
        "segnalate": {
            "totale": len(sel["segnalate"]),
            "nuove": nuove,
            "risegnalate": risegnalate,
            "da_ASTE": da_aste,
            "da_IMM_ID": da_immid,
        },
    }


def note_selezione(sel: dict[str, Any], config: dict[str, Any]) -> list[str]:
    """Note aggiuntive che dipendono dall'ESITO della selezione (non dagli
    input): oggi solo il caso §4.4 'meno di 10 opportunità idonee', quando
    la classifica si esaurisce prima di raggiungere n_segnalazioni. Non si
    riempiono mai i posti vuoti con MONITORA/SCARTA (§4.4): qui si segnala
    solo il fatto, senza alterare la selezione."""
    n_segnalazioni = config["selezione"]["n_segnalazioni"]
    n_segnalate = len(sel["segnalate"])
    if n_segnalate >= n_segnalazioni:
        return []
    if n_segnalate == 0:
        return ["nessuna opportunità idonea e con novità questa settimana (classifica vuota o interamente già segnalata e invariata)"]
    return [f"solo {n_segnalate} opportunità idonee e con novità questa settimana (su {n_segnalazioni} richieste da n_segnalazioni)"]


def costruisci_scartate_e_monitora(valutazioni: list[dict[str, Any]]) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    scartate: dict[str, list[str]] = {}
    monitora: list[dict[str, Any]] = []
    for v in valutazioni:
        if v["esito"] == "SCARTA" and v.get("motivo"):
            scartate.setdefault(v["motivo"], []).append(v["rec"]["id"])
        elif v["esito"] == "MONITORA":
            scadenza = valore_di(v["rec"].get("scadenza_asta"))
            monitora.append({
                "id": v["rec"]["id"],
                "canale": v["rec"]["canale"],
                "motivo": v.get("motivo"),
                "scadenza_asta": scadenza.date().isoformat() if scadenza else None,
            })
    return scartate, monitora
