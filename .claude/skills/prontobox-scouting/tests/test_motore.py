"""Test §9 'Normalizzazione e valutazione' (casi 1-17), da
prompt_skill_prontobox_scouting_v2.md.

Le fixture in fixtures/INPUT/ sono estratti REALI di opportunities/INPUT/
del 16/09/2026 (vedi tests/aiuto.py e lo script che le ha generate). Quando
l'ID citato nella spec non esiste piu' uguale nei dati reali, il commento
del test lo dichiara e indica il sostituto scelto con le stesse
caratteristiche (stessa fascia di superficie, stesso difetto, ecc.), come
richiesto dalle istruzioni di build.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from tests import aiuto
from prontobox import filtri, parser, pipeline
from prontobox.campo import valore_di


class PipelineFixtureTestCase(unittest.TestCase):
    """Carica una volta sola tutte le fixture e applica Stadio 0 + Stadio 1
    (basta per la maggior parte dei casi 1-17, che riguardano adattatori e
    hard filter, non scorecard/economico)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        output_dir = Path(cls._tmp.name)
        cls.config = aiuto.config_di_prova(output_dir)
        cls.root = aiuto.root_fittizio()
        preparato = pipeline.carica_e_prepara(cls.config, cls.root, aiuto.DATA_RUN_TEST)
        cls.valutazioni = {v["rec"]["id"]: v for v in preparato["valutazioni"]}
        cls.record_letti = preparato["record_letti"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def v(self, id_):
        self.assertIn(id_, self.valutazioni, f"fixture mancante: {id_}")
        return self.valutazioni[id_]


class Test01FuoriPerimetro(PipelineFixtureTestCase):
    def test_asteflorio_roma_scarta_perimetro(self):
        v = self.v("asteflorio_84")
        self.assertEqual(v["stadio1"]["esito"], "SCARTA")
        self.assertEqual(v["stadio1"]["motivo"], "PERIMETRO_GEOGRAFICO")


class Test02Garage(PipelineFixtureTestCase):
    def test_idealista_garage_scarta_tipologia(self):
        v = self.v("idealista_36837357")
        self.assertEqual(valore_di(v["rec"]["categoria"]), "garage")
        self.assertEqual(v["stadio1"]["esito"], "SCARTA")
        self.assertEqual(v["stadio1"]["motivo"], "TIPOLOGIA")


class Test03BeniMobili(PipelineFixtureTestCase):
    def test_vgi_mobili_scarta_tipologia(self):
        v = self.v("vgi_4626228")
        self.assertEqual(valore_di(v["rec"]["categoria"]), "mobili")
        self.assertEqual(v["stadio1"]["esito"], "SCARTA")
        self.assertEqual(v["stadio1"]["motivo"], "TIPOLOGIA")


class Test04QuotaIndivisa(PipelineFixtureTestCase):
    def test_fallcoaste_diritto_estratto_quota_indivisa(self):
        # fallcoaste_1629594 ha oggi (16/09/2026) scadenza asta il
        # 22/09/2026, a soli 6 giorni dalla run: il filtro 4
        # (TEMPO_INSUFFICIENTE) scatta prima del filtro 5 (DIRITTO) perche'
        # e' controllato prima nell'ordine di §Stadio1, quindi l'esito reale
        # oggi e' MONITORA, non SCARTA. Questo verifica solo che
        # l'estrazione del diritto dal testo sia corretta; l'effetto del
        # filtro DIRITTO in isolamento e' verificato sotto con una fixture
        # sintetica che ha la stessa quota indivisa ma scadenza lontana.
        v = self.v("fallcoaste_1629594")
        self.assertIn("quota indivisa", valore_di(v["rec"]["diritto"]))

    def test_diritto_quota_indivisa_scarta_sintetico(self):
        rec = aiuto.rec_minimo(
            id="sintetico_quota_indivisa",
            diritto=aiuto.campo("quota indivisa 2/6", "regex_testo", "alta"),
        )
        risultato = filtri.applica_stadio1(rec, self.config, aiuto.DATA_RUN_TEST)
        self.assertEqual(risultato["esito"], "SCARTA")
        self.assertEqual(risultato["motivo"], "DIRITTO")


class Test05SuperficieAssente(PipelineFixtureTestCase):
    def test_asteannunci_busto_arsizio_da_approfondire(self):
        v = self.v("asteannunci_4625557")
        self.assertEqual(v["stadio1"]["esito"], "PASSA")
        self.assertIn("SUPERFICIE_IGNOTA", v["stadio1"]["flag"])
        motivi = [d["dato"] for d in v["stadio1"]["dati_mancanti"]]
        self.assertIn("superficie", motivi)
        voce = next(d for d in v["stadio1"]["dati_mancanti"] if d["dato"] == "superficie")
        self.assertIn("perizia", voce["dove_trovarlo"].lower())
        # a 42 giorni dalla run (2026-09-16 -> 28/10/2026) e' azionabile
        scadenza = valore_di(v["rec"]["scadenza_asta"])
        self.assertEqual((scadenza.date() - aiuto.DATA_RUN_TEST).days, 42)


class Test06AstaImminente(PipelineFixtureTestCase):
    def test_fallcoaste_binasco_monitora_tempo_insufficiente(self):
        v = self.v("fallcoaste_1653419")
        self.assertEqual(v["stadio1"]["esito"], "MONITORA")
        self.assertEqual(v["stadio1"]["motivo"], "TEMPO_INSUFFICIENTE")


class Test07AstaGiaPassata(PipelineFixtureTestCase):
    def test_asteannunci_gorgonzola_monitora_asta_scaduta(self):
        v = self.v("asteannunci_4538705")
        self.assertEqual(v["stadio1"]["esito"], "MONITORA")
        self.assertEqual(v["stadio1"]["motivo"], "ASTA_SCADUTA")


class Test08DedupPortali(PipelineFixtureTestCase):
    def test_vgi_asteannunci_stesso_lotto_unito(self):
        presente = "vgi_4614163" in self.valutazioni or "asteannunci_4614163" in self.valutazioni
        self.assertTrue(presente)
        vincitore_id = "vgi_4614163" if "vgi_4614163" in self.valutazioni else "asteannunci_4614163"
        altro_id = "asteannunci_4614163" if vincitore_id == "vgi_4614163" else "vgi_4614163"
        self.assertNotIn(altro_id, self.valutazioni, "il duplicato non deve comparire come record separato")
        rec = self.valutazioni[vincitore_id]["rec"]
        self.assertIn(altro_id, rec["alias_ids"])
        self.assertTrue(rec["urls_altre_fonti"], "l'URL dell'altra fonte va conservato")


class Test09LottiDiversiNonUniti(PipelineFixtureTestCase):
    def test_ancona_5_lotti_restano_separati(self):
        ids = [f"asteannunci_{n}" for n in (4597961, 4597970, 4597983, 4597997, 4598007)]
        for id_ in ids:
            self.assertIn(id_, self.valutazioni, f"{id_} deve restare un record separato (lotti diversi)")


class Test10ParserPrezzi(unittest.TestCase):
    def test_tutti_i_formati(self):
        casi = [
            ("176.000,00", 176000),
            ("31800.00", 31800),
            ("€ 125.250", 125250),
            ("6.000.000€", 6000000),
            ("€ 20.800,00", 20800),
            (None, None),
        ]
        for grezzo, atteso in casi:
            with self.subTest(grezzo=grezzo):
                self.assertEqual(parser.norma_prezzo(grezzo), atteso)


class Test11ParserDate(unittest.TestCase):
    def test_formati_supportati(self):
        casi = {
            "15/09/2026 - 15:30": (2026, 9, 15, 15, 30),
            "17/09/2026 h 12:00": (2026, 9, 17, 12, 0),
            "22/10/2026, 15:30": (2026, 10, 22, 15, 30),
            "2026-11-25T12:00:00+00:00": None,  # verificato solo che parse senza errori sotto
        }
        for grezzo, atteso in casi.items():
            with self.subTest(grezzo=grezzo):
                d = parser.norma_data(grezzo)
                self.assertIsNotNone(d, f"non interpretata: {grezzo}")
                if atteso:
                    aa, mm, gg, hh, mi = atteso
                    self.assertEqual((d.year, d.month, d.day, d.hour, d.minute), (aa, mm, gg, hh, mi))


class Test12ComuneDiversoDaCitta(PipelineFixtureTestCase):
    def test_caseasta_comune_da_indirizzo_non_da_city(self):
        # SOSTITUZIONE DICHIARATA: nei dati reali del 16/09/2026 non esiste
        # piu' un record di Gorle con city=Bergamo (citato nella spec come
        # esempio). Lo stesso identico difetto (city = citta' di ricerca
        # diversa dal comune reale nell'indirizzo) esiste su caseasta_32606:
        # city="Verona", indirizzo="Villafranca di Verona (VR) - ...".
        v = self.v("caseasta_32606")
        self.assertEqual(valore_di(v["rec"]["comune"]), "Villafranca di Verona")
        self.assertNotEqual(valore_di(v["rec"]["comune"]), "Verona")


class Test13AltezzaImmobiliare(PipelineFixtureTestCase):
    def test_altezza_valida(self):
        v = self.v("immobiliare_131398054")
        self.assertAlmostEqual(valore_di(v["rec"]["altezza_m"]), 7.1)
        self.assertEqual(v["rec"]["altezza_m"]["confidenza"], "alta")

    def test_altezza_non_plausibile_scartata(self):
        v = self.v("immobiliare_130814250")
        self.assertIsNone(valore_di(v["rec"]["altezza_m"]))
        self.assertIn("ALTEZZA_DA_VERIFICARE", v["stadio1"]["flag"])


class Test14ImmobiliareAllAsta(PipelineFixtureTestCase):
    def test_tipo_vendita_asta_da_auction(self):
        v = self.v("immobiliare_132382128")
        self.assertEqual(v["rec"]["tipo_vendita"], "asta")
        self.assertEqual(v["rec"]["canale"], "IMM_ID")
        scadenza = valore_di(v["rec"]["scadenza_asta"])
        self.assertEqual((scadenza.year, scadenza.month, scadenza.day), (2026, 10, 22))


class Test15PrezzoSuRichiesta(PipelineFixtureTestCase):
    def test_prezzo_null_mai_scarto(self):
        # SOSTITUZIONE DICHIARATA: immobiliare_130966026 (citato nella spec)
        # ha oggi superficie 350 m², che lo scarterebbe per
        # SUPERFICIE_INSUFFICIENTE indipendentemente dal prezzo, mascherando
        # il comportamento da verificare. Si usa invece
        # medianord_capannoni-industriali_v1412-f1, che ha davvero
        # prezzo=null nei dati reali e una superficie (12.000 m²) che non
        # causa scarto (sopra il massimo non è mai un motivo di scarto).
        v = self.v("medianord_capannoni-industriali_v1412-f1")
        self.assertIsNone(valore_di(v["rec"]["prezzo_eur"]))
        self.assertNotEqual(v["stadio1"]["esito"], "SCARTA")
        motivi = [d["dato"] for d in v["stadio1"]["dati_mancanti"]]
        self.assertIn("prezzo", motivi)


class Test16FileVuoto(PipelineFixtureTestCase):
    def test_astalegale_vuoto_nessun_errore(self):
        ids_astalegale = [id_ for id_ in self.valutazioni if id_.startswith("astalegale_")]
        self.assertEqual(ids_astalegale, [])
        # non deve comparire nessun avviso di crash per questo file
        # (un file vuoto e' valido, non "illeggibile")
        self.assertGreater(self.record_letti, 0)


class Test17FasciaSuperficie(PipelineFixtureTestCase):
    def test_dentro_fascia_passa(self):
        # immobiliare_131138538 ha superficie 1200 m² (dentro fascia,
        # nessuna correzione/flag di superficie): questo e' cio' che il
        # caso di test verifica. Il record scarta comunque per un motivo
        # indipendente e legittimo (prezzo/m² SLN sopra la soglia hard,
        # PREZZO_FUORI_SCALA — dato reale del 16/09/2026, non un difetto
        # del filtro sulla superficie), quindi l'asserzione e' mirata al
        # motivo di superficie e non all'esito complessivo.
        v = self.v("immobiliare_131138538")
        self.assertEqual(valore_di(v["rec"]["superficie_mq"]), 1200.0)
        self.assertNotEqual(v["stadio1"].get("motivo"), "SUPERFICIE_INSUFFICIENTE")
        self.assertNotIn("SUPERFICIE_IGNOTA", v["stadio1"]["flag"])
        self.assertNotIn("AL_LIMITE_SOGLIA", v["stadio1"]["flag"])
        self.assertNotIn("FUORI_TAGLIA_GRANDE", v["stadio1"]["flag"])

    def test_sotto_soglia_scarta(self):
        for id_, sup_attesa in (("immobiliare_132452472", 733.0), ("medianord_capannoni-industriali_v1420", 843.0)):
            with self.subTest(id_=id_):
                v = self.v(id_)
                self.assertEqual(valore_di(v["rec"]["superficie_mq"]), sup_attesa)
                self.assertEqual(v["stadio1"]["esito"], "SCARTA")
                self.assertEqual(v["stadio1"]["motivo"], "SUPERFICIE_INSUFFICIENTE")

    def test_fuori_taglia_grande_non_scarta(self):
        v = self.v("worldcapital_RH-260148")
        self.assertEqual(valore_di(v["rec"]["superficie_mq"]), 10500.0)
        self.assertNotEqual(v["stadio1"]["esito"], "SCARTA")
        self.assertIn("FUORI_TAGLIA_GRANDE", v["stadio1"]["flag"])

    def test_al_limite_soglia_sintetico(self):
        # Nessun record reale del 16/09/2026 cade esattamente nella
        # tolleranza 950-999 m²: fixture sintetica dichiarata (vedi
        # tests/aiuto.rec_minimo).
        rec = aiuto.rec_minimo(id="sintetico_al_limite", superficie_mq=aiuto.campo(970.0, "json", "alta"))
        risultato = filtri.applica_stadio1(rec, self.config, aiuto.DATA_RUN_TEST)
        self.assertNotEqual(risultato["esito"], "SCARTA")
        self.assertIn("AL_LIMITE_SOGLIA", risultato["flag"])

    def test_confidenza_bassa_sotto_soglia_non_scarta(self):
        # Fixture sintetica: una superficie letta dal testo con piu' valori
        # ambigui (confidenza bassa) sotto il minimo non deve scartare, solo
        # DA APPROFONDIRE (regola esplicita del filtro 3).
        rec = aiuto.rec_minimo(id="sintetico_conf_bassa", superficie_mq=aiuto.campo(500.0, "regex_testo", "bassa"))
        risultato = filtri.applica_stadio1(rec, self.config, aiuto.DATA_RUN_TEST)
        self.assertNotEqual(risultato["esito"], "SCARTA")
        self.assertIn("SUPERFICIE_CONFIDENZA_BASSA", risultato["flag"])


if __name__ == "__main__":
    unittest.main()
