"""Adattatori per fonte: da record grezzo di ciascun dataset_<fonte>.json al
record canonico (§2.2 e §3.0.1). Ogni funzione e' scritta per NON sollevare
mai eccezioni su input parziali: un campo mancante diventa un Campo a
valore None con confidenza bassa, mai un crash (§8, "robusta ai campi
null").
"""

from __future__ import annotations

from typing import Any, Optional

from . import parser, testo, util
from .campo import campo

CANALE_ASTE = "ASTE"
CANALE_IMMID = "IMM_ID"


def _get(d: Any, *chiavi: Any, default: Any = None) -> Any:
    cur = d
    for k in chiavi:
        if cur is None:
            return default
        try:
            if isinstance(k, int):
                cur = cur[k]
            else:
                cur = cur.get(k) if isinstance(cur, dict) else default
        except (KeyError, IndexError, TypeError):
            return default
    return default if cur is None else cur


def _id_pulito(valore: Any) -> str:
    """Alcuni scraper (visto in asteflorio) a volte salvano nel campo 'id'
    l'URL intero invece di un identificativo breve. Si estrae l'ultimo
    segmento del percorso: resta stabile settimana su settimana (serve al
    confronto con lo storico, §4) ed e' leggibile nel report."""
    s = str(valore)
    if "://" in s:
        s = s.rstrip("/").rsplit("/", 1)[-1]
        s = s or str(valore)
    return s


def _record_base(id_completo: str, fonte: str, canale: str, file_origine: str, url: Optional[str]) -> dict[str, Any]:
    return {
        "id": id_completo,
        "alias_ids": [],
        "canale": canale,
        "tipo_vendita": None,
        "fonte": fonte,
        "file_origine": file_origine,
        "url": util.pulisci(url),
        "urls_altre_fonti": [],
        "titolo": None,
        "descrizione": None,
        "prezzo_eur": campo(None, "stima", "bassa"),
        "offerta_minima_eur": campo(None, "stima", "bassa"),
        "superficie_mq": campo(None, "stima", "bassa"),
        "superfici_etichettate": [],
        "comune": campo(None, "stima", "bassa"),
        "provincia": campo(None, "stima", "bassa"),
        "lat": None,
        "lon": None,
        "scadenza_asta": campo(None, "stima", "bassa"),
        "tribunale": None,
        "procedura": None,
        "lotto": None,
        "categoria": campo("altro", "stima", "bassa"),
        "altezza_m": campo(None, "stima", "bassa"),
        "piano": None,
        "n_piani": None,
        "stato_occupazione": None,
        "anno_costruzione": None,
        "diritto": campo(None, "stima", "bassa"),
        # campo interno, non fa parte dello schema di output (§5): raccoglie
        # attributi extra usati solo dalla scorecard/gate economico, in modo
        # che quei moduli restino agnostici rispetto alla fonte.
        "_attributi": {},
    }


def _superficie_da_campo_o_testo(
    rec: dict[str, Any],
    *,
    valore_campo: Any,
    testi: list[Optional[str]],
    fonte_campo: str = "json",
) -> None:
    """Riempie superficie_mq e superfici_etichettate su rec, preferendo un
    campo JSON esplicito e altrimenti cercando nel testo libero."""
    numero = parser.norma_numero_it(valore_campo) if valore_campo not in (None, "") else None
    if numero is not None:
        plausibile = parser.SUP_MIN_PLAUSIBILE <= numero <= parser.SUP_MAX_PLAUSIBILE
        rec["superficie_mq"] = campo(numero, fonte_campo, "alta" if plausibile else "bassa")
        rec["superfici_etichettate"] = [
            {"valore": numero, "etichetta": "coperta", "confidenza": "alta" if plausibile else "bassa", "testo": str(valore_campo)}
        ]
        return

    testo_unito = " ".join(t for t in testi if t)
    trovate = parser.estrai_superfici_da_testo(testo_unito)
    rec["superfici_etichettate"] = trovate
    if trovate:
        migliore = max(trovate, key=lambda r: 0 if r["confidenza"] == "bassa" else 1)
        rec["superficie_mq"] = campo(migliore["valore"], "regex_testo", migliore["confidenza"], testo=migliore["testo"])
    else:
        rec["superficie_mq"] = campo(None, "stima", "bassa")


def _applica_comune(rec: dict[str, Any], **kwargs: Any) -> None:
    from . import geografia

    c, p = geografia.estrai_comune_provincia(**kwargs)
    rec["comune"] = c
    rec["provincia"] = p


def _applica_categoria(rec: dict[str, Any], **kwargs: Any) -> None:
    rec["categoria"] = testo.classifica_categoria(**kwargs)


def _applica_diritto_e_lotto(rec: dict[str, Any], testo_completo: Optional[str]) -> None:
    rec["diritto"] = testo.estrai_diritto(testo_completo)
    lotto = testo.estrai_lotto(testo_completo)
    if lotto:
        rec["lotto"] = lotto


# --------------------------------------------------------------------------- asteannunci


def adatta_asteannunci(id_chiave: str, raw: dict[str, Any], file_origine: str) -> dict[str, Any]:
    id_completo = _id_pulito(id_chiave)
    rec = _record_base(id_completo, "asteannunci.it", CANALE_ASTE, file_origine, raw.get("url"))
    rec["tipo_vendita"] = "asta"
    rec["titolo"] = util.pulisci(raw.get("titolo"))
    rec["descrizione"] = util.pulisci(raw.get("descrizione_completa"))
    rec["prezzo_eur"] = campo(parser.norma_prezzo(raw.get("prezzo_base")), "json", "alta" if raw.get("prezzo_base") else "bassa")
    rec["scadenza_asta"] = campo(parser.norma_data(raw.get("data_asta")), "json", "alta" if raw.get("data_asta") else "bassa")
    rec["tribunale"] = util.pulisci(raw.get("tribunale"))
    rec["procedura"] = testo.normalizza_procedura(rec["tribunale"], raw.get("procedura"))
    _applica_comune(rec, indirizzo=raw.get("indirizzo"), titolo=raw.get("titolo"))
    _applica_categoria(rec, titolo=raw.get("titolo"), descrizione=raw.get("descrizione_completa"))
    _superficie_da_campo_o_testo(rec, valore_campo=None, testi=[raw.get("titolo"), raw.get("descrizione_completa")])
    _applica_diritto_e_lotto(rec, raw.get("descrizione_completa"))
    return rec


# --------------------------------------------------------------------------- asteflorio


def adatta_asteflorio(id_chiave: str, raw: dict[str, Any], file_origine: str) -> dict[str, Any]:
    id_completo = _id_pulito(id_chiave)
    rec = _record_base(id_completo, "asteflorio.it", CANALE_ASTE, file_origine, raw.get("url"))
    rec["tipo_vendita"] = "asta"
    rec["titolo"] = util.pulisci(raw.get("titolo"))
    rec["descrizione"] = util.pulisci(raw.get("descrizione_completa")) or rec["titolo"]
    rec["prezzo_eur"] = campo(parser.norma_prezzo(raw.get("prezzo")), "json", "alta" if raw.get("prezzo") not in (None, "") else "bassa")
    _applica_comune(rec, titolo=raw.get("titolo"), city_fallback=raw.get("city"))
    _applica_categoria(rec, titolo=raw.get("titolo"), descrizione=raw.get("descrizione_completa"))
    _superficie_da_campo_o_testo(rec, valore_campo=None, testi=[raw.get("titolo"), raw.get("descrizione_completa")])
    _applica_diritto_e_lotto(rec, raw.get("descrizione_completa"))
    return rec


# --------------------------------------------------------------------------- caseasta


def adatta_caseasta(id_chiave: str, raw: dict[str, Any], file_origine: str) -> dict[str, Any]:
    id_completo = _id_pulito(id_chiave)
    rec = _record_base(id_completo, "case-asta.it", CANALE_ASTE, file_origine, raw.get("url"))
    rec["tipo_vendita"] = "asta"
    rec["titolo"] = util.pulisci(raw.get("titolo"))
    rec["descrizione"] = util.pulisci(raw.get("descrizione_completa"))
    rec["prezzo_eur"] = campo(parser.norma_prezzo(raw.get("prezzo")), "json", "alta" if raw.get("prezzo") not in (None, "") else "bassa")
    _applica_comune(rec, indirizzo=raw.get("indirizzo"), city_fallback=raw.get("city"))
    _applica_categoria(rec, titolo=raw.get("titolo"), descrizione=raw.get("descrizione_completa"))
    _superficie_da_campo_o_testo(rec, valore_campo=None, testi=[raw.get("titolo"), raw.get("descrizione_completa")])
    _applica_diritto_e_lotto(rec, raw.get("descrizione_completa"))
    if rec["descrizione"] and rec["descrizione"].endswith("..."):
        rec["_attributi"]["descrizione_troncata"] = True
    return rec


# --------------------------------------------------------------------------- fallcoaste


def adatta_fallcoaste(id_chiave: str, raw: dict[str, Any], file_origine: str) -> dict[str, Any]:
    id_completo = _id_pulito(id_chiave)
    rec = _record_base(id_completo, "fallcoaste.it", CANALE_ASTE, file_origine, raw.get("url"))
    rec["tipo_vendita"] = "asta"
    rec["titolo"] = util.pulisci(raw.get("titolo"))
    rec["descrizione"] = util.pulisci(raw.get("descrizione_completa"))
    rec["prezzo_eur"] = campo(parser.norma_prezzo(raw.get("prezzo")), "json", "alta" if raw.get("prezzo") not in (None, "") else "bassa")
    scadenza_raw = raw.get("data_termine_asta") or raw.get("data_inizio")
    rec["scadenza_asta"] = campo(parser.norma_data(scadenza_raw), "json", "alta" if scadenza_raw else "bassa")
    rec["tribunale"] = util.pulisci(raw.get("tribunale"))
    rec["procedura"] = testo.normalizza_procedura(rec["tribunale"], raw.get("procedura"))
    # niente campo indirizzo su fallcoaste: il comune va cercato SOLO nel
    # titolo, e SOLO col pattern libero (mai ancorato: il titolo e' una
    # frase discorsiva, non inizia con il comune, vedi geografia.py)
    _applica_comune(rec, titolo=raw.get("titolo"))
    _applica_categoria(rec, titolo=raw.get("titolo"), descrizione=raw.get("descrizione_completa"))
    _superficie_da_campo_o_testo(
        rec,
        valore_campo=raw.get("superficie_mq"),
        testi=[raw.get("titolo"), raw.get("descrizione_completa")],
    )
    _applica_diritto_e_lotto(rec, (raw.get("titolo") or "") + " " + (raw.get("descrizione_completa") or ""))
    return rec


# --------------------------------------------------------------------------- medianord


def adatta_medianord(id_chiave: str, raw: dict[str, Any], file_origine: str) -> dict[str, Any]:
    id_completo = _id_pulito(id_chiave)
    rec = _record_base(id_completo, "medianord.it", CANALE_ASTE, file_origine, raw.get("url"))
    rec["tipo_vendita"] = "vendita"
    rec["titolo"] = util.pulisci(raw.get("tipologia")) or util.pulisci(raw.get("codice"))
    rec["descrizione"] = util.pulisci(raw.get("descrizione_completa"))
    prezzo_grezzo = raw.get("prezzo")
    rec["prezzo_eur"] = campo(parser.norma_prezzo(prezzo_grezzo), "json", "alta" if prezzo_grezzo not in (None, "") else "bassa")
    _applica_comune(rec, comune_esplicito=raw.get("comune"))
    _applica_categoria(rec, titolo=raw.get("tipologia"), descrizione=raw.get("descrizione_completa"))
    _superficie_da_campo_o_testo(
        rec,
        valore_campo=raw.get("superficie_mq"),
        testi=[raw.get("descrizione_completa")],
    )
    return rec


# --------------------------------------------------------------------------- venditegiudiziarie / vgi


def adatta_vgi(id_chiave: str, raw: dict[str, Any], file_origine: str) -> dict[str, Any]:
    id_completo = _id_pulito(id_chiave)
    rec = _record_base(id_completo, "venditegiudiziarieitalia.it", CANALE_ASTE, file_origine, raw.get("url"))
    rec["tipo_vendita"] = "asta"
    rec["titolo"] = util.pulisci(raw.get("titolo"))
    rec["descrizione"] = util.pulisci(raw.get("descrizione_completa"))
    rec["prezzo_eur"] = campo(parser.norma_prezzo(raw.get("prezzo_base")), "json", "alta" if raw.get("prezzo_base") else "bassa")
    rec["offerta_minima_eur"] = campo(parser.norma_prezzo(raw.get("offerta_minima")), "json", "alta" if raw.get("offerta_minima") else "bassa")
    scadenza_raw = raw.get("termine_presentazione_offerte") or raw.get("data_vendita")
    rec["scadenza_asta"] = campo(parser.norma_data(scadenza_raw), "json", "alta" if scadenza_raw else "bassa")
    rec["tribunale"] = util.pulisci(raw.get("tribunale"))
    rec["procedura"] = testo.normalizza_procedura(rec["tribunale"], None, raw.get("numero_procedura"), raw.get("anno_procedura"))
    _applica_comune(rec, citta_completa=raw.get("citta_completa"))
    hint = "mobili" if (raw.get("categoria") or "").upper() == "MOBILI" else None
    _applica_categoria(
        rec,
        indizio_fonte=hint,
        titolo=raw.get("titolo"),
        descrizione=(raw.get("sottocategoria") or "") + " " + (raw.get("descrizione_completa") or ""),
    )
    _superficie_da_campo_o_testo(rec, valore_campo=None, testi=[raw.get("titolo"), raw.get("descrizione_completa")])
    _applica_diritto_e_lotto(rec, raw.get("descrizione_completa"))
    pubblicato_su = raw.get("pubblicato_anche_su") or []
    rec["_attributi"]["pubblicato_anche_su"] = pubblicato_su
    return rec


# --------------------------------------------------------------------------- worldcapital


def adatta_worldcapital(id_chiave: str, raw: dict[str, Any], file_origine: str) -> dict[str, Any]:
    id_completo = _id_pulito(id_chiave)
    rec = _record_base(id_completo, "worldcapital.it", CANALE_ASTE, file_origine, raw.get("url"))
    rec["tipo_vendita"] = "vendita"
    rec["titolo"] = util.pulisci(raw.get("titolo"))
    rec["descrizione"] = util.pulisci(raw.get("descrizione_completa")) or rec["titolo"]
    prezzo_grezzo = raw.get("prezzo")
    rec["prezzo_eur"] = campo(parser.norma_prezzo(prezzo_grezzo), "json", "alta" if prezzo_grezzo not in (None, "") else "bassa")
    _applica_comune(rec, titolo=raw.get("titolo"), city_fallback=raw.get("citta"))
    _applica_categoria(rec, titolo=raw.get("titolo"), descrizione=raw.get("descrizione_completa"))
    _superficie_da_campo_o_testo(
        rec,
        valore_campo=raw.get("superficie_mq"),
        testi=[raw.get("titolo"), raw.get("descrizione_completa")],
    )
    return rec


# --------------------------------------------------------------------------- idealista


def adatta_idealista(id_chiave: str, raw: dict[str, Any], file_origine: str) -> dict[str, Any]:
    id_completo = _id_pulito(id_chiave)
    rec = _record_base(id_completo, "idealista.it", CANALE_IMMID, file_origine, raw.get("url"))
    rec["tipo_vendita"] = "vendita"
    rec["titolo"] = util.pulisci(raw.get("titolo"))
    rec["descrizione"] = util.pulisci(raw.get("descrizione_completa"))
    rec["prezzo_eur"] = campo(parser.norma_prezzo(raw.get("prezzo")), "json", "alta" if raw.get("prezzo") not in (None, "") else "bassa")
    _applica_comune(rec, titolo=raw.get("titolo"), city_fallback=raw.get("city"))
    hint = "garage" if raw.get("category") == "vendita-garage" else None
    dettagli = " ".join(raw.get("details") or [])
    _applica_categoria(rec, indizio_fonte=hint, titolo=raw.get("titolo"), descrizione=(raw.get("descrizione_completa") or "") + " " + dettagli)
    testi = [raw.get("titolo"), raw.get("descrizione_completa"), dettagli]
    _superficie_da_campo_o_testo(rec, valore_campo=raw.get("superficie_mq"), testi=testi)
    _applica_diritto_e_lotto(rec, raw.get("descrizione_completa"))
    if raw.get("descrizione_completa") and len(raw["descrizione_completa"]) <= 400:
        rec["_attributi"]["descrizione_forse_troncata"] = True
    return rec


# --------------------------------------------------------------------------- immobiliare (la piu' ricca)


_ALTEZZA_MIN_HARD, _ALTEZZA_MAX_HARD = 2.0, 20.0


def adatta_immobiliare(id_chiave: str, raw: dict[str, Any], file_origine: str, *, altezza_min: float = _ALTEZZA_MIN_HARD, altezza_max: float = _ALTEZZA_MAX_HARD) -> dict[str, Any]:
    id_completo = _id_pulito(id_chiave)
    rec = _record_base(id_completo, "immobiliare.it", CANALE_IMMID, file_origine, raw.get("url"))
    rec["titolo"] = util.pulisci(raw.get("titolo"))
    rec["descrizione"] = util.pulisci(raw.get("descrizione_completa"))

    p = _get(raw, "raw_detail", "pageProps", "detailData", "realEstate", "properties", 0, default={})
    if not isinstance(p, dict):
        p = {}

    auction = p.get("auction")
    rec["tipo_vendita"] = "asta" if auction else "vendita"

    prezzo_valore = raw.get("prezzo_valore")
    if prezzo_valore is not None:
        rec["prezzo_eur"] = campo(int(prezzo_valore), "json", "alta")
    else:
        rec["prezzo_eur"] = campo(parser.norma_prezzo(raw.get("prezzo")), "json", "bassa")

    if auction:
        rec["offerta_minima_eur"] = campo(parser.norma_prezzo(auction.get("minimumOffer")), "raw_detail", "alta" if auction.get("minimumOffer") else "bassa")
        rec["scadenza_asta"] = campo(parser.norma_data(auction.get("saleDate")), "raw_detail", "alta" if auction.get("saleDate") else "bassa")
        rec["tribunale"] = util.pulisci(auction.get("auctionCourt"))
        rec["procedura"] = testo.normalizza_procedura(rec["tribunale"], auction.get("procedureNumber"))
        if auction.get("lotNumber"):
            rec["lotto"] = str(auction["lotNumber"]).upper()
        rec["_attributi"]["custode_presente"] = any(
            (s.get("auctionSubjectType") or "").lower() == "custode" for s in (auction.get("auctionSubjects") or [])
        )

    _applica_comune(
        rec,
        comune_esplicito=raw.get("comune"),
        provincia_esplicita=raw.get("provincia"),
        indirizzo=raw.get("indirizzo"),
        titolo=raw.get("titolo"),
    )

    loc = p.get("location") or {}
    lat, lon = loc.get("latitude"), loc.get("longitude")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        rec["lat"], rec["lon"] = float(lat), float(lon)

    _applica_categoria(rec, titolo=raw.get("titolo"), tipologia=raw.get("tipologia"), descrizione=(raw.get("descrizione_completa") or "") + " " + (raw.get("categoria") or ""))
    _superficie_da_campo_o_testo(
        rec,
        valore_campo=raw.get("superficie_mq") or raw.get("surface"),
        testi=[raw.get("titolo"), raw.get("descrizione_completa")],
    )

    industrial = p.get("industrial") or {}
    altezza_grezza = industrial.get("underBeamHeight") or industrial.get("barnHeight")
    altezza_fonte_campo = "underBeamHeight" if industrial.get("underBeamHeight") else "barnHeight"
    altezza_valore = parser.norma_altezza(altezza_grezza)
    if altezza_valore is not None:
        if altezza_min <= altezza_valore <= altezza_max:
            rec["altezza_m"] = campo(altezza_valore, "raw_detail", "alta", campo_origine=altezza_fonte_campo)
        else:
            rec["altezza_m"] = campo(None, "raw_detail", "bassa", motivo=f"valore {altezza_valore} m fuori dal range plausibile ({altezza_min}-{altezza_max} m)")
            rec["_attributi"]["altezza_scartata_non_plausibile"] = altezza_valore
    else:
        rec["altezza_m"] = campo(None, "stima", "bassa")

    floor = p.get("floor") or {}
    rec["piano"] = util.pulisci(floor.get("value") or floor.get("floorOnlyValue"))
    rec["n_piani"] = util.pulisci(p.get("floors")) if p.get("floors") else None

    disponibilita = p.get("availability")
    if disponibilita:
        rec["stato_occupazione"] = util.pulisci(str(disponibilita))

    anno = p.get("buildingYear")
    if isinstance(anno, (int, float)) and 1800 <= anno <= 2100:
        rec["anno_costruzione"] = int(anno)

    diritto_testo = raw.get("descrizione_completa") or ""
    rec["diritto"] = testo.estrai_diritto(diritto_testo)

    rec["_attributi"].update(
        {
            "campate": industrial.get("bayes"),
            "banchine": industrial.get("numberLoadingDock"),
            "gru": industrial.get("numberCranes"),
            "features": p.get("features") or [],
            "surface_constitution": p.get("surfaceConstitution"),
            "condition": p.get("condition"),
            "cadastrals": p.get("cadastrals"),
        }
    )

    return rec


# --------------------------------------------------------------------------- astalegale / fonte generica sconosciuta


def adatta_generico(fonte_prefisso: str, id_chiave: str, raw: dict[str, Any], canale: str, file_origine: str) -> dict[str, Any]:
    """Ripiego per una fonte senza adattatore dedicato (es. astalegale, oggi
    sempre vuota, o una fonte nuova comparsa in INPUT/). Usa nomi di campo
    comuni a piu' scraper, e in caso di fallimento produce comunque un
    record con DATI_INSUFFICIENTI invece di saltare l'annuncio o crashare."""
    id_completo = _id_pulito(id_chiave)
    rec = _record_base(id_completo, f"{fonte_prefisso} (adattatore generico)", canale, file_origine, raw.get("url"))
    rec["tipo_vendita"] = "asta" if canale == CANALE_ASTE else "vendita"
    rec["titolo"] = util.pulisci(raw.get("titolo") or raw.get("title"))
    rec["descrizione"] = util.pulisci(raw.get("descrizione_completa") or raw.get("descrizione") or raw.get("description"))
    prezzo_grezzo = raw.get("prezzo") or raw.get("prezzo_base") or raw.get("price")
    rec["prezzo_eur"] = campo(parser.norma_prezzo(prezzo_grezzo), "json", "bassa")
    _applica_comune(
        rec,
        comune_esplicito=raw.get("comune"),
        indirizzo=raw.get("indirizzo"),
        titolo=rec["titolo"],
        city_fallback=raw.get("city") or raw.get("citta"),
    )
    _applica_categoria(rec, titolo=rec["titolo"], descrizione=rec["descrizione"])
    _superficie_da_campo_o_testo(
        rec,
        valore_campo=raw.get("superficie_mq"),
        testi=[rec["titolo"], rec["descrizione"]],
    )
    rec["_attributi"]["fonte_senza_adattatore_dedicato"] = True
    return rec


ADATTATORI = {
    "asteannunci": (adatta_asteannunci, CANALE_ASTE),
    "asteflorio": (adatta_asteflorio, CANALE_ASTE),
    "caseasta": (adatta_caseasta, CANALE_ASTE),
    "fallcoaste": (adatta_fallcoaste, CANALE_ASTE),
    "medianord": (adatta_medianord, CANALE_ASTE),
    "venditegiudiziarie": (adatta_vgi, CANALE_ASTE),
    "worldcapital": (adatta_worldcapital, CANALE_ASTE),
    "astalegale": (None, CANALE_ASTE),
    "idealista": (adatta_idealista, CANALE_IMMID),
    "immobiliare": (adatta_immobiliare, CANALE_IMMID),
}
