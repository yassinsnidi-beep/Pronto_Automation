#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_medianord_master.py
=============================

Scraper per Medianord.it — agenzia/network immobiliare (NON aste giudiziarie:
è un portale di compravendita "normale", come Immobiliare/Idealista, focalizzato
sul Nord Italia). Estrae capannoni industriali e magazzini/depositi IN VENDITA,
filtrando per le 36 città target.

*** VALIDATO DAL VIVO ***
Struttura delle card e categorie confermate con richieste HTTP reali.

*** LIMITE DI COPERTURA GEOGRAFICA NOTO ***
Medianord opera principalmente nel Nord Italia (rete di agenzie collegate al
gestionale "gestim.biz"). È MOLTO probabile che molte delle 36 città target
(specialmente al Centro-Sud: Roma, Firenze, Cagliari, Pistoia, Lucca, Prato,
Pisa, Ancona...) non abbiano alcun annuncio su questo sito: non è un difetto
dello script, è la reale area di copertura del portale. Lo script salva
comunque tutti gli annunci trovati; se una città target non compare mai nel
dataset, molto probabilmente Medianord non ha presenza in quella zona.

Tecnologia
----------
- `requests` + `BeautifulSoup`: sito server-side renderizzato, nessuna
  protezione anti-bot rilevata, nessun browser necessario.
- Le due pagine di categoria già includono TUTTI gli annunci in un'unica
  pagina (nessuna paginazione rilevata nei test):
    - https://www.medianord.it/capannoni-industriali-affitto-vendita/
    - https://www.medianord.it/magazzini-depositi-affitto-vendita/
  Ogni card riporta un tag "In Vendita" / "In Affitto": si filtrano SOLO gli
  annunci "In Vendita", scartando gli affitti.

Installazione
--------------
    pip install requests beautifulsoup4 lxml

Esecuzione
----------
    python scraper_medianord_master.py

Output
------
    scrapers_v2/dataset_medianord_completo.json
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
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

BASE_URL = "https://www.medianord.it"

CATEGORY_URLS: Dict[str, str] = {
    "capannoni-industriali": f"{BASE_URL}/capannoni-industriali-affitto-vendita/",
    "magazzini-depositi": f"{BASE_URL}/magazzini-depositi-affitto-vendita/",
}

CITIES: List[str] = [
    "Bolzano", "Bologna", "Trento", "Padova", "Milano", "Parma", "Modena",
    "Reggio Emilia", "Brescia", "Monza", "Bergamo", "Verona", "Vicenza",
    "Firenze", "Pavia", "Como", "Piacenza", "Treviso", "Novara", "Cremona",
    "Cesena", "Forlì", "Pisa", "Udine", "Varese", "Ravenna", "Ancona",
    "Sesto San Giovanni", "Cinisello Balsamo", "Busto Arsizio", "Roma",
    "Legnano", "Prato", "Cagliari", "Pistoia", "Lucca",
]
CITIES_LOWER = {c.lower() for c in CITIES}

OUTPUT_FILE = Path(__file__).resolve().parent.parent / "opportunities" / "INPUT" / "ASTE" / "dataset_medianord_completo.json"
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
DEFAULT_MAX_PAGES = 5  # difensivo: nei test il sito non ha mostrato paginazione

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
            logging.FileHandler(Path(__file__).resolve().parent / "scraper_medianord.log", encoding="utf-8"),
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


def matches_target_city(comune: Optional[str]) -> bool:
    if not comune:
        return False
    return comune.strip().lower() in CITIES_LOWER


# ============================================================================
# PARSING
# ============================================================================

def parse_listing_cards(html: str) -> List[dict]:
    """
    Estrae gli annunci dalla pagina di categoria. Ogni card è un
    <div class="offer_box"> con link di dettaglio, comune, tag vendita/affitto,
    tipologia, superficie, codice e prezzo.
    """
    soup = BeautifulSoup(html, "lxml")
    results: List[dict] = []
    seen_ids = set()

    for box in soup.select("div.offer_box"):
        link = box.select_one("a.trigger_zoom") or box.select_one("a[href]")
        href = link.get("href", "") if link else ""
        if not href:
            continue

        # L'id del listing è l'ultimo segmento di path (es. "v1437-f3", "a1421-f").
        match = re.search(r"/([a-z]\d+(?:-[a-z0-9]+)?)/?$", href.rstrip("/"), re.IGNORECASE)
        listing_id = match.group(1) if match else href
        if listing_id in seen_ids:
            continue
        seen_ids.add(listing_id)

        tag_el = box.select_one("span.top_tag")
        tag = tag_el.get_text(strip=True) if tag_el else None

        comune_el = box.select_one("div.place span:not(.fal)")
        comune = comune_el.get_text(strip=True) if comune_el else None

        tipo_el = box.select_one("div.top_info h6 a") or box.select_one("h6 a")
        tipo = tipo_el.get_text(strip=True) if tipo_el else None

        full_text = box.get_text(" | ", strip=True)
        mq_match = re.search(r"([\d.]+)\s*mq", full_text)
        prezzo_match = re.search(r"€\s*([\d.]+)", full_text)
        codice_match = re.search(r"Cod\.\s*([\w.\-]+)", full_text)

        results.append({
            "id": listing_id,
            "url": href if href.startswith("http") else f"{BASE_URL}{href}",
            "tipologia": tipo,
            "comune": comune,
            "in_vendita": bool(tag and "vendita" in tag.lower()),
            "superficie_mq": mq_match.group(1) if mq_match else None,
            "prezzo": prezzo_match.group(1) if prezzo_match else None,
            "codice": codice_match.group(1) if codice_match else listing_id,
            "descrizione_completa": full_text,
        })

    return results


def has_next_page(html: str, current_page: int) -> bool:
    """Rilevamento difensivo di un'eventuale paginazione non vista nei test."""
    return bool(re.search(rf'(?:pagina|page)[/=]{current_page + 1}\b', html, re.IGNORECASE))


# ============================================================================
# SCRAPER
# ============================================================================

class MedianordScraper:
    def __init__(
        self,
        delay_min: float = 1.0,
        delay_max: float = 2.5,
        max_pages: int = DEFAULT_MAX_PAGES,
        resume: bool = True,
        output_path: Path = OUTPUT_FILE,
        only_target_city: bool = True,
        only_vendita: bool = True,
    ) -> None:
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.max_pages = max_pages
        self.resume = resume
        self.output_path = output_path
        self.only_target_city = only_target_city
        self.only_vendita = only_vendita

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

    def scrape_category(self, categoria: str, base_url: str) -> int:
        logging.info("=== Categoria: %s ===", categoria)
        new_count = 0
        page = 1
        while page <= self.max_pages:
            url = base_url if page == 1 else f"{base_url}page/{page}/"
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

            filtered = [
                r for r in results
                if (not self.only_vendita or r["in_vendita"])
                and (not self.only_target_city or matches_target_city(r["comune"]))
            ]
            logging.info(
                "Pagina %d: %d annunci totali, %d in vendita nelle 36 città target.",
                page, len(results), len(filtered),
            )

            for item in filtered:
                key = f"medianord_{categoria}_{item['id']}"
                if self.resume and key in self.dataset["listings"]:
                    continue
                self.dataset["listings"][key] = {
                    "source": "medianord.it",
                    "categoria": categoria,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                    **item,
                }
                new_count += 1

            if not has_next_page(html, page):
                break

            page += 1
            polite_sleep(self.delay_min, self.delay_max)

        return new_count

    def save(self) -> None:
        self.dataset["generated_at"] = datetime.now(timezone.utc).isoformat()
        self.dataset["total"] = len(self.dataset["listings"])
        atomic_save_json(self.dataset, self.output_path)
        logging.info("Dataset salvato in %s (%d annunci totali).", self.output_path, self.dataset["total"])

    def run(self) -> None:
        for categoria, url in CATEGORY_URLS.items():
            try:
                added = self.scrape_category(categoria, url)
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
    parser = argparse.ArgumentParser(description="Scraper per Medianord.it - capannoni/magazzini in vendita.")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Numero massimo di pagine per categoria (difensivo).")
    parser.add_argument("--delay-min", type=float, default=1.0, help="Delay minimo (secondi) tra le richieste.")
    parser.add_argument("--delay-max", type=float, default=2.5, help="Delay massimo (secondi) tra le richieste.")
    parser.add_argument("--no-resume", action="store_true", help="Ignora il dataset esistente e riparte da zero.")
    parser.add_argument("--no-city-filter", action="store_true", help="Non filtrare per le 36 città target: salva tutti gli annunci della categoria.")
    parser.add_argument("--include-affitto", action="store_true", help="Includi anche gli annunci in affitto (default: solo vendita).")
    parser.add_argument("--output", type=str, default=str(OUTPUT_FILE), help="Percorso del file JSON di output.")
    parser.add_argument("--log-level", type=str, default="INFO", help="Livello di log (DEBUG, INFO, WARNING, ERROR).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    scraper = MedianordScraper(
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        max_pages=args.max_pages,
        resume=not args.no_resume,
        output_path=Path(args.output),
        only_target_city=not args.no_city_filter,
        only_vendita=not args.include_affitto,
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
