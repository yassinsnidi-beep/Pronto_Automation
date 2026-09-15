#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_opportunities_immid.py
============================
Orchestratore settimanale per Idealista.it e Immobiliare.it: lancia i due
scraper in modalità `--recent-only` e gestisce la cartella
`opportunities/INPUT/IMM_ID/` (current_week / past_week).

Per ogni fonte (idealista, immobiliare), IN MODO INDIPENDENTE:
  1. Legge il file attualmente in current_week/dataset_<fonte>_recenti.json
     (se esiste): è la baseline "settimana precedente".
  2. Lancia lo scraper corrispondente in modalità --recent-only (stop interno
     invariato, a --staleness-days giorni, basato sul dataset "completo" di
     baseline: scraper_idealista/immobiliare_master.py --baseline default),
     salvando in un file temporaneo.
  3. Filtra i risultati appena scaricati: tiene solo gli annunci con
     differenza tra la data di aggiornamento e lo scraped_at della baseline
     (punto 1) inferiore a --weekly-days giorni. Gli annunci non presenti
     nella baseline precedente (mai visti la settimana prima) vengono sempre
     tenuti, perché non c'è nulla con cui confrontarli.
  4. Sposta (sovrascrivendo) l'eventuale file attuale di current_week dentro
     past_week.
  5. Scrive il file filtrato in current_week.

Uso
----
    python run_opportunities_immid.py
    python run_opportunities_immid.py --sources idealista
    python run_opportunities_immid.py --headless false --cities Milano
    python run_opportunities_immid.py --weekly-days 7 --staleness-days 14
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
IMM_ID_DIR = ROOT_DIR / "opportunities" / "INPUT" / "IMM_ID"
CURRENT_WEEK_DIR = IMM_ID_DIR / "current_week"
PAST_WEEK_DIR = IMM_ID_DIR / "past_week"

DEFAULT_WEEKLY_DAYS = 7
DEFAULT_STALENESS_DAYS = 14
DEFAULT_STALE_STREAK = 3

# Per ogni fonte: script scraper, nome file di output, e nome del campo che
# nel record contiene la data di aggiornamento (diverso tra le due fonti:
# vedi extract_updated_at / fetch_update_recency_days nei rispettivi master).
SOURCES: Dict[str, Dict[str, str]] = {
    "idealista": {
        "script": str(SCRIPT_DIR / "scraper_idealista_master.py"),
        "filename": "dataset_idealista_recenti.json",
        "updated_field": "updated_at_estimate",
    },
    "immobiliare": {
        "script": str(SCRIPT_DIR / "scraper_immobiliare_master.py"),
        "filename": "dataset_immobiliare_recenti.json",
        "updated_field": "updated_at",
    },
}


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(SCRIPT_DIR / "run_opportunities_immid.log", encoding="utf-8"),
        ],
    )


def load_dataset(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"generated_at": None, "total": 0, "listings": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = json.load(f)
        if isinstance(content, dict) and "listings" in content:
            return content
    except Exception:
        logging.warning("Impossibile leggere %s: lo tratto come vuoto.", path)
    return {"generated_at": None, "total": 0, "listings": {}}


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def run_scraper(cfg: Dict[str, str], tmp_output: Path, extra_args: List[str]) -> None:
    cmd = [
        sys.executable, cfg["script"],
        "--recent-only",
        "--output", str(tmp_output),
        *extra_args,
    ]
    logging.info("Lancio: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(SCRIPT_DIR))
    if result.returncode != 0:
        raise RuntimeError(f"Lo scraper è terminato con codice di errore {result.returncode} (comando: {' '.join(cmd)})")


def filter_weekly(fresh: Dict[str, Any], baseline: Dict[str, Any], updated_field: str, weekly_days: int) -> Dict[str, Any]:
    """
    Tiene solo gli annunci "freschi": quelli mai visti nella baseline della
    settimana precedente (non c'è nulla con cui confrontarli, si tengono per
    non perdere dati), oppure quelli la cui differenza tra data di
    aggiornamento e scraped_at della baseline è inferiore a weekly_days.
    """
    baseline_listings = baseline.get("listings", {})
    kept: Dict[str, Any] = {}
    n_new = n_kept_recent = n_dropped_stale = n_unverifiable = 0

    for key, item in fresh.get("listings", {}).items():
        baseline_item = baseline_listings.get(key)
        if baseline_item is None:
            kept[key] = item
            n_new += 1
            continue

        baseline_scraped_at = parse_iso(baseline_item.get("scraped_at"))
        updated_dt = parse_iso(item.get(updated_field))

        if baseline_scraped_at is None or updated_dt is None:
            # Non possiamo calcolare la differenza: si tiene per non perdere
            # dati piuttosto che scartare per un'incertezza di lettura.
            kept[key] = item
            n_unverifiable += 1
            continue

        diff_days = abs((baseline_scraped_at - updated_dt).total_seconds()) / 86400
        if diff_days < weekly_days:
            kept[key] = item
            n_kept_recent += 1
        else:
            n_dropped_stale += 1

    logging.info(
        "Filtro settimanale (soglia %d gg): %d annunci totali scaricati -> %d tenuti "
        "(%d nuovi + %d recenti + %d non verificabili), %d scartati (troppo vecchi).",
        weekly_days, len(fresh.get("listings", {})), len(kept), n_new, n_kept_recent, n_unverifiable, n_dropped_stale,
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(kept),
        "listings": kept,
    }


def rotate_and_save(cfg: Dict[str, str], filtered: Dict[str, Any]) -> None:
    CURRENT_WEEK_DIR.mkdir(parents=True, exist_ok=True)
    PAST_WEEK_DIR.mkdir(parents=True, exist_ok=True)

    current_path = CURRENT_WEEK_DIR / cfg["filename"]
    past_path = PAST_WEEK_DIR / cfg["filename"]

    if current_path.exists():
        if past_path.exists():
            past_path.unlink()
        shutil.move(str(current_path), str(past_path))
        logging.info("Spostato %s -> %s", current_path, past_path)

    tmp_path = current_path.with_suffix(current_path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)
    tmp_path.replace(current_path)
    logging.info("Salvato %s (%d annunci).", current_path, filtered["total"])


def process_source(source: str, weekly_days: int, extra_args: List[str]) -> None:
    cfg = SOURCES[source]
    logging.info("=== Fonte: %s ===", source)

    current_path = CURRENT_WEEK_DIR / cfg["filename"]
    # Baseline "settimana precedente": va letta PRIMA di sovrascrivere/spostare current_week.
    baseline = load_dataset(current_path)

    tmp_output = SCRIPT_DIR / f"_tmp_{source}_recenti.json"
    if tmp_output.exists():
        tmp_output.unlink()

    run_scraper(cfg, tmp_output, extra_args)
    fresh = load_dataset(tmp_output)

    filtered = filter_weekly(fresh, baseline, cfg["updated_field"], weekly_days)
    rotate_and_save(cfg, filtered)

    if tmp_output.exists():
        tmp_output.unlink()
    tmp_lock = tmp_output.with_suffix(tmp_output.suffix + ".tmp")
    if tmp_lock.exists():
        tmp_lock.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Orchestratore settimanale opportunities/INPUT/IMM_ID per Idealista.it e Immobiliare.it."
    )
    parser.add_argument("--sources", type=str, default="idealista,immobiliare", help="Fonti da eseguire, separate da virgola (default: entrambe).")
    parser.add_argument("--weekly-days", type=int, default=DEFAULT_WEEKLY_DAYS, help="Soglia in giorni per il filtro settimanale (default: 7).")
    parser.add_argument("--staleness-days", type=int, default=DEFAULT_STALENESS_DAYS, help="Soglia --staleness-days passata allo scraper per il suo stop interno (default: 14).")
    parser.add_argument("--stale-streak", type=int, default=DEFAULT_STALE_STREAK, help="Soglia --stale-streak passata allo scraper (annunci vecchi consecutivi richiesti prima di fermarsi, default: 3).")
    parser.add_argument("--headless", type=str, default="true", help="true/false, passato allo scraper.")
    parser.add_argument("--cities", type=str, default=None, help="Lista città separate da virgola, passata allo scraper (default: tutte).")
    parser.add_argument("--log-level", type=str, default="INFO", help="Livello di log (DEBUG, INFO, WARNING, ERROR).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    extra_args = [
        "--staleness-days", str(args.staleness_days),
        "--stale-streak", str(args.stale_streak),
        "--headless", args.headless,
    ]
    if args.cities:
        extra_args += ["--cities", args.cities]

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    had_error = False
    for source in sources:
        if source not in SOURCES:
            logging.error("Fonte sconosciuta: %s (valide: %s)", source, ", ".join(SOURCES))
            had_error = True
            continue
        try:
            process_source(source, args.weekly_days, extra_args)
        except Exception:
            logging.exception("Errore durante l'elaborazione della fonte %s", source)
            had_error = True

    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
