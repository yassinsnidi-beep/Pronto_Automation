"""Identita' e dedup tra fonti diverse (§3.0.8).

Regola cardine, esplicita nella spec: "Mai unire in silenzio due lotti con
procedura uguale ma lotto diverso." L'implementazione qui sotto tratta
questo come un vincolo bloccante: se il lotto e' noto su entrambi i record e
diverso, non c'e' unione, punto, anche se tutto il resto combacia.
"""

from __future__ import annotations

from typing import Any, Optional

from . import util
from .campo import valore_di
from .geografia import _haversine_km


def _prefisso_id(rec: dict[str, Any]) -> str:
    return str(rec.get("id", "")).split("_", 1)[0]


def costruisci_identity_key(rec: dict[str, Any]) -> str:
    """Chiave stabile per il confronto con lo storico (§4.2), indipendente
    dall'ID della singola fonte. Non e' un identificatore perfetto (non
    esiste, con questi dati): e' il miglior compromesso deterministico."""
    tribunale = util.norm_chiave(rec.get("tribunale"))
    procedura = util.norm_chiave(rec.get("procedura"))
    lotto = util.norm_chiave(rec.get("lotto"))
    if tribunale and procedura:
        base = f"asta|{tribunale}|{procedura}"
        if lotto:
            base += f"|{lotto}"
        return base
    comune = util.norm_chiave(valore_di(rec.get("comune"))) or "comune-ignoto"
    categoria = valore_di(rec.get("categoria")) or "altro"
    superficie = valore_di(rec.get("superficie_mq"))
    bucket = "superficie-ignota" if superficie is None else str(int(round(superficie / 50.0)) * 50)
    return f"vendita|{comune}|{categoria}|{bucket}"


# --------------------------------------------------------------------------- regole di corrispondenza (§3.0.8)


def _stesso_id_rete(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Regola 1: vgi_ e asteannunci_ condividono l'ID numerico (stessa rete
    astegiudiziarie)."""
    rete = {"vgi", "asteannunci"}
    pa, pb = _prefisso_id(a), _prefisso_id(b)
    if pa not in rete or pb not in rete or pa == pb:
        return False
    ida = str(a.get("id", "")).split("_", 1)[-1]
    idb = str(b.get("id", "")).split("_", 1)[-1]
    return bool(ida) and ida == idb


def _stesso_lotto_asta(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Regola 2: stesso tribunale|procedura|lotto. Se il lotto non e'
    ricavabile su almeno uno dei due, richiede in aggiunta stesso comune e
    superficie entro ±5%. Blocca sempre se il lotto e' noto su entrambi ed e'
    diverso (test §9.9: i 5 lotti di Ancona 60/2024 restano separati)."""
    trib_a, trib_b = util.norm_chiave(a.get("tribunale")), util.norm_chiave(b.get("tribunale"))
    proc_a, proc_b = util.norm_chiave(a.get("procedura")), util.norm_chiave(b.get("procedura"))
    if not trib_a or not trib_b or not proc_a or not proc_b:
        return False
    if trib_a != trib_b or proc_a != proc_b:
        return False

    comune_a, comune_b = valore_di(a.get("comune")), valore_di(b.get("comune"))
    comuni_noti_e_diversi = bool(comune_a) and bool(comune_b) and util.norm_chiave(comune_a) != util.norm_chiave(comune_b)

    lotto_a, lotto_b = a.get("lotto"), b.get("lotto")
    if lotto_a and lotto_b:
        # Anche a lotto uguale, un comune diverso e noto su entrambi i lati
        # e' una contraddizione piu' forte del numero di lotto: la
        # numerazione dei lotti non e' sempre univoca dentro una procedura
        # con piu' beni (visto nei dati reali fallcoaste, procedura
        # 667/2003: "Lotto n. 1" compare sia a Maiolati Spontini sia ad
        # Arcevia). Meglio non unire che unire in silenzio due beni diversi.
        if comuni_noti_e_diversi:
            return False
        return util.norm_chiave(lotto_a) == util.norm_chiave(lotto_b)

    if not comune_a or not comune_b or comuni_noti_e_diversi:
        return False
    sup_a, sup_b = valore_di(a.get("superficie_mq")), valore_di(b.get("superficie_mq"))
    if sup_a and sup_b and max(sup_a, sup_b) > 0:
        return abs(sup_a - sup_b) / max(sup_a, sup_b) <= 0.05
    return False


def _stesse_coordinate(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Regola 3: solo per record con coordinate, distanza < 100 m e stessa
    categoria.

    RAFFINAMENTO rispetto al testo letterale della spec (documentato anche
    nel README): sui dati reali, un palazzo puo' contenere piu' unita' in
    vendita separate (visto in Via Cicco Simonetta, Milano: 4 depositi da
    23-27 m² ciascuno, stesse coordinate). Applicare la regola alla lettera
    le fonderebbe tutte in una sola, perdendo 3 opportunita' vere. Si
    aggiunge quindi il vincolo che, quando la superficie e' nota su
    entrambi i lati, deve coincidere entro ±3% (come la regola 4): due unita'
    diverse nello stesso edificio quasi mai hanno la stessa superficie esatta.
    Se la superficie manca su un lato o su entrambi, si mantiene il
    comportamento della sola distanza (non c'e' modo di essere piu' cauti
    con questi dati)."""
    if a.get("lat") is None or a.get("lon") is None or b.get("lat") is None or b.get("lon") is None:
        return False
    cat_a, cat_b = valore_di(a.get("categoria")), valore_di(b.get("categoria"))
    if not cat_a or cat_a != cat_b:
        return False
    distanza_km = _haversine_km((a["lat"], a["lon"]), (b["lat"], b["lon"]))
    if distanza_km * 1000 >= 100:
        return False
    sup_a, sup_b = valore_di(a.get("superficie_mq")), valore_di(b.get("superficie_mq"))
    if sup_a and sup_b:
        return abs(sup_a - sup_b) / max(sup_a, sup_b) <= 0.03
    return True


def _match_idealista_immobiliare(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Regola 4: stesso comune + superficie ±3% + prezzo ±2% -> unione con
    confidenza media."""
    prefissi = {_prefisso_id(a), _prefisso_id(b)}
    if prefissi != {"idealista", "immobiliare"}:
        return False
    comune_a, comune_b = valore_di(a.get("comune")), valore_di(b.get("comune"))
    if not comune_a or not comune_b or util.norm_chiave(comune_a) != util.norm_chiave(comune_b):
        return False
    sup_a, sup_b = valore_di(a.get("superficie_mq")), valore_di(b.get("superficie_mq"))
    prezzo_a, prezzo_b = valore_di(a.get("prezzo_eur")), valore_di(b.get("prezzo_eur"))
    if not (sup_a and sup_b and prezzo_a and prezzo_b):
        return False
    entro_sup = abs(sup_a - sup_b) / max(sup_a, sup_b) <= 0.03
    entro_prezzo = abs(prezzo_a - prezzo_b) / max(prezzo_a, prezzo_b) <= 0.02
    return entro_sup and entro_prezzo


def _corrisponde(a: dict[str, Any], b: dict[str, Any]) -> Optional[str]:
    """Ritorna il motivo dell'unione se una delle regole scatta, altrimenti
    None. L'ordine riflette quello della spec."""
    if _stesso_id_rete(a, b):
        return "stesso ID nella rete astegiudiziarie (vgi/asteannunci)"
    if _stesso_lotto_asta(a, b):
        return "stesso tribunale/procedura/lotto (o comune+superficie quando il lotto manca)"
    if _stesse_coordinate(a, b):
        return "coordinate a meno di 100 m e stessa categoria"
    if _match_idealista_immobiliare(a, b):
        return "idealista/immobiliare: stesso comune, superficie ±3%, prezzo ±2%"
    return None


# --------------------------------------------------------------------------- unione (union-find) e merge


def _ricchezza(rec: dict[str, Any]) -> tuple[int, str]:
    """Quanto e' 'ricco di dati' un record: conta i campi noti, i piu' utili
    prima. Il secondo elemento (id) rende l'ordinamento stabile e
    riproducibile a parita' di ricchezza."""
    punti = 0
    for chiave in (
        "prezzo_eur", "offerta_minima_eur", "superficie_mq", "comune",
        "provincia", "scadenza_asta", "altezza_m", "diritto", "categoria",
    ):
        if valore_di(rec.get(chiave)) is not None:
            punti += 1
    if rec.get("tribunale"):
        punti += 1
    if rec.get("procedura"):
        punti += 1
    if rec.get("lat") is not None:
        punti += 2
    descrizione = rec.get("descrizione") or ""
    if len(descrizione) > 200:
        punti += 2
    elif len(descrizione) > 50:
        punti += 1
    return punti, rec.get("id", "")


def deduplica(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    """Applica le 4 regole di corrispondenza (§3.0.8), fonde i gruppi
    trovati (vince il record piu' ricco di dati) e ritorna
    (records_risultanti, n_duplicati_uniti, log_unioni)."""
    n = len(records)
    padre = list(range(n))

    def trova(x: int) -> int:
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    def unisci(x: int, y: int) -> None:
        rx, ry = trova(x), trova(y)
        if rx != ry:
            padre[max(rx, ry)] = min(rx, ry)

    log_unioni: list[dict[str, Any]] = []
    for i in range(n):
        for j in range(i + 1, n):
            motivo = _corrisponde(records[i], records[j])
            if motivo:
                unisci(i, j)
                log_unioni.append({"a": records[i]["id"], "b": records[j]["id"], "motivo": motivo})

    gruppi: dict[int, list[int]] = {}
    for i in range(n):
        gruppi.setdefault(trova(i), []).append(i)

    risultato: list[dict[str, Any]] = []
    n_duplicati = 0
    for indici in gruppi.values():
        membri = sorted((records[i] for i in indici), key=_ricchezza, reverse=True)
        vincitore = dict(membri[0])
        altri = membri[1:]
        if altri:
            n_duplicati += len(altri)
            alias = list(vincitore.get("alias_ids") or [])
            urls_altre = list(vincitore.get("urls_altre_fonti") or [])
            for altro in altri:
                alias.append(altro["id"])
                alias.extend(altro.get("alias_ids") or [])
                if altro.get("url"):
                    urls_altre.append(altro["url"])
                urls_altre.extend(altro.get("urls_altre_fonti") or [])
            # dedup preservando l'ordine
            vincitore["alias_ids"] = list(dict.fromkeys(a for a in alias if a and a != vincitore["id"]))
            vincitore["urls_altre_fonti"] = list(
                dict.fromkeys(u for u in urls_altre if u and u != vincitore.get("url"))
            )
            confidenze = {
                "idealista", "immobiliare",
            }
            fonti_gruppo = {_prefisso_id(m) for m in membri}
            vincitore.setdefault("_attributi", {})
            if fonti_gruppo == confidenze:
                vincitore["_attributi"]["dedup_confidenza"] = "media"
            else:
                vincitore["_attributi"]["dedup_confidenza"] = "alta"
            vincitore["_attributi"]["dedup_membri"] = [m["id"] for m in membri]
        vincitore["identity_key"] = costruisci_identity_key(vincitore)
        risultato.append(vincitore)

    return risultato, n_duplicati, log_unioni
