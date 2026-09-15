#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_asteannunci_master.py
===============================

Scraper per Asteannunci.it (Gruppo Edicom) — aste giudiziarie immobiliari.
Cerca lotti di categoria "Immobili industriali" e "Depositi" (capannoni,
magazzini, depositi), filtrando per le province delle 36 città target.

*** VALIDATO DAL VIVO ***
A differenza dei tentativi precedenti su altri portali, questo sito è stato
ispezionato con richieste HTTP reali (nessun accesso a internet era invece
disponibile quando furono scritti i primi tentativi speculativi): struttura
delle card, paginazione e conteggi sono confermati sui dati reali del sito.

Tecnologia
----------
- `requests` + `BeautifulSoup` (NON serve un browser): il sito è
  server-side renderizzato in HTML semplice, senza protezioni anti-bot
  rilevate nei test — nessun Playwright necessario, molto più veloce.
- Ricerca per CATEGORIA (non serve compilare filtri di località complessi):
    - https://www.asteannunci.it/aste-immobiliari/immobili-industriali
    - https://www.asteannunci.it/aste-immobiliari/depositi
  paginate con /page/2, /page/3, ... Il sito non supporta l'incrocio diretto
  categoria+comune nell'URL (verificato: dà 404), quindi si scarica l'intera
  categoria (63 e 60 annunci rispettivamente al momento del test: volumi
  gestibili) e si filtra lato client per provincia sulle 36 città target,
  usando la sigla provincia (es. "(MI)") presente in ogni indirizzo.
- La categoria "locali-commerciali" esiste ma è stata esclusa di default: è
  troppo generica (1220+ annunci, in gran parte negozi non pertinenti);
  aggiungibile con --categories se serve.

Installazione
--------------
    pip install requests beautifulsoup4 lxml

Esecuzione
----------
    python scraper_asteannunci_master.py
    python scraper_asteannunci_master.py --max-pages 3 --delay-min 1 --delay-max 2

Output
------
    scrapers_v2/dataset_asteannunci_completo.json
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:
    print("ERRORE: la libreria 'requests' non è installata.\nInstallala con:  pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print(
        "ERRORE: la libreria 'beautifulsoup4' non è installata.\n"
        "Installala con:  pip install beautifulsoup4 lxml",
        file=sys.stderr,
    )
    sys.exit(1)


# ============================================================================
# CONFIGURAZIONE
# ============================================================================

BASE_URL = "https://www.asteannunci.it"

# Categorie confermate dal dropdown di filtro del sito. Solo le due pertinenti
# a capannoni/magazzini/depositi sono attive di default.
DEFAULT_CATEGORIES: List[str] = ["immobili-industriali", "depositi"]
ALL_KNOWN_CATEGORIES: List[str] = [
    "immobili-industriali", "depositi", "locali-commerciali", "fabbricati",
    "garage", "strutture-pubbliche", "cantina", "case",
]

CITIES: List[str] = [
    "Bolzano", "Bologna", "Trento", "Padova", "Milano", "Parma", "Modena",
    "Reggio Emilia", "Brescia", "Monza", "Bergamo", "Verona", "Vicenza",
    "Firenze", "Pavia", "Como", "Piacenza", "Treviso", "Novara", "Cremona",
    "Cesena", "Forlì", "Pisa", "Udine", "Varese", "Ravenna", "Ancona",
    "Sesto San Giovanni", "Cinisello Balsamo", "Busto Arsizio", "Roma",
    "Legnano", "Prato", "Cagliari", "Pistoia", "Lucca",
]

# Sigla automobilistica di provincia, usata per riconoscere gli annunci delle
# 36 città target dentro il testo indirizzo (es. "Gorgonzola (MI), Via...").
CITY_PROVINCE_ABBR: Dict[str, str] = {
    "Bolzano": "BZ", "Bologna": "BO", "Trento": "TN", "Padova": "PD", "Milano": "MI",
    "Parma": "PR", "Modena": "MO", "Reggio Emilia": "RE", "Brescia": "BS", "Monza": "MB",
    "Bergamo": "BG", "Verona": "VR", "Vicenza": "VI", "Firenze": "FI", "Pavia": "PV",
    "Como": "CO", "Piacenza": "PC", "Treviso": "TV", "Novara": "NO", "Cremona": "CR",
    "Cesena": "FC", "Forlì": "FC", "Pisa": "PI", "Udine": "UD", "Varese": "VA",
    "Ravenna": "RA", "Ancona": "AN", "Sesto San Giovanni": "MI", "Cinisello Balsamo": "MI",
    "Busto Arsizio": "VA", "Roma": "RM", "Legnano": "MI", "Prato": "PO", "Cagliari": "CA",
    "Pistoia": "PT", "Lucca": "LU",
}
TARGET_PROVINCE_ABBRS = set(CITY_PROVINCE_ABBR.values())

OUTPUT_FILE = Path(__file__).resolve().parent.parent / "opportunities" / "INPUT" / "ASTE" / "dataset_asteannunci_completo.json"
DEFAULT_MAX_PAGES = 15
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "it-IT,it;q=0.9",
}


# ============================================================================
# UTILITY
# ============================================================================

def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(Path(__file__).resolve().parent / "scraper_asteannunci.log", encoding="utf-8"),
        ],
    )


def polite_sleep(delay_min: float, delay_max: float) -> None:
    time.sleep(random.uniform(delay_min, delay_max))


def atomic_save_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def load_existing_dataset(path: Path) -> Dict[str, Any]:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = json.load(f)
            if isinstance(content, dict) and "listings" in content:
                return content
        except Exception:
            logging.warning("Impossibile leggere il dataset esistente: si riparte da zero.")
    return {"generated_at": None, "total": 0, "listings": {}}


def matches_target_area(indirizzo: Optional[str]) -> bool:
    """True se l'indirizzo contiene la sigla di una delle province delle 36 città target."""
    if not indirizzo:
        return False
    match = re.search(r"\(([A-Z]{2})\)", indirizzo)
    if not match:
        return False
    return match.group(1) in TARGET_PROVINCE_ABBRS


# ============================================================================
# PARSING
# ============================================================================

def parse_listing_cards(html: str) -> List[dict]:
    """
    Estrae gli annunci dalla pagina di categoria. Ogni card è un <a href="/aste/ID/SUBID/slug">
    che racchiude titolo, indirizzo, prezzo base, codice inserzione e data dell'asta.
    """
    soup = BeautifulSoup(html, "lxml")
    results: List[dict] = []
    seen_ids = set()

    for a in soup.select('a[href*="/aste/"]'):
        href = a.get("href", "")
        match = re.search(r"/aste/(\d+)/(\d+)/", href)
        if not match:
            continue
        listing_id = match.group(1)
        if listing_id in seen_ids:
            continue
        seen_ids.add(listing_id)

        full_url = href if href.startswith("http") else f"{BASE_URL}{href}"

        # Unisce i nodi di testo con un separatore per poterli isolare in ordine.
        full_text = a.get_text(" | ", strip=True)
        parts = [p.strip() for p in full_text.split("|") if p.strip()]

        titolo = parts[0] if parts else None
        indirizzo = parts[1] if len(parts) > 1 and not parts[1].lower().startswith("prezzo") else None

        prezzo_match = re.search(r"Prezzo base:.*?([\d.]+,\d{2})", full_text)
        codice_match = re.search(r"Codice inserzione:\s*(\d+)", full_text)
        data_match = re.search(r"\d{2}/\d{2}/\d{4}\s*-\s*\d{2}:\d{2}", full_text)
        tribunale_match = re.search(r"Tribunale di [\w' ]+", full_text)
        procedura_match = re.search(r"Proc\.?\s*[\d/]+", full_text)

        results.append({
            "id": listing_id,
            "url": full_url,
            "titolo": titolo,
            "indirizzo": indirizzo,
            "prezzo_base": prezzo_match.group(1) if prezzo_match else None,
            "data_asta": data_match.group(0) if data_match else None,
            "tribunale": tribunale_match.group(0) if tribunale_match else None,
            "procedura": procedura_match.group(0) if procedura_match else None,
            "codice_inserzione": codice_match.group(1) if codice_match else listing_id,
            "descrizione_completa": full_text,
        })

    return results


# ============================================================================
# SCRAPER
# ============================================================================

class AsteannunciScraper:
    def __init__(
        self,
        categories: List[str] = None,
        delay_min: float = 1.0,
        delay_max: float = 2.5,
        max_pages: int = DEFAULT_MAX_PAGES,
        resume: bool = True,
        output_path: Path = OUTPUT_FILE,
        only_target_area: bool = True,
    ) -> None:
        self.categories = categories or DEFAULT_CATEGORIES
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.max_pages = max_pages
        self.resume = resume
        self.output_path = output_path
        self.only_target_area = only_target_area

        self.session = requests.Session()
        self.session.headers.update(HEADERS)

        self.dataset: Dict[str, Any] = (
            load_existing_dataset(output_path) if resume else {"generated_at": None, "total": 0, "listings": {}}
        )

    def fetch(self, url: str) -> Optional[str]:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code == 404:
                    # Pagina oltre l'ultima disponibile: fine paginazione reale, non un
                    # errore transitorio, quindi niente retry.
                    logging.debug("HTTP 404 per %s (probabile fine paginazione).", url)
                    return None
                logging.warning("HTTP %s per %s (tentativo %d/%d)", resp.status_code, url, attempt, MAX_RETRIES)
            except Exception as exc:
                logging.warning("Errore di rete su %s: %s (tentativo %d/%d)", url, exc, attempt, MAX_RETRIES)
            time.sleep(2 ** attempt)
        logging.error("Impossibile recuperare %s dopo %d tentativi.", url, MAX_RETRIES)
        return None

    @staticmethod
    def build_url(categoria: str, page: int) -> str:
        base = f"{BASE_URL}/aste-immobiliari/{categoria}"
        return f"{base}/page/{page}" if page > 1 else base

    def scrape_category(self, categoria: str) -> int:
        logging.info("=== Categoria: %s ===", categoria)
        new_count = 0
        seen_ids: set = set()
        page = 1
        while page <= self.max_pages:
            url = self.build_url(categoria, page)
            html = self.fetch(url)
            if html is None:
                break

            results = parse_listing_cards(html)
            if not results:
                if page == 1:
                    logging.info("Nessun annuncio trovato per la categoria %s.", categoria)
                else:
                    logging.info("Fine paginazione per %s a pagina %d.", categoria, page)
                break

            current_ids = {r["id"] for r in results}
            new_ids = current_ids - seen_ids
            if not new_ids:
                logging.info("Pagina %d senza annunci nuovi: fine paginazione per %s.", page, categoria)
                break
            seen_ids |= current_ids

            in_area = [r for r in results if r["id"] in new_ids and (not self.only_target_area or matches_target_area(r["indirizzo"]))]
            logging.info(
                "Pagina %d: %d annunci totali (%d nuovi, %d nelle 36 città target).",
                page, len(results), len(new_ids), len(in_area),
            )

            for item in in_area:
                key = f"asteannunci_{item['id']}"
                if self.resume and key in self.dataset["listings"]:
                    continue
                self.dataset["listings"][key] = {
                    "source": "asteannunci.it",
                    "categoria": categoria,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                    **item,
                }
                new_count += 1

            page += 1
            polite_sleep(self.delay_min, self.delay_max)

        return new_count

    def save(self) -> None:
        self.dataset["generated_at"] = datetime.now(timezone.utc).isoformat()
        self.dataset["total"] = len(self.dataset["listings"])
        atomic_save_json(self.dataset, self.output_path)
        logging.info("Dataset salvato in %s (%d annunci totali).", self.output_path, self.dataset["total"])

    def run(self) -> None:
        for categoria in self.categories:
            try:
                added = self.scrape_category(categoria)
                logging.info("-> %d nuovi annunci per categoria %s.", added, categoria)
            except Exception:
                logging.exception("Errore durante lo scraping della categoria %s", categoria)
            finally:
                self.save()

        logging.info("Scraping completato. Totale annunci nel dataset: %d", len(self.dataset["listings"]))


# ============================================================================
# CLI
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scraper per Asteannunci.it (Gruppo Edicom) - aste giudiziarie.")
    parser.add_argument(
        "--categories", type=str, default=None,
        help=f"Categorie separate da virgola (default: {','.join(DEFAULT_CATEGORIES)}). Disponibili: {','.join(ALL_KNOWN_CATEGORIES)}",
    )
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Numero massimo di pagine per categoria.")
    parser.add_argument("--delay-min", type=float, default=1.0, help="Delay minimo (secondi) tra le richieste.")
    parser.add_argument("--delay-max", type=float, default=2.5, help="Delay massimo (secondi) tra le richieste.")
    parser.add_argument("--no-resume", action="store_true", help="Ignora il dataset esistente e riparte da zero.")
    parser.add_argument("--no-area-filter", action="store_true", help="Non filtrare per le 36 città target: salva tutti gli annunci della categoria, in tutta Italia.")
    parser.add_argument("--output", type=str, default=str(OUTPUT_FILE), help="Percorso del file JSON di output.")
    parser.add_argument("--log-level", type=str, default="INFO", help="Livello di log (DEBUG, INFO, WARNING, ERROR).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    categories = DEFAULT_CATEGORIES
    if args.categories:
        categories = [c.strip() for c in args.categories.split(",")]

    scraper = AsteannunciScraper(
        categories=categories,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        max_pages=args.max_pages,
        resume=not args.no_resume,
        output_path=Path(args.output),
        only_target_area=not args.no_area_filter,
    )

    try:
        scraper.run()
    except KeyboardInterrupt:
        logging.warning("Interruzione manuale ricevuta: salvataggio dello stato parziale...")
        scraper.save()
    except Exception:
        logging.exception("Errore fatale durante lo scraping.")
        scraper.save()
        sys.exit(1)


if __name__ == "__main__":
    main()
