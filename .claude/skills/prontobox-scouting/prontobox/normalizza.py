"""Stadio 0: carica i dataset di INPUT/, li adatta al record canonico e fa
il dedup. Orchestratore di adattatori.py + identita.py (§3.0 e §2.1)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import adattatori, identita, util

_RE_NOME_FILE = re.compile(r"^dataset_(.+?)_(completo|recenti)\.json$")


def _fonte_da_nome_file(nome: str) -> str | None:
    m = _RE_NOME_FILE.match(nome)
    return m.group(1) if m else None


def adatta_file(percorso: Path, canale: str, root: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[str]]:
    """Legge e adatta un singolo dataset_<fonte>_*.json. Non solleva mai:
    un file illeggibile o vuoto produce solo un avviso, mai un crash."""
    avvisi: list[str] = []
    try:
        rel = str(percorso.relative_to(root))
    except ValueError:
        rel = str(percorso)

    fonte = _fonte_da_nome_file(percorso.name)
    if fonte is None:
        avvisi.append(f"nome file non riconosciuto, saltato: {rel}")
        return [], None, avvisi

    try:
        dati = util.leggi_json(percorso)
    except Exception as exc:  # file corrotto, encoding sbagliato, ecc.
        avvisi.append(f"file illeggibile, saltato: {rel} ({exc})")
        return [], None, avvisi

    if not isinstance(dati, dict):
        avvisi.append(f"formato inatteso (non è un oggetto JSON), saltato: {rel}")
        return [], None, avvisi

    listings = dati.get("listings") or {}
    if not isinstance(listings, dict):
        avvisi.append(f"'listings' non è un oggetto, saltato: {rel}")
        listings = {}

    info = {
        "file": rel,
        "canale": canale,
        "generated_at": dati.get("generated_at"),
        "n_listings": len(listings),
    }

    adattatore, _canale_atteso = adattatori.ADATTATORI.get(fonte, (None, canale))
    records: list[dict[str, Any]] = []
    n_errori = 0
    for id_chiave, raw in listings.items():
        try:
            if adattatore is not None:
                rec = adattatore(id_chiave, raw or {}, rel)
            else:
                rec = adattatori.adatta_generico(fonte, id_chiave, raw or {}, canale, rel)
            rec["canale"] = canale  # la cartella di provenienza e' sempre autorevole (§3.0.1)
            records.append(rec)
        except Exception as exc:  # difesa estrema: mai bloccare l'intera run per un record
            n_errori += 1
            avvisi.append(f"record {fonte}_{id_chiave} in {rel} non elaborabile, saltato: {exc}")

    if n_errori:
        avvisi.append(f"{n_errori} record non elaborabili in {rel} (su {len(listings)})")

    if fonte not in adattatori.ADATTATORI:
        avvisi.append(f"fonte '{fonte}' senza adattatore dedicato (file {rel}): usato l'adattatore generico, verificare la mappa campi")

    return records, info, avvisi


def carica_tutto(config: dict[str, Any], root: Path) -> dict[str, Any]:
    """Carica ASTE + IMM_ID, adatta e deduplica. Ritorna un dict con:
    records, meta_input, note, record_letti, duplicati_uniti."""
    root = Path(root)
    percorsi_cfg = config["percorsi"]
    dir_aste = root / percorsi_cfg["input_aste"]
    dir_immid = root / percorsi_cfg["input_immid"]

    tutti_records: list[dict[str, Any]] = []
    meta_input: list[dict[str, Any]] = []
    note: list[str] = []

    if dir_aste.is_dir():
        for percorso in sorted(dir_aste.glob("dataset_*_completo.json")):
            recs, info, avvisi = adatta_file(percorso, identita_canale_aste(), root)
            note.extend(avvisi)
            if info:
                meta_input.append(info)
            tutti_records.extend(recs)
    else:
        note.append(f"cartella ASTE non trovata: {dir_aste}")

    if dir_immid.is_dir():
        for percorso in sorted(dir_immid.glob("dataset_*_recenti.json")):
            recs, info, avvisi = adatta_file(percorso, identita_canale_immid(), root)
            note.extend(avvisi)
            if info:
                meta_input.append(info)
            tutti_records.extend(recs)
    else:
        note.append(f"cartella IMM_ID/current_week non trovata: {dir_immid}")

    record_letti = len(tutti_records)
    records_dedotti, n_duplicati, log_unioni = identita.deduplica(tutti_records)

    return {
        "records": records_dedotti,
        "meta_input": meta_input,
        "note": note,
        "record_letti": record_letti,
        "duplicati_uniti": n_duplicati,
        "log_unioni": log_unioni,
    }


def identita_canale_aste() -> str:
    return adattatori.CANALE_ASTE


def identita_canale_immid() -> str:
    return adattatori.CANALE_IMMID
