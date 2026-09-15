#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_caseasta_master.py
============================

Scraper per Case-Asta.it — portale di aste immobiliari con navigazione
provincia/comune. Cerca capannoni/magazzini/depositi nelle 36 città target.

*** VALIDATO DAL VIVO *** — struttura card e URL confermati con richieste
HTTP reali.

Tecnologia
----------
- `requests` + `BeautifulSoup`, nessun browser necessario.
- URL per comune: https://case-asta.it/aste/<provincia-slug>/<comune-slug>
  (es. "aste/milano/sesto-san-giovanni"), confermato funzionante per capoluogo
  e non-capoluogo. Nessun filtro di categoria individuato via query string:
  si scarica tutta la pagina del comune e si filtra lato script per
  "capannone"/"magazzino"/"deposito" nel titolo.
- Paginazione (corretta settembre 2026): il parametro giusto è `?page=N` (in
  inglese) — `?pagina=2` non produceva differenze perché non è il nome giusto
  del parametro (stesso bug trovato e corretto su scraper_fallcoaste_master.py).
  Verificato dal vivo: Milano ha 84 annunci reali su 5 pagine, non 25.

Installazione
--------------
    pip install requests beautifulsoup4 lxml

Esecuzione
----------
    python scraper_caseasta_master.py

Output
------
    scrapers_v2/dataset_caseasta_completo.json
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

BASE_URL = "https://case-asta.it"

KEYWORDS = ["capannone", "magazzino", "deposito"]

CITIES: List[str] = [
    "Bolzano", "Bologna", "Trento", "Padova", "Milano", "Parma", "Modena",
    "Reggio Emilia", "Brescia", "Monza", "Bergamo", "Verona", "Vicenza",
    "Firenze", "Pavia", "Como", "Piacenza", "Treviso", "Novara", "Cremona",
    "Cesena", "Forlì", "Pisa", "Udine", "Varese", "Ravenna", "Ancona",
    "Sesto San Giovanni", "Cinisello Balsamo", "Busto Arsizio", "Roma",
    "Legnano", "Prato", "Cagliari", "Pistoia", "Lucca",
]

CITY_PROVINCE: Dict[str, str] = {
    "Bolzano": "Bolzano", "Bologna": "Bologna", "Trento": "Trento", "Padova": "Padova",
    "Milano": "Milano", "Parma": "Parma", "Modena": "Modena", "Reggio Emilia": "Reggio Emilia",
    "Brescia": "Brescia", "Monza": "Monza e della Brianza", "Bergamo": "Bergamo",
    "Verona": "Verona", "Vicenza": "Vicenza", "Firenze": "Firenze", "Pavia": "Pavia",
    "Como": "Como", "Piacenza": "Piacenza", "Treviso": "Treviso", "Novara": "Novara",
    "Cremona": "Cremona", "Cesena": "Forlì-Cesena", "Forlì": "Forlì-Cesena", "Pisa": "Pisa",
    "Udine": "Udine", "Varese": "Varese", "Ravenna": "Ravenna", "Ancona": "Ancona",
    "Sesto San Giovanni": "Milano", "Cinisello Balsamo": "Milano", "Busto Arsizio": "Varese",
    "Roma": "Roma", "Legnano": "Milano", "Prato": "Prato", "Cagliari": "Cagliari",
    "Pistoia": "Pistoia", "Lucca": "Lucca",
}

OUTPUT_FILE = Path(__file__).resolve().parent.parent / "opportunities" / "INPUT" / "ASTE" / "dataset_caseasta_completo.json"
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
DEFAULT_MAX_PAGES = 40  # safety cap per comune (verificato dal vivo: Milano arriva a 4-5 pagine reali)

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
            logging.FileHandler(Path(__file__).resolve().parent / "scraper_caseasta.log", encoding="utf-8"),
        ],
    )


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_str = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_str.lower()).strip("-")


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


def is_relevant(titolo: Optional[str], descrizione: Optional[str]) -> bool:
    text = f"{titolo or ''} {descrizione or ''}".lower()
    return any(kw in text for kw in KEYWORDS)


def item_matches_city(item: dict, city: str) -> bool:
    """
    Verifica che l'indirizzo dell'annuncio menzioni davvero la città cercata.
    Necessario perché, quando un comune non ha aste reali in corso, il sito
    NON restituisce una pagina vuota: mostra invece un feed generico con
    indirizzi sparsi per tutta Italia, che pagina all'infinito (verificato dal
    vivo: pagina 45 di città diverse come Lucca e Prato risultavano identiche).
    Il solo controllo "nessun id nuovo" non intercetta questo fallback, quindi
    va incrociato anche l'indirizzo con il nome della città.
    """
    indirizzo = (item.get("indirizzo") or "").lower()
    return city.lower() in indirizzo


# ============================================================================
# PARSING
# ============================================================================

def parse_listing_cards(html: str) -> List[dict]:
    soup = BeautifulSoup(html, "lxml")
    results: List[dict] = []

    for a in soup.select("a.card-immobile"):
        href = a.get("href", "")
        match = re.search(r"/([a-z\-]+)/(\d+)$", href)
        if not match:
            continue
        listing_id = match.group(2)

        titolo_el = a.select_one("p.titolo")
        prezzo_el = a.select_one("p.prezzo")
        indirizzo_el = a.select_one("p.info")
        descrizione_el = a.select_one("p.descrizione")

        results.append({
            "id": listing_id,
            "url": href if href.startswith("http") else f"{BASE_URL}{href}",
            "titolo": titolo_el.get_text(strip=True) if titolo_el else None,
            "prezzo": prezzo_el.get_text(strip=True) if prezzo_el else None,
            "indirizzo": indirizzo_el.get_text(" ", strip=True) if indirizzo_el else None,
            "descrizione_completa": descrizione_el.get_text(strip=True) if descrizione_el else None,
        })

    return results


# ============================================================================
# SCRAPER
# ============================================================================

class CaseastaScraper:
    def __init__(
        self,
        delay_min: float = 1.0,
        delay_max: float = 2.5,
        resume: bool = True,
        output_path: Path = OUTPUT_FILE,
    ) -> None:
        self.delay_min = delay_min
        self.delay_max = delay_max
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

    def scrape_city(self, city: str) -> int:
        provincia_slug = slugify(CITY_PROVINCE.get(city, city))
        comune_slug = slugify(city)
        base_url = f"{BASE_URL}/aste/{provincia_slug}/{comune_slug}"
        logging.info("=== Città: %s (%s) ===", city, base_url)

        new_count = 0
        page_num = 1
        seen_ids: set = set()
        while page_num <= DEFAULT_MAX_PAGES:
            url = base_url if page_num == 1 else f"{base_url}?page={page_num}"
            html = self.fetch(url)
            if html is None:
                if page_num == 1:
                    logging.info("Nessuna pagina trovata per %s (slug provincia/comune potrebbe non corrispondere).", city)
                break

            results = parse_listing_cards(html)
            if not results:
                if page_num > 1:
                    logging.info("Fine paginazione per %s a pagina %d.", city, page_num)
                break

            current_page_ids = {r["id"] for r in results if r.get("id")}
            new_ids_on_page = current_page_ids - seen_ids
            if not new_ids_on_page:
                logging.info("Pagina %d di %s senza annunci nuovi: fine paginazione.", page_num, city)
                break
            seen_ids |= current_page_ids

            # Se NESSUN annuncio di questa pagina menziona davvero la città cercata,
            # siamo usciti dai risultati reali e siamo entrati nel feed generico di
            # fallback del sito (vedi item_matches_city): ci si ferma qui, anche se
            # gli id sono tecnicamente "nuovi" (il feed generico è enorme e non
            # convergerebbe mai da solo).
            city_matches_on_page = sum(1 for r in results if item_matches_city(r, city))
            if city_matches_on_page == 0:
                logging.info(
                    "Pagina %d di %s: nessun annuncio con indirizzo a %s (probabile feed generico di "
                    "fallback del sito): fine paginazione.",
                    page_num, city, city,
                )
                break

            relevant = [
                r for r in results
                if r.get("id") in new_ids_on_page
                and is_relevant(r["titolo"], r["descrizione_completa"])
                and item_matches_city(r, city)
            ]
            logging.info(
                "Pagina %d, città %s: %d annunci totali, %d pertinenti (capannoni/magazzini/depositi).",
                page_num, city, len(results), len(relevant),
            )

            for item in relevant:
                key = f"caseasta_{item['id']}"
                if self.resume and key in self.dataset["listings"]:
                    continue
                self.dataset["listings"][key] = {
                    "source": "case-asta.it",
                    "city": city,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                    **item,
                }
                new_count += 1

            page_num += 1
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
    parser = argparse.ArgumentParser(description="Scraper per Case-Asta.it - aste giudiziarie.")
    parser.add_argument("--cities", type=str, default=None, help="Lista di città separate da virgola (default: tutte e 36).")
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

    scraper = CaseastaScraper(
        delay_min=args.delay_min,
        delay_max=args.delay_max,
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
