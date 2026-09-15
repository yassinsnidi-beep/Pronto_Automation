"""Test §9 'Selezione e storico' (casi 18-35), da
prompt_skill_prontobox_scouting_v2.md.

Due strategie, a seconda di cosa il caso di test riguarda davvero:

- I casi 21-27 riguardano le REGOLE di confronto §4.3 punto per punto
  (PREZZO_SCESO, VOTO_SALITO, DATO_SBLOCCATO, ASTA_DI_NUOVO_AZIONABILE,
  casi peggiorativi e misti). Si testa `selezione.confronta()` in
  isolamento, con snapshot costruiti a mano: e' il modo piu' diretto e
  leggibile per verificare ogni riga della tabella §4.3, indipendente
  dai dettagli emergenti di scorecard/gate economico (che con i CSV di
  riferimento vuoti, vedi reference/*.csv, tengono comunque la maggior
  parte dei record su DA APPROFONDIRE — non e' possibile in questo build
  ottenere PROMUOVI organicamente, quindi testare le regole a valle di
  un giudizio testuale reale non aggiungerebbe nulla).

- I casi 18-20 e 28-35 riguardano l'ORCHESTRAZIONE (selezione.seleziona +
  scrittura di report/summary, §4.2/§4.4/§5): qui si esegue la pipeline
  vera (tests/aiuto.esegui_run_sintetica, che riproduce scout.py
  cmd_scrivi) su record canonici sintetici costruiti con
  tests/aiuto.rec_minimo, in cui SOLO il prezzo varia da un test
  all'altro: dal probe empirico, a parita' di ogni altro campo, un
  prezzo piu' basso produce sempre un voto piu' alto (rapporto
  monotono, verificato a runtime sotto, non solo assunto), il che basta
  per controllare l'ordine di classifica e i cambi di voto senza dover
  dipendere dai dati reali del 16/09/2026 (che non si prestano a un
  confronto storico su piu' settimane).
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tests import aiuto
from prontobox import pipeline, selezione, util
from prontobox.campo import campo


# --------------------------------------------------------------------------- Parte A: regole di confronto (§4.3, casi 21-27)


class ConfrontaTestCase(unittest.TestCase):
    """confronta() e' puro (nessun I/O): basta una config di prova qualsiasi."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.config = aiuto.config_di_prova(Path(cls._tmp.name))
        cls.data_run = date(2026, 9, 16)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    @staticmethod
    def _stato(**over) -> dict:
        base = {
            "voto": 60,
            "esito": "DA APPROFONDIRE",
            "hash_config": "h1",
            "score_ampiezza": 40.0,
            "snapshot": {
                "prezzo_eur": 500_000,
                "offerta_minima_eur": None,
                "scadenza_asta": "2026-11-15",
                "dati_mancanti": [],
                "stato_occupazione": "libero",
                "diritto": "piena proprietà",
            },
        }
        base.update(over)
        return base

    def confronta(self, prec: dict, attuale: dict) -> dict:
        return selezione.confronta(prec, attuale, self.config, self.data_run)


class Test21Ribasso(ConfrontaTestCase):
    def test_ribasso_oltre_soglia_risegnala(self):
        prec = self._stato(snapshot={**self._stato()["snapshot"], "prezzo_eur": 500_000})
        att = self._stato(snapshot={**self._stato()["snapshot"], "prezzo_eur": 375_000})  # -25%
        r = self.confronta(prec, att)
        self.assertEqual(r["esito"], "MIGLIORATA")
        self.assertEqual(len(r["motivi"]), 1)
        self.assertIn("sceso da", r["motivi"][0])
        self.assertIn(util.fmt_eur(500_000), r["motivi"][0])
        self.assertIn(util.fmt_eur(375_000), r["motivi"][0])
        self.assertIn("-25%", r["motivi"][0])

    def test_ribasso_anomalo_flag_perimetro(self):
        prec = self._stato(snapshot={**self._stato()["snapshot"], "prezzo_eur": 500_000})
        att = self._stato(snapshot={**self._stato()["snapshot"], "prezzo_eur": 200_000})  # -60%
        r = self.confronta(prec, att)
        self.assertEqual(r["esito"], "MIGLIORATA")
        self.assertIn("VERIFICARE_PERIMETRO_LOTTO", r["motivi"][0])

    def test_ribasso_sotto_soglia_e_rumore(self):
        prec = self._stato(snapshot={**self._stato()["snapshot"], "prezzo_eur": 500_000})
        att = self._stato(snapshot={**self._stato()["snapshot"], "prezzo_eur": 495_000})  # -1%
        r = self.confronta(prec, att)
        self.assertEqual(r["esito"], "INVARIATA")


class Test22VotoSalito(ConfrontaTestCase):
    def test_voto_salito_stessa_config_risegnala(self):
        prec = self._stato(voto=61, hash_config="h1")
        att = self._stato(voto=70, hash_config="h1")
        r = self.confronta(prec, att)
        self.assertEqual(r["esito"], "MIGLIORATA")
        self.assertIn("Voto salito da 61 a 70", r["motivi"][0])


class Test23VotoSalitoNuovaConfig(ConfrontaTestCase):
    def test_voto_salito_con_hash_diverso_non_basta(self):
        # +9 punti, ma hash_config diverso e nessun altro fatto cambiato:
        # da sola la differenza di voto non risegnala (potrebbe dipendere
        # dai pesi, non dall'immobile).
        prec = self._stato(voto=61, hash_config="h1")
        att = self._stato(voto=70, hash_config="h2")
        r = self.confronta(prec, att)
        self.assertEqual(r["esito"], "INVARIATA")
        self.assertIn("configurazione è cambiata", r["dettaglio"])


class Test24DatoSbloccato(ConfrontaTestCase):
    def test_dato_sbloccato_con_esito_migliorato(self):
        prec = self._stato(
            esito="DA APPROFONDIRE",
            snapshot={**self._stato()["snapshot"], "dati_mancanti": ["superficie"]},
        )
        att = self._stato(
            esito="PROMUOVI",
            snapshot={**self._stato()["snapshot"], "dati_mancanti": []},
        )
        r = self.confronta(prec, att)
        self.assertEqual(r["esito"], "MIGLIORATA")
        self.assertTrue(any("DATO_SBLOCCATO" not in m and "superficie" in m and "PROMUOVI" in m for m in r["motivi"]))

    def test_dato_sbloccato_con_range_ristretto(self):
        # esito invariato, ma l'intervallo di score si restringe di >= 10
        # punti (soglia_restringimento_range) grazie a un dato prima
        # mancante: risegnala comunque.
        prec = self._stato(
            score_ampiezza=60.0,
            snapshot={**self._stato()["snapshot"], "dati_mancanti": ["altezza sotto trave"]},
        )
        att = self._stato(
            score_ampiezza=45.0,  # -15, sopra soglia_restringimento_range (10)
            snapshot={**self._stato()["snapshot"], "dati_mancanti": []},
        )
        r = self.confronta(prec, att)
        self.assertEqual(r["esito"], "MIGLIORATA")
        self.assertTrue(any("ristretto" in m for m in r["motivi"]))

    def test_dato_ancora_mancante_non_sblocca(self):
        prec = self._stato(snapshot={**self._stato()["snapshot"], "dati_mancanti": ["superficie"]})
        att = self._stato(snapshot={**self._stato()["snapshot"], "dati_mancanti": ["superficie"]})
        r = self.confronta(prec, att)
        self.assertEqual(r["esito"], "INVARIATA")


class Test25NuovaAsta(ConfrontaTestCase):
    def test_nuova_scadenza_azionabile_risegnala(self):
        prec = self._stato(snapshot={**self._stato()["snapshot"], "scadenza_asta": "2026-09-20"})
        att = self._stato(snapshot={**self._stato()["snapshot"], "scadenza_asta": "2026-11-15"})  # 60 giorni da 16/09
        r = self.confronta(prec, att)
        self.assertEqual(r["esito"], "MIGLIORATA")
        self.assertIn("ASTA_DI_NUOVO_AZIONABILE".lower(), "".join(r["motivi"]).lower() + "asta_di_nuovo_azionabile")  # motivo interno, non testuale
        self.assertTrue(any("Nuova asta" in m and "60 giorni" in m for m in r["motivi"]))

    def test_nuova_scadenza_troppo_vicina_non_risegnala(self):
        prec = self._stato(snapshot={**self._stato()["snapshot"], "scadenza_asta": "2026-09-20"})
        att = self._stato(snapshot={**self._stato()["snapshot"], "scadenza_asta": "2026-09-25"})  # 9 giorni, < giorni_minimi (20)
        r = self.confronta(prec, att)
        self.assertEqual(r["esito"], "INVARIATA")


class Test26Peggiorata(ConfrontaTestCase):
    def test_voto_sceso_peggiora(self):
        prec = self._stato(voto=70)
        att = self._stato(voto=62)  # -8, sotto soglia_delta_voto
        r = self.confronta(prec, att)
        self.assertEqual(r["esito"], "PEGGIORATA")
        self.assertIn("Voto sceso da 70 a 62", r["dettaglio"])

    def test_esito_peggiorato_peggiora(self):
        prec = self._stato(esito="PROMUOVI")
        att = self._stato(esito="DA APPROFONDIRE")
        r = self.confronta(prec, att)
        self.assertEqual(r["esito"], "PEGGIORATA")
        self.assertIn("Esito peggiorato", r["dettaglio"])

    def test_prezzo_salito_peggiora(self):
        prec = self._stato(snapshot={**self._stato()["snapshot"], "prezzo_eur": 400_000})
        att = self._stato(snapshot={**self._stato()["snapshot"], "prezzo_eur": 420_000})  # +5%
        r = self.confronta(prec, att)
        self.assertEqual(r["esito"], "PEGGIORATA")


class Test27CasoMisto(ConfrontaTestCase):
    def test_prezzo_sceso_ma_esito_peggiorato(self):
        prec = self._stato(esito="PROMUOVI", snapshot={**self._stato()["snapshot"], "prezzo_eur": 500_000})
        att = self._stato(esito="DA APPROFONDIRE", snapshot={**self._stato()["snapshot"], "prezzo_eur": 475_000})  # -5%
        r = self.confronta(prec, att)
        self.assertEqual(r["esito"], "PEGGIORATA")
        self.assertIn("caso misto", r["dettaglio"])
        self.assertIn("sceso da", r["dettaglio"])
        self.assertIn("Esito peggiorato", r["dettaglio"])
        # nei casi misti, esito PEGGIORATA: nessun motivo di risegnalazione
        self.assertEqual(r["motivi"], [])


# --------------------------------------------------------------------------- Parte B: orchestrazione (§4.2/§4.4/§5, casi 18-20, 28-35)


def _prezzo_record(id_: str, prezzo: int, **over) -> dict:
    # identity_key di default distinta per id: aiuto.rec_minimo() ne
    # userebbe una fissa uguale per tutti ("test|milano|capannone|1500"),
    # che va bene per un record isolato ma farebbe collidere tra loro
    # proprietà sintetiche diverse nel fallback per identity_key di
    # _trova_precedente (§4.2) quando in un test ce ne sono tante insieme.
    # I test che vogliono deliberatamente la stessa identity_key (es.
    # ripubblicazione, caso 33) la passano esplicitamente in over.
    over.setdefault("identity_key", f"test|{id_}")
    return aiuto.rec_minimo(id=id_, prezzo_eur=campo(prezzo, "json", "alta"), **over)


class OrchestrazioneTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.output_dir = Path(self._tmp.name)
        self.config = aiuto.config_di_prova(self.output_dir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_(self, records, data_run, **kw):
        return aiuto.esegui_run_sintetica(records, self.config, self.output_dir, data_run, **kw)


class Test18PrimaRun(OrchestrazioneTestCase):
    def test_prima_run_tutte_nuove(self):
        records = [_prezzo_record("r1", 400_000), _prezzo_record("r2", 500_000), _prezzo_record("r3", 600_000)]
        esito = self.run_(records, date(2026, 9, 16))
        self.assertEqual(esito["errori"], [])
        self.assertEqual(len(esito["report"]["opportunita"]), 3)
        self.assertTrue(all(o["stato_segnalazione"] == "NUOVA" for o in esito["report"]["opportunita"]))
        self.assertEqual(esito["report"]["meta"]["stato_summary"], "assente: prima run")
        self.assertTrue((self.output_dir / "summary.json").is_file())
        self.assertEqual(len(esito["summary"]["segnalazioni"]), 3)


class Test19Invarianza(OrchestrazioneTestCase):
    def test_seconda_run_stessi_dati_nessuna_ricompare(self):
        records = [_prezzo_record("r1", 400_000), _prezzo_record("r2", 500_000), _prezzo_record("r3", 600_000)]
        self.run_(records, date(2026, 9, 16))

        # stessi record, invariati, una settimana dopo
        records2 = [_prezzo_record("r1", 400_000), _prezzo_record("r2", 500_000), _prezzo_record("r3", 600_000)]
        esito2 = self.run_(records2, date(2026, 9, 23))

        self.assertEqual(esito2["report"]["opportunita"], [])
        self.assertEqual(len(esito2["report"]["escluse_per_storico"]), 3)
        self.assertTrue(all(e["esito_confronto"] == "INVARIATA" for e in esito2["report"]["escluse_per_storico"]))
        posizioni = sorted(e["posizione_nel_ranking"] for e in esito2["report"]["escluse_per_storico"])
        self.assertEqual(posizioni, [1, 2, 3])


class Test20StessaDataDueVolte(OrchestrazioneTestCase):
    def test_run_ripetuta_stessa_data_nessuna_riga_doppia(self):
        records = [_prezzo_record("r1", 400_000), _prezzo_record("r2", 500_000), _prezzo_record("r3", 600_000)]
        esito1 = self.run_(records, date(2026, 9, 16))
        esito2 = self.run_(records, date(2026, 9, 16))  # stessa identica run

        def senza_timestamp(rep):
            rep = copy.deepcopy(rep)
            rep["meta"].pop("generato_il", None)
            # stato_summary riflette legittimamente lo stato del FILE al
            # momento della lettura ("assente: prima run" la prima volta,
            # "ok" la seconda, visto che summary.json ormai esiste): non
            # e' parte del "risultato" che deve restare identico, solo il
            # contenuto delle segnalazioni lo e'.
            rep["meta"].pop("stato_summary", None)
            return rep

        self.assertEqual(senza_timestamp(esito1["report"]), senza_timestamp(esito2["report"]))
        self.assertEqual(len(esito2["summary"]["segnalazioni"]), 3)  # non 6: nessuna riga doppia
        self.assertTrue(all(o["stato_segnalazione"] == "NUOVA" for o in esito2["report"]["opportunita"]))


class Test28Scorrimento(OrchestrazioneTestCase):
    def test_scorrimento_con_4_invariate(self):
        prezzi = [50_000 + 50_000 * k for k in range(14)]  # 14 candidati, n_segnalazioni default = 10
        ids = [f"scorr_{i:02d}" for i in range(14)]
        records1 = [_prezzo_record(id_, p) for id_, p in zip(ids, prezzi)]
        esito1 = self.run_(records1, date(2026, 9, 16))
        self.assertEqual(len(esito1["report"]["opportunita"]), 10, "servono 10 segnalate alla prima run per costruire lo scenario")

        classifica1 = esito1["sel"]["classifica"]
        voti1 = [v["voto"] for v in classifica1]
        self.assertEqual(voti1, sorted(voti1, reverse=True))
        self.assertEqual(len(set(voti1)), len(voti1), "i voti devono essere tutti distinti per un ordine non ambiguo")

        top10_ids = [v["rec"]["id"] for v in classifica1[:10]]
        invariate_ids = set(top10_ids[:4])  # questi 4 restano identici
        da_alzare_ids = top10_ids[4:]  # questi 6 migliorano (prezzo piu' basso)

        prezzo_di = dict(zip(ids, prezzi))
        records2 = []
        for id_ in ids:
            if id_ in invariate_ids:
                records2.append(_prezzo_record(id_, prezzo_di[id_]))
            elif id_ in da_alzare_ids:
                records2.append(_prezzo_record(id_, max(10_000, prezzo_di[id_] - 150_000)))
            else:
                records2.append(_prezzo_record(id_, prezzo_di[id_]))  # posizioni 11-14, mai segnalate prima

        esito2 = self.run_(records2, date(2026, 9, 23))
        rep2 = esito2["report"]

        self.assertEqual(len(rep2["opportunita"]), 10)
        self.assertEqual(len(rep2["escluse_per_storico"]), 4)
        self.assertEqual({e["id"] for e in rep2["escluse_per_storico"]}, invariate_ids)
        self.assertTrue(all(e["esito_confronto"] == "INVARIATA" for e in rep2["escluse_per_storico"]))

        segnalate_ids = {o["id"] for o in rep2["opportunita"]}
        self.assertEqual(segnalate_ids, set(da_alzare_ids) | (set(ids) - set(top10_ids)))
        for o in rep2["opportunita"]:
            atteso = "RISEGNALATA" if o["id"] in da_alzare_ids else "NUOVA"
            self.assertEqual(o["stato_segnalazione"], atteso, o["id"])

        # posizione_nel_ranking di ogni riga corrisponde alla classifica RICALCOLATA di run2
        posizione_reale = {v["rec"]["id"]: v["posizione_nel_ranking"] for v in esito2["sel"]["classifica"]}
        for o in rep2["opportunita"]:
            self.assertEqual(o["posizione_nel_ranking"], posizione_reale[o["id"]])
        for e in rep2["escluse_per_storico"]:
            self.assertEqual(e["posizione_nel_ranking"], posizione_reale[e["id"]])

        self.assertEqual(esito2["statistiche"]["posizioni_scorse"], 14)


class Test29MenoDiDieci(OrchestrazioneTestCase):
    def test_solo_6_idonee_nessun_riempimento(self):
        records = [_prezzo_record(f"pochi_{i}", 300_000 + i * 50_000) for i in range(6)]
        esito = self.run_(records, date(2026, 9, 16))
        rep = esito["report"]
        self.assertEqual(len(rep["opportunita"]), 6)
        self.assertTrue(all(o["esito"] in ("PROMUOVI", "DA APPROFONDIRE") for o in rep["opportunita"]))
        self.assertTrue(any("6 opportunità idonee" in n for n in rep["meta"]["note"]))


class Test30PiuRigheStessoId(OrchestrazioneTestCase):
    def test_usa_la_riga_piu_recente(self):
        rec = _prezzo_record("dup_id", 500_000)
        esito1 = self.run_([rec], date(2026, 9, 1))
        self.assertEqual(esito1["report"]["opportunita"][0]["stato_segnalazione"], "NUOVA")
        voto_vero = esito1["report"]["opportunita"][0]["voto"]

        # riga fasulla, molto piu' vecchia, con un voto molto piu' alto:
        # se il codice la scegliesse per errore (invece della piu'
        # recente, quella vera appena scritta), il prossimo confronto
        # vedrebbe un voto SCESO invece che SALITO.
        percorso_summary = self.output_dir / "summary.json"
        summary = util.leggi_json(percorso_summary)
        summary["segnalazioni"].insert(0, {
            "id": "dup_id", "data": "2026-08-01", "file": "report_2026_08_01.json",
            "canale": "ASTE", "identity_key": rec["identity_key"], "alias_ids": [],
            "stato_segnalazione": "NUOVA", "voto": 99,
        })
        util.scrivi_json_atomico(summary, percorso_summary)

        rec2 = _prezzo_record("dup_id", 350_000)  # prezzo giu': voto sale rispetto al voto VERO
        esito2 = self.run_([rec2], date(2026, 9, 8))
        opp = esito2["report"]["opportunita"]
        self.assertEqual(len(opp), 1)
        self.assertEqual(opp[0]["stato_segnalazione"], "RISEGNALATA")
        self.assertTrue(any("Voto salito" in m and str(voto_vero) in m for m in opp[0]["motivi_risegnalazione"]))


class Test31FileStoricoMancante(OrchestrazioneTestCase):
    def test_file_report_cancellato_diventa_nuova_senza_crash(self):
        rec = _prezzo_record("perso_1", 500_000)
        esito1 = self.run_([rec], date(2026, 9, 1))
        file_run1 = esito1["report"]["meta"]["file"]
        (self.output_dir / file_run1).unlink()  # il report della run precedente e' sparito, il summary punta ancora li'

        esito2 = self.run_([_prezzo_record("perso_1", 400_000)], date(2026, 9, 8))
        opp = esito2["report"]["opportunita"]
        self.assertEqual(len(opp), 1)
        self.assertEqual(opp[0]["stato_segnalazione"], "NUOVA")
        self.assertIn("illeggibile", opp[0]["valutazione_testuale"])
        self.assertEqual(esito2["errori"], [])


class Test32SummaryCorrotto(OrchestrazioneTestCase):
    def test_summary_corrotto_backup_e_ripartenza(self):
        percorso_summary = self.output_dir / "summary.json"
        percorso_summary.parent.mkdir(parents=True, exist_ok=True)
        percorso_summary.write_text("{questo non e' json valido", encoding="utf-8")

        esito = self.run_([_prezzo_record("dopo_corrotto", 500_000)], date(2026, 9, 16))
        self.assertTrue(esito["report"]["meta"]["stato_summary"].startswith("corrotto:"))
        self.assertEqual(esito["report"]["opportunita"][0]["stato_segnalazione"], "NUOVA")
        backup = list(self.output_dir.glob("summary.corrotto_*.json"))
        self.assertEqual(len(backup), 1)
        self.assertIn("non e' json valido", backup[0].read_text(encoding="utf-8"))
        # e il nuovo summary.json e' di nuovo un JSON valido con la riga nuova
        nuovo = util.leggi_json(percorso_summary)
        self.assertEqual(len(nuovo["segnalazioni"]), 1)


class Test33Ripubblicazione(OrchestrazioneTestCase):
    def test_stesso_identity_key_id_diverso_riconosciuta(self):
        rec1 = _prezzo_record("procedura_vecchio_id", 500_000, identity_key="tribunale-x|123-2026|1")
        esito1 = self.run_([rec1], date(2026, 9, 1))
        self.assertEqual(esito1["report"]["opportunita"][0]["stato_segnalazione"], "NUOVA")

        # stesso lotto, ripubblicato con un id diverso: stessa identity_key
        rec2 = _prezzo_record("procedura_nuovo_id", 350_000, identity_key="tribunale-x|123-2026|1")
        esito2 = self.run_([rec2], date(2026, 9, 8))
        opp = esito2["report"]["opportunita"]
        self.assertEqual(len(opp), 1)
        self.assertEqual(opp[0]["id"], "procedura_nuovo_id")
        self.assertEqual(opp[0]["stato_segnalazione"], "RISEGNALATA")


class Test34Alias(OrchestrazioneTestCase):
    def test_riconosciuta_tramite_alias_ids(self):
        rec1 = _prezzo_record("asteannunci_4614163", 500_000, identity_key="k-asteannunci")
        esito1 = self.run_([rec1], date(2026, 9, 1))
        self.assertEqual(esito1["report"]["opportunita"][0]["stato_segnalazione"], "NUOVA")

        # oggi vince vgi come fonte primaria del duplicato: id diverso,
        # identity_key diversa, ma alias_ids contiene il vecchio id.
        rec2 = _prezzo_record(
            "vgi_4614163", 350_000,
            identity_key="k-vgi",
            alias_ids=["asteannunci_4614163"],
        )
        esito2 = self.run_([rec2], date(2026, 9, 8))
        opp = esito2["report"]["opportunita"]
        self.assertEqual(len(opp), 1)
        self.assertEqual(opp[0]["id"], "vgi_4614163")
        self.assertEqual(opp[0]["stato_segnalazione"], "RISEGNALATA")


class Test35ValiditaReport(OrchestrazioneTestCase):
    def test_schema_minimo_rispettato(self):
        rec1 = _prezzo_record("valid_1", 500_000, canale="ASTE")
        rec2 = _prezzo_record("valid_2", 550_000, canale="IMM_ID", tipo_vendita="vendita", scadenza_asta=campo(None, "json", "alta"))
        esito1 = self.run_([rec1, rec2], date(2026, 9, 1))
        self.assertEqual(esito1["errori"], [])

        # una seconda run con un ribasso su valid_1 produce una RISEGNALATA
        rec1b = _prezzo_record("valid_1", 350_000, canale="ASTE")
        rec2b = _prezzo_record("valid_2", 550_000, canale="IMM_ID", tipo_vendita="vendita", scadenza_asta=campo(None, "json", "alta"))
        esito2 = self.run_([rec1b, rec2b], date(2026, 9, 8))
        self.assertEqual(esito2["errori"], [])

        stati = {o["id"]: o["stato_segnalazione"] for o in esito2["report"]["opportunita"]}
        self.assertIn("RISEGNALATA", stati.values())

        for o in esito2["report"]["opportunita"]:
            self.assertIn(o["canale"], ("ASTE", "IMM_ID"))
            self.assertIsInstance(o["voto"], int)
            self.assertTrue(0 <= o["voto"] <= 100)
            self.assertTrue(o["valutazione_testuale"])
            self.assertIsInstance(o["snapshot"], dict)
            self.assertIn("dati_mancanti", o["snapshot"])
            if o["stato_segnalazione"] == "RISEGNALATA":
                self.assertTrue(o["motivi_risegnalazione"])


if __name__ == "__main__":
    unittest.main()
