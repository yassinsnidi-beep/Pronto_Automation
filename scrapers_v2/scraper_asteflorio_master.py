#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_asteflorio_master.py
==============================

Scraper per Asteflorio.it — portale di aste giudiziarie (18.500+ immobili),
NON solo uno studio di consulenza come si potrebbe pensare dal nome: ha un
vero motore di ricerca pubblico. Cerca capannoni/magazzini/depositi nelle 36
città target.

*** VALIDATO DAL VIVO *** — confermato con richieste HTTP reali.

Tecnologia
----------
- `requests` + parsing del blocco JSON-LD (`<script type="application/ld+json">`
  con `@type: CollectionPage` / `ItemList`) incorporato in ogni pagina di
  ricerca per città: NON serve fare parsing HTML fragile delle card, il sito
  fornisce già titolo, URL e prezzo in formato strutturato.
- Ricerca per CITTÀ: https://asteflorio.it/aste/citta/<slug>?page=N — la
  paginazione tramite `?page=` FUNZIONA (verificato: pagina 1 e 2 danno
  risultati diversi). Il filtro per categoria/tipologia via query string
  invece NON funziona lato server (verificato: risultati identici a
  prescindere dal parametro) — probabilmente applicato lato client via JS.
  Per questo si scarica tutta la produzione di annunci per città e si filtra
  lato script cercando "capannone"/"magazzino"/"deposito" nel nome
  dell'annuncio (già presente nel JSON-LD, es. "Capannone industriale a
  Milano (MI)").

Installazione
--------------
    pip install requests beautifulsoup4 lxml

Esecuzione
----------
    python scraper_asteflorio_master.py

Output
------
    scrapers_v2/dataset_asteflorio_completo.json
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

BASE_URL = "https://asteflorio.it"

CITIES: List[str] = [
    "Bolzano", "Bologna", "Trento", "Padova", "Milano", "Parma", "Modena",
    "Reggio Emilia", "Brescia", "Monza", "Bergamo", "Verona", "Vicenza",
    "Firenze", "Pavia", "Como", "Piacenza", "Treviso", "Novara", "Cremona",
    "Cesena", "Forlì", "Pisa", "Udine", "Varese", "Ravenna", "Ancona",
    "Sesto San Giovanni", "Cinisello Balsamo", "Busto Arsizio", "Roma",
    "Legnano", "Prato", "Cagliari", "Pistoia", "Lucca",
]

# Parole chiave usate per riconoscere gli annunci pertinenti dal nome
# dell'annuncio nel JSON-LD (es. "Capannone industriale a Milano (MI)").
KEYWORDS = ["capannone", "magazzino", "deposito"]

OUTPUT_FILE = Path(__file__).resolve().parent.parent / "opportunities" / "INPUT" / "ASTE" / "dataset_asteflorio_completo.json"
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
DEFAULT_MAX_PAGES = 15

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
            logging.FileHandler(Path(__file__).resolve().parent / "scraper_asteflorio.log", encoding="utf-8"),
        ],
    )


def slugify(city: str) -> str:
    normalized = unicodedata.normalize("NFKD", city)
    ascii_str = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_str = ascii_str.lower().strip()
    ascii_str = re.sub(r"[^a-z0-9]+", "-", ascii_str)
    return ascii_str.strip("-")


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


def is_relevant(name: str) -> bool:
    lowered = (name or "").lower()
    return any(kw in lowered for kw in KEYWORDS)


# ============================================================================
# PARSING
# ============================================================================

def parse_listing_cards(html: str) -> List[dict]:
    """
    Estrae gli annunci dal blocco JSON-LD "CollectionPage"/"ItemList"
    incorporato nella pagina di ricerca per città.
    """
    soup = BeautifulSoup(html, "lxml")
    results: List[dict] = []
    seen_urls = set()

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        item_list = data.get("mainEntity") if isinstance(data, dict) else None
        if not isinstance(item_list, dict) or item_list.get("@type") != "ItemList":
            continue

        for element in item_list.get("itemListElement", []):
            item = element.get("item") if isinstance(element, dict) else None
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            offers = item.get("offers") if isinstance(item.get("offers"), dict) else {}
            id_match = re.search(r"-(\d+)$", url)

            results.append({
                "id": id_match.group(1) if id_match else url,
                "url": url,
                "titolo": item.get("name"),
                "prezzo": offers.get("price"),
                "valuta": offers.get("priceCurrency"),
                "immagine": item.get("image"),
                "descrizione_completa": item.get("name"),
            })

    return results


# ============================================================================
# SCRAPER
# ============================================================================

class AsteflorioScraper:
    def __init__(
        self,
        delay_min: float = 1.0,
        delay_max: float = 2.5,
        max_pages: int = DEFAULT_MAX_PAGES,
        resume: bool = True,
        output_path: Path = OUTPUT_FILE,
    ) -> None:
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.max_pages = max_pages
        self.resume = resume
        self.output_path = output_path

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
                    return None
                logging.warning("HTTP %s per %s (tentativo %d/%d)", resp.status_code, url, attempt, MAX_RETRIES)
            except Exception as exc:
                logging.warning("Errore di rete su %s: %s (tentativo %d/%d)", url, exc, attempt, MAX_RETRIES)
            time.sleep(2 ** attempt)
        logging.error("Impossibile recuperare %s dopo %d tentativi.", url, MAX_RETRIES)
        return None

    @staticmethod
    def build_url(city_slug: str, page: int) -> str:
        base = f"{BASE_URL}/aste/citta/{city_slug}"
        return f"{base}?page={page}" if page > 1 else base

    def scrape_city(self, city: str) -> int:
        city_slug = slugify(city)
        logging.info("=== Città: %s (slug: %s) ===", city, city_slug)

        new_count = 0
        seen_ids: set = set()
        page = 1
        while page <= self.max_pages:
            url = self.build_url(city_slug, page)
            html = self.fetch(url)
            if html is None:
                break

            results = parse_listing_cards(html)
            if not results:
                if page == 1:
                    logging.info("Nessun annuncio trovato per %s.", city)
                else:
                    logging.info("Fine paginazione per %s a pagina %d.", city, page)
                break

            current_ids = {r["id"] for r in results}
            new_ids = current_ids - seen_ids
            if not new_ids:
                logging.info("Pagina %d senza annunci nuovi: fine paginazione per %s.", page, city)
                break
            seen_ids |= current_ids

            relevant = [r for r in results if r["id"] in new_ids and is_relevant(r["titolo"])]
            logging.info(
                "Pagina %d: %d annunci totali (%d nuovi, %d pertinenti capannoni/magazzini/depositi).",
                page, len(results), len(new_ids), len(relevant),
            )

            for item in relevant:
                key = f"asteflorio_{item['id']}"
                if self.resume and key in self.dataset["listings"]:
                    continue
                self.dataset["listings"][key] = {
                    "source": "asteflorio.it",
                    "city": city,
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

    def run(self, cities: List[str]) -> None:
        for city in cities:
            try:
                added = self.scrape_city(city)
                logging.info("-> %d nuovi annunci per %s.", added, city)
            except Exception:
                logging.exception("Errore durante lo scraping di %s", city)
            finally:
                self.save()
            polite_sleep(self.delay_min, self.delay_max)

        logging.info("Scraping completato. Totale annunci nel dataset: %d", len(self.dataset["listings"]))


# ============================================================================
# CLI
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scraper per Asteflorio.it - aste giudiziarie.")
    parser.add_argument("--cities", type=str, default=None, help="Lista di città separate da virgola (default: tutte e 36).")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Numero massimo di pagine per città.")
    parser.add_argument("--delay-min", type=float, default=1.0, help="Delay minimo (secondi) tra le richieste.")
    parser.add_argument("--delay-max", type=float, default=2.5, help="Delay massimo (secondi) tra le richieste.")
    parser.add_argument("--no-resume", action="store_true", help="Ignora il dataset esistente e riparte da zero.")
    parser.add_argument("--output", type=str, default=str(OUTPUT_FILE), help="Percorso del file JSON di output.")
    parser.add_argument("--log-level", type=str, default="INFO", help="Livello di log (DEBUG, INFO, WARNING, ERROR).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    cities = CITIES
    if args.cities:
        requested = {c.strip() for c in args.cities.split(",")}
        cities = [c for c in CITIES if c in requested]

    scraper = AsteflorioScraper(
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        max_pages=args.max_pages,
        resume=not args.no_resume,
        output_path=Path(args.output),
    )

    try:
        scraper.run(cities)
    except KeyboardInterrupt:
        logging.warning("Interruzione manuale ricevuta: salvataggio dello stato parziale...")
        scraper.save()
    except Exception:
        logging.exception("Errore fatale durante lo scraping.")
        scraper.save()
        sys.exit(1)


if __name__ == "__main__":
    main()
