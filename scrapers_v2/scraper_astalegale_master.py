#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_astalegale_master.py
==============================

Scraper per Astalegale.net (piattaforma condivisa anche da Asteimmobili.it e
Portaleaste.com — stesso identico sito, mirror su 3 domini). Cerca capannoni,
magazzini e depositi in vendita giudiziaria, filtrando per le 36 città target.

*** VALIDATO DAL VIVO *** — pattern URL e struttura card confermati con
richieste HTTP reali.

Tecnologia
----------
- `requests` + `BeautifulSoup`: la pagina `/Immobili` è un componente Vue ma
  RENDERIZZATO SERVER-SIDE (SSR) con dati reali già nell'HTML — non serve
  Playwright.
- Paginazione: `/Immobili?page=N` FUNZIONA (confermato: pagina 1, 2, 3 danno
  risultati diversi).
- Non è stato individuato un filtro diretto per categoria o città via query
  string (nessun form/select visibile lato server): si scarica quindi
  l'intero elenco (tutte le categorie, tutta Italia) e si filtra lato script
  cercando "capannone"/"magazzino"/"deposito" + il nome di una delle 36
  città target nell'URL/slug dell'annuncio (che li contiene già, es.
  "/Aste/Detail/B2422140-Magazzino-Via-Piave-35-Piombino-Dese" — nota: in
  questo esempio "Piombino Dese" non è tra le 36 città target e verrebbe
  scartato correttamente).

Installazione
--------------
    pip install requests beautifulsoup4 lxml

Esecuzione
----------
    python scraper_astalegale_master.py

Output
------
    scrapers_v2/dataset_astalegale_completo.json
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

BASE_URL = "https://www.astalegale.net"
LISTING_URL = f"{BASE_URL}/Immobili"

KEYWORDS = ["capannone", "magazzino", "deposito"]

CITIES: List[str] = [
    "Bolzano", "Bologna", "Trento", "Padova", "Milano", "Parma", "Modena",
    "Reggio Emilia", "Brescia", "Monza", "Bergamo", "Verona", "Vicenza",
    "Firenze", "Pavia", "Como", "Piacenza", "Treviso", "Novara", "Cremona",
    "Cesena", "Forlì", "Pisa", "Udine", "Varese", "Ravenna", "Ancona",
    "Sesto San Giovanni", "Cinisello Balsamo", "Busto Arsizio", "Roma",
    "Legnano", "Prato", "Cagliari", "Pistoia", "Lucca",
]
CITIES_LOWER = {c.lower() for c in CITIES}

OUTPUT_FILE = Path(__file__).resolve().parent.parent / "opportunities" / "INPUT" / "ASTE" / "dataset_astalegale_completo.json"
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
DEFAULT_MAX_PAGES = 60  # l'elenco non è filtrato per categoria: servono più pagine per coprire tutta Italia

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
            logging.FileHandler(Path(__file__).resolve().parent / "scraper_astalegale.log", encoding="utf-8"),
        ],
    )


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_str = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_str.lower()).strip("-")


CITY_SLUGS = {slugify(c): c for c in CITIES}


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


def find_target_city(slug: str) -> Optional[str]:
    for city_slug, city in CITY_SLUGS.items():
        if slug.endswith(city_slug) or f"-{city_slug}-" in f"-{slug}-":
            return city
    return None


def is_relevant_category(slug: str) -> bool:
    lowered = slug.lower()
    return any(kw in lowered for kw in KEYWORDS)


# ============================================================================
# PARSING
# ============================================================================

# La pagina incorpora un payload dati (stato Nuxt/Vue serializzato) con la
# descrizione catastale completa di ogni immobile — molto più ricca del solo
# testo visibile in card: contiene già superficie (mq) e valore di perizia.
# Non è un JSON annidato "normale" (è un array piatto con riferimenti per
# indice), quindi invece di deserializzarlo per intero si isola con una
# regex la stringa di descrizione (riconoscibile dalla parola "consistenza"
# seguita da "mq") e si abbina all'ID dell'annuncio più vicino subito dopo.
RICH_DESC_RE = re.compile(r'"(-\s*[^"]{20,700}?consistenza[^"]{0,400}?)"')
ID_AFTER_RE = re.compile(r'"([A-Z]\d{6,8})"')


def extract_rich_data(html: str) -> Dict[str, dict]:
    """Ritorna {listing_id: {superficie_mq, valore_perizia, descrizione_catastale}}."""
    rich: Dict[str, dict] = {}
    for m in RICH_DESC_RE.finditer(html):
        raw_desc = m.group(1)
        tail = html[m.end():m.end() + 300]
        id_match = ID_AFTER_RE.search(tail)
        if not id_match:
            continue
        listing_id = id_match.group(1)
        if listing_id in rich:
            continue  # tiene solo la prima occorrenza (bene principale del lotto)

        try:
            # La stringa è un valore JSON con escape (/, \n, ecc.): la
            # decodifica come stringa JSON per ripulirla correttamente.
            desc = json.loads(f'"{raw_desc}"')
        except (json.JSONDecodeError, ValueError):
            desc = raw_desc.replace("\\n", " ").replace("\\u002F", "/")

        mq_match = re.search(r"consistenza\s*([\d.,]+)\s*mq", desc, re.IGNORECASE)
        perizia_match = re.search(r"Valore in\s*perizia\s*([\d.,]+)", desc, re.IGNORECASE)

        rich[listing_id] = {
            "superficie_mq": mq_match.group(1) if mq_match else None,
            "valore_perizia": perizia_match.group(1) if perizia_match else None,
            "descrizione_catastale": re.sub(r"\s+", " ", desc).strip(),
        }
    return rich


def parse_listing_cards(html: str) -> List[dict]:
    soup = BeautifulSoup(html, "lxml")
    rich_data = extract_rich_data(html)
    results: List[dict] = []
    seen_ids = set()

    for a in soup.select('a[href^="/Aste/Detail/"]'):
        href = a.get("href", "")
        match = re.search(r"/Aste/Detail/([A-Z0-9]+)-(.+)$", href)
        if not match:
            continue
        listing_id, slug = match.group(1), match.group(2)
        if listing_id in seen_ids:
            continue
        seen_ids.add(listing_id)

        card = a.find_parent("div", class_="card") or a.parent
        card_text = card.get_text(" | ", strip=True) if card else ""

        prezzo_match = re.search(r"€\s?([\d.]+,\d{2})", card_text)
        data_match = re.search(r"\d{2}/\d{2}/\d{4}\s*-\s*\d{2}:\d{2}", card_text)
        tribunale_match = re.search(r"Tribunale di [\w' ]+", card_text)
        # Il testo della card unisce i nodi con " | ": l'importo può essere separato
        # dall'etichetta da quel pipe invece che da un semplice spazio.
        offerta_match = re.search(r"Offerta minima:?\s*\|?\s*€\s?([\d.]+,\d{2})", card_text)

        # Il nome della categoria è il primo segmento leggibile dello slug
        # (es. "Magazzino-Via-Piave-35-Piombino-Dese" -> "Magazzino").
        categoria_match = re.match(r"([A-Za-z]+)", slug)

        rich = rich_data.get(listing_id, {})
        # La descrizione catastale (dal payload dati) è molto più ricca e
        # affidabile del semplice testo della card: se disponibile, sostituisce
        # "descrizione_completa"; altrimenti si tiene il testo grezzo della card.
        descrizione_completa = rich.get("descrizione_catastale") or card_text

        results.append({
            "id": listing_id,
            "url": f"{BASE_URL}{href}",
            "titolo": slug.replace("-", " "),
            "categoria": categoria_match.group(1) if categoria_match else None,
            "citta": find_target_city(slugify(slug)),
            "prezzo": prezzo_match.group(1) if prezzo_match else None,
            "offerta_minima": offerta_match.group(1) if offerta_match else None,
            "tribunale": tribunale_match.group(0) if tribunale_match else None,
            "superficie_mq": rich.get("superficie_mq"),
            "valore_perizia": rich.get("valore_perizia"),
            "data_asta": data_match.group(0) if data_match else None,
            "descrizione_completa": descrizione_completa,
        })

    return results


# ============================================================================
# SCRAPER
# ============================================================================

class AstalegaleScraper:
    def __init__(
        self,
        delay_min: float = 1.0,
        delay_max: float = 2.5,
        max_pages: int = DEFAULT_MAX_PAGES,
        resume: bool = True,
        output_path: Path = OUTPUT_FILE,
        only_target_area: bool = True,
    ) -> None:
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
                    return None
                logging.warning("HTTP %s per %s (tentativo %d/%d)", resp.status_code, url, attempt, MAX_RETRIES)
            except Exception as exc:
                logging.warning("Errore di rete su %s: %s (tentativo %d/%d)", url, exc, attempt, MAX_RETRIES)
            time.sleep(2 ** attempt)
        logging.error("Impossibile recuperare %s dopo %d tentativi.", url, MAX_RETRIES)
        return None

    def run(self) -> None:
        seen_ids: set = set()
        page = 1
        while page <= self.max_pages:
            url = LISTING_URL if page == 1 else f"{LISTING_URL}?page={page}"
            html = self.fetch(url)
            if html is None:
                break

            results = parse_listing_cards(html)
            if not results:
                if page == 1:
                    logging.info("Nessun annuncio trovato.")
                else:
                    logging.info("Fine paginazione a pagina %d.", page)
                break

            current_ids = {r["id"] for r in results}
            new_ids = current_ids - seen_ids
            if not new_ids:
                logging.info("Pagina %d senza annunci nuovi: fine paginazione.", page)
                break
            seen_ids |= current_ids

            relevant = [
                r for r in results
                if r["id"] in new_ids
                and is_relevant_category(r["titolo"])
                and (not self.only_target_area or r["citta"])
            ]
            logging.info(
                "Pagina %d: %d annunci totali (%d nuovi), %d pertinenti nelle 36 città target.",
                page, len(results), len(new_ids), len(relevant),
            )

            for item in relevant:
                key = f"astalegale_{item['id']}"
                if self.resume and key in self.dataset["listings"]:
                    continue
                self.dataset["listings"][key] = {
                    "source": "astalegale.net",
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                    **item,
                }

            page += 1
            polite_sleep(self.delay_min, self.delay_max)

        self.save()
        logging.info("Scraping completato. Totale annunci nel dataset: %d", len(self.dataset["listings"]))

    def save(self) -> None:
        self.dataset["generated_at"] = datetime.now(timezone.utc).isoformat()
        self.dataset["total"] = len(self.dataset["listings"])
        atomic_save_json(self.dataset, self.output_path)
        logging.info("Dataset salvato in %s (%d annunci totali).", self.output_path, self.dataset["total"])


# ============================================================================
# CLI
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scraper per Astalegale.net (+ Asteimmobili.it, Portaleaste.com).")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Numero massimo di pagine.")
    parser.add_argument("--delay-min", type=float, default=1.0, help="Delay minimo (secondi) tra le richieste.")
    parser.add_argument("--delay-max", type=float, default=2.5, help="Delay massimo (secondi) tra le richieste.")
    parser.add_argument("--no-resume", action="store_true", help="Ignora il dataset esistente e riparte da zero.")
    parser.add_argument("--no-area-filter", action="store_true", help="Non filtrare per le 36 città target.")
    parser.add_argument("--output", type=str, default=str(OUTPUT_FILE), help="Percorso del file JSON di output.")
    parser.add_argument("--log-level", type=str, default="INFO", help="Livello di log (DEBUG, INFO, WARNING, ERROR).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    scraper = AstalegaleScraper(
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
