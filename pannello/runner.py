#!/usr/bin/env python3
"""pannello/runner.py - orchestratore di UNA run settimanale (§7.2).

Gira come PROCESSO SEPARATO dal server del pannello (pannello.py lo lancia
con subprocess.Popen e non lo aspetta): cosi' chiudere il browser, o anche
riavviare pannello.py, non interrompe la run. Lo stato si scambia solo
tramite file (OUTPUT/.run_state.json, OUTPUT/.run.lock, OUTPUT/logs/…): il
server non ha NESSUNA logica di valutazione propria (§7.5), legge solo
questi file.

Fasi, sempre in quest'ordine (§7.2): controlli -> scraping (aste in
parallelo + catena IMM_ID in sequenza, insieme) -> valutazione -> controllo
finale. Con --solo-valutazione si saltano le fasi 1-2 e si riparte dai dati
gia' presenti in INPUT/ (bottone "Riprova solo la valutazione", §7.2).

NOTA DI PROGETTAZIONE - modalita' --simula (§7.5): sostituisce scraper e
Claude con operazioni veloci sui fixture di tests/fixtures/INPUT/, cosi'
tutto il pannello si puo' provare in meno di un minuto, offline, senza
scraper reali (che in questo ambiente di build non esistono nemmeno).

NOTA DI PROGETTAZIONE - scraper reali (§7.2): questo runner lancia
esattamente gli script e gli argomenti dichiarati in config.yaml
(pannello.scraper_aste / catena_immid), senza assumere altri contratti CLI
(es. un flag --output con scrittura su temporaneo e swap atomico): quel
comportamento e' responsabilita' degli scraper stessi, che non fanno parte
di questa build (vedi verifiche_scraper_per_claude_code.md) e non sono mai
stati lanciati qui. Se lo script manca (come in questo ambiente), il runner
lo segnala come errore per quella sola fonte e prosegue con le altre
(§9 test 39): e' un comportamento verificato, non solo dichiarato.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

RADICE_PANNELLO = Path(__file__).resolve().parent
RADICE_PROGETTO_DEFAULT = RADICE_PANNELLO.parent


def _percorso_skill(root: Path) -> Path:
    return root / ".claude" / "skills" / "prontobox-scouting"


def _importa_prontobox(root: Path):
    p = str(_percorso_skill(root))
    if p not in sys.path:
        sys.path.insert(0, p)
    from prontobox import config as cfgmod  # noqa
    from prontobox import util  # noqa

    return cfgmod, util


class Interrotta(Exception):
    pass


class Runner:
    def __init__(self, root: Path, percorso_config: Optional[Path], data_run: date, simula: bool, solo_valutazione: bool) -> None:
        self.root = root
        self.data_run = data_run
        self.simula = simula
        self.solo_valutazione = solo_valutazione

        cfgmod, util = _importa_prontobox(root)
        self._util = util
        self.percorso_config = percorso_config or (_percorso_skill(root) / "config.yaml")
        self.config = cfgmod.carica_config(self.percorso_config)
        self.hash_config = cfgmod.hash_config(self.percorso_config)

        self.output_dir = root / self.config["percorsi"]["output_dir"]
        self.log_dir = self.output_dir / "logs"
        self.stato_path = self.output_dir / ".run_state.json"
        self.lock_path = self.output_dir / ".run.lock"
        self.flag_interrompi = self.output_dir / ".interrompi_richiesta"
        self.log_path = self.log_dir / f"run_{util.id_run(data_run)}.log"

        self._processi: list[subprocess.Popen] = []
        self._processi_lock = threading.Lock()
        self._interrompi_richiesto = threading.Event()
        self._log_fh = None
        self._stato: dict[str, Any] = {}

    # ------------------------------------------------------------------ log e stato

    def _apri_log(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._log_fh = open(self.log_path, "a", encoding="utf-8")

    def _log(self, riga: str) -> None:
        ts = self._util.ora_roma().strftime("%H:%M:%S")
        testo = f"[{ts}] {riga}"
        print(testo, flush=True)
        if self._log_fh:
            self._log_fh.write(testo + "\n")
            self._log_fh.flush()

    def _scrivi_stato(self, **campi: Any) -> None:
        self._stato.update(campi)
        self._stato["aggiornata_il"] = self._util.ora_roma().isoformat()
        self._util.scrivi_json_atomico(self._stato, self.stato_path)

    # ------------------------------------------------------------------ lock (§7.2: una sola run alla volta)

    def _pid_vivo(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        except AttributeError:  # pragma: no cover - piattaforme senza os.kill
            return True
        return True

    def acquisisci_lock(self) -> Optional[str]:
        """Ritorna un messaggio d'errore se non si puo' procedere, altrimenti None."""
        if self.lock_path.is_file():
            try:
                info = self._util.leggi_json(self.lock_path)
                pid_vecchio = info.get("pid")
            except Exception:
                pid_vecchio = None
            if pid_vecchio and self._pid_vivo(int(pid_vecchio)):
                return f"una run e' gia' in corso (pid {pid_vecchio})"
            self._log(f"lock residuo di un processo non piu' attivo (pid {pid_vecchio}): rimosso")
            try:
                self.lock_path.unlink()
            except OSError:
                pass
        self._util.scrivi_json_atomico({"pid": os.getpid(), "avviato_il": self._util.ora_roma().isoformat(), "data_run": self.data_run.isoformat()}, self.lock_path)
        return None

    def rilascia_lock(self) -> None:
        try:
            if self.lock_path.is_file():
                info = self._util.leggi_json(self.lock_path)
                if info.get("pid") == os.getpid():
                    self.lock_path.unlink()
        except Exception:
            pass

    # ------------------------------------------------------------------ interruzione (§7.2: "Interrompi")
    #
    # Il pannello (pannello.py) e questo runner sono DUE PROCESSI SEPARATI
    # (§7.5: "il server non ha nessuna logica propria"). Su Windows un
    # os.kill/signal.SIGTERM tra processi diversi non e' un segnale
    # intercettabile (TerminateProcess uccide subito, senza dare modo a
    # _termina_tutti_i_processi_figli di ripulire gli scraper figli). Per
    # questo l'interruzione passa da un FILE: il pannello lo crea,
    # un thread di sorveglianza qui lo legge ogni secondo. Il gestore di
    # segnali resta comunque installato per chi lancia il runner a mano
    # (Ctrl+C) o su piattaforme dove i segnali funzionano.

    def _installa_gestore_segnali(self) -> None:
        def gestore(signum, frame):  # noqa: ANN001
            self._interrompi_richiesto.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, gestore)
            except (ValueError, OSError):
                pass  # non nel thread principale o piattaforma senza supporto

    def _avvia_sorveglianza_interruzione(self) -> None:
        """Thread demone: se il pannello crea OUTPUT/.interrompi_richiesta,
        interrompe la run entro ~1s anche a meta' dell'attesa bloccante di
        uno scraper (proc.wait), che altrimenti non sarebbe interrompibile."""

        def sorveglia() -> None:
            while not self._interrompi_richiesto.is_set():
                if self.flag_interrompi.is_file():
                    self._log("richiesta di interruzione dal pannello: fermo tutti i processi")
                    self._interrompi_richiesto.set()
                    self._termina_tutti_i_processi_figli()
                    try:
                        self.flag_interrompi.unlink()
                    except OSError:
                        pass
                    break
                time.sleep(1.0)

        threading.Thread(target=sorveglia, daemon=True, name="sorveglianza-interrompi").start()

    def _impedisci_sospensione_windows(self, attiva: bool) -> None:
        """§7.2: il PC non deve andare in sospensione durante la run (solo Windows)."""
        if sys.platform != "win32":
            return
        try:
            import ctypes

            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_DISPLAY_REQUIRED = 0x00000002
            flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if attiva else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - dipende dal sistema operativo
            self._log(f"impossibile impedire la sospensione del PC: {exc}")

    def _termina_tutti_i_processi_figli(self) -> None:
        with self._processi_lock:
            processi = list(self._processi)
        for p in processi:
            if p.poll() is None:
                try:
                    p.terminate()
                except OSError:
                    pass
        deadline = time.time() + 10
        for p in processi:
            resto = max(0, deadline - time.time())
            try:
                p.wait(timeout=resto)
            except subprocess.TimeoutExpired:
                try:
                    p.kill()
                except OSError:
                    pass

    def _controlla_interruzione(self) -> None:
        if self._interrompi_richiesto.is_set():
            raise Interrotta()

    # ------------------------------------------------------------------ fase 1: controlli

    def _fase_controlli(self) -> Optional[str]:
        self._scrivi_stato(fase="controlli")
        self._log("Fase 1/4 - controlli")

        if shutil.which(sys.executable) is None and shutil.which("python") is None and shutil.which("python3") is None:
            return "python non trovato nel PATH"

        if not self.simula:
            eseguibile_claude = (self.config["pannello"].get("comando_claude") or {}).get("eseguibile", "claude")
            if shutil.which(eseguibile_claude) is None:
                return f"comando '{eseguibile_claude}' non trovato: installa Claude Code ed effettua l'accesso prima di riprovare"
            # NOTA: verificare che l'account sia effettivamente connesso
            # richiederebbe di invocare claude (anche solo per un check),
            # cosa che questo build non fa mai in autonomia (vedi
            # SKILL.md/"Limiti noti"): qui ci si limita a verificare che il
            # comando esista nel PATH.

        try:
            uso = shutil.disk_usage(self.root)
            if uso.free < 200 * 1024 * 1024:
                return f"spazio su disco insufficiente ({uso.free // (1024*1024)} MB liberi, minimo consigliato 200 MB)"
        except OSError:
            pass

        return None

    # ------------------------------------------------------------------ fase 2: scraping

    def _esegui_script(self, fonte: str, script_rel: str, argomenti: list[str], timeout_min: int) -> dict[str, Any]:
        script = self.root / script_rel
        inizio = self._util.ora_roma().isoformat()
        if not script.is_file():
            self._log(f"[{fonte}] script non trovato: {script} - fonte saltata, le altre proseguono")
            return {"stato": "errore", "avviato_il": inizio, "completato_il": self._util.ora_roma().isoformat(), "dettaglio": f"script non trovato: {script_rel}", "codice_uscita": None}

        argv = [sys.executable, str(script), *argomenti]
        self._log(f"[{fonte}] avvio: {' '.join(argv)}")
        try:
            proc = subprocess.Popen(argv, cwd=str(self.root), stdout=self._log_fh, stderr=self._log_fh)
        except OSError as exc:
            return {"stato": "errore", "avviato_il": inizio, "completato_il": self._util.ora_roma().isoformat(), "dettaglio": str(exc), "codice_uscita": None}

        with self._processi_lock:
            self._processi.append(proc)
        try:
            codice = proc.wait(timeout=timeout_min * 60)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return {"stato": "errore", "avviato_il": inizio, "completato_il": self._util.ora_roma().isoformat(), "dettaglio": f"timeout dopo {timeout_min} minuti", "codice_uscita": None}

        stato = "completato" if codice == 0 else "errore"
        if codice != 0:
            self._log(f"[{fonte}] terminato con codice {codice}: il file della settimana precedente resta invariato")
        return {"stato": stato, "avviato_il": inizio, "completato_il": self._util.ora_roma().isoformat(), "dettaglio": None if codice == 0 else f"codice di uscita {codice}", "codice_uscita": codice}

    def _fase_scraping_reale(self) -> None:
        pcfg = self.config["pannello"]
        timeout_aste = pcfg.get("timeout_scraper_min", 30)
        # NOTA: prima riusava per errore "timeout_valutazione_min" (pensato per
        # la fase Claude, §7.3) — troppo corto per idealista/immobiliare completi
        # (~53 minuti solo per idealista su 36 città), causando un troncamento a
        # metà scambiato per un errore reale (vedi commento in config.yaml).
        timeout_immid = pcfg.get("timeout_immid_min", 180)

        scraper = {s["fonte"]: {"stato": "attesa"} for s in pcfg.get("scraper_aste", [])}
        immid = {s["fonte"]: {"stato": "attesa"} for s in pcfg.get("catena_immid", [])}
        self._scrivi_stato(fase="scraping", scraper=scraper, immid=immid)

        risultati_lock = threading.Lock()

        def lancia_aste(voce: dict[str, Any]) -> None:
            r = self._esegui_script(voce["fonte"], voce["script"], voce.get("argomenti") or [], timeout_aste)
            with risultati_lock:
                scraper[voce["fonte"]] = r
                self._scrivi_stato(scraper=dict(scraper))

        def lancia_catena_immid() -> None:
            precedenti_ok = True
            for voce in pcfg.get("catena_immid", []):
                if self._interrompi_richiesto.is_set():
                    with risultati_lock:
                        immid[voce["fonte"]] = {"stato": "interrotto"}
                        self._scrivi_stato(immid=dict(immid))
                    continue
                argomenti = list(voce.get("argomenti") or [])
                if voce["fonte"] == "run_opportunities_immid" and not precedenti_ok:
                    self._log("[run_opportunities_immid] parte comunque: riferimento non aggiornato (idealista/immobiliare completo falliti)")
                r = self._esegui_script(voce["fonte"], voce["script"], argomenti, timeout_immid)
                if voce["fonte"] != "run_opportunities_immid" and r["stato"] != "completato":
                    precedenti_ok = False
                if not precedenti_ok and voce["fonte"] == "run_opportunities_immid":
                    r["dettaglio"] = ("riferimento non aggiornato: " + (r.get("dettaglio") or "")).strip(": ")
                with risultati_lock:
                    immid[voce["fonte"]] = r
                    self._scrivi_stato(immid=dict(immid))

        thread_aste = [threading.Thread(target=lancia_aste, args=(voce,), daemon=True) for voce in pcfg.get("scraper_aste", [])]
        thread_immid = threading.Thread(target=lancia_catena_immid, daemon=True)

        for t in thread_aste:
            t.start()
        thread_immid.start()
        for t in thread_aste:
            t.join()
        thread_immid.join()

        self._controlla_interruzione()

        falliti = [f for f, r in {**scraper, **immid}.items() if r.get("stato") == "errore"]
        if falliti:
            self._log(f"fonti non aggiornate questa settimana: {', '.join(falliti)} (si prosegue comunque, §9 test 39)")

    def _fase_scraping_simulata(self) -> None:
        """--simula (§7.5): copia i fixture di test al posto di lanciare scraper veri."""
        self._scrivi_stato(fase="scraping")
        fixtures = _percorso_skill(self.root) / "tests" / "fixtures" / "INPUT"
        input_aste = self.root / self.config["percorsi"]["input_aste"]
        input_immid = self.root / self.config["percorsi"]["input_immid"]
        input_aste.mkdir(parents=True, exist_ok=True)
        input_immid.mkdir(parents=True, exist_ok=True)

        scraper: dict[str, Any] = {}
        immid: dict[str, Any] = {}
        for f in sorted((fixtures / "ASTE").glob("dataset_*_completo.json")):
            self._controlla_interruzione()
            time.sleep(0.05)  # solo per rendere visibile l'avanzamento nel pannello
            shutil.copyfile(f, input_aste / f.name)
            n = self._conta_annunci(input_aste / f.name)
            fonte = f.stem.replace("dataset_", "").replace("_completo", "")
            scraper[fonte] = {"stato": "completato", "annunci_trovati": n, "dettaglio": "[simulato] copiato da tests/fixtures"}
            self._log(f"[simulato:{fonte}] {n} annunci")
            self._scrivi_stato(scraper=dict(scraper))

        for f in sorted((fixtures / "IMM_ID" / "current_week").glob("dataset_*.json")):
            self._controlla_interruzione()
            time.sleep(0.05)
            shutil.copyfile(f, input_immid / f.name)
            n = self._conta_annunci(input_immid / f.name)
            fonte = f.stem.replace("dataset_", "")
            immid[fonte] = {"stato": "completato", "annunci_trovati": n, "dettaglio": "[simulato] copiato da tests/fixtures"}
            self._log(f"[simulato:{fonte}] {n} annunci")
            self._scrivi_stato(immid=dict(immid))

        self._scrivi_stato(scraper=scraper, immid=immid)

    def _conta_annunci(self, percorso: Path) -> int:
        try:
            dati = self._util.leggi_json(percorso)
        except Exception:
            return 0
        if isinstance(dati, dict):
            listings = dati.get("listings")
            if isinstance(listings, dict):
                return len(listings)
            if isinstance(listings, list):
                return len(listings)
        if isinstance(dati, list):
            return len(dati)
        return 0

    # ------------------------------------------------------------------ fase 3: valutazione

    def _lancia_scout(self, *args: str) -> int:
        script = _percorso_skill(self.root) / "scout.py"
        argv = [sys.executable, str(script), "--root", str(self.root), "--config", str(self.percorso_config), "--data", self.data_run.isoformat(), *args]
        self._log("comando: " + " ".join(argv))
        proc = subprocess.Popen(argv, cwd=str(self.root), stdout=self._log_fh, stderr=self._log_fh)
        with self._processi_lock:
            self._processi.append(proc)
        return proc.wait()

    def _fase_valutazione(self) -> Optional[str]:
        self._scrivi_stato(fase="valutazione", valutazione={"stato": "in_corso"})
        self._log("Fase 3/4 - valutazione")

        codice = self._lancia_scout("prepara")
        self._controlla_interruzione()
        if codice != 0:
            return f"'scout.py prepara' ha fallito (codice {codice}): vedi il log"

        if self.simula:
            # §7.5: anche Claude e' sostituito da uno script finto e veloce.
            # scout.py scrivi, senza --giudizi, usa da solo il ripiego
            # euristico (prontobox/giudizi_fallback.py) e lo dichiara in
            # meta.note: e' esattamente il comportamento "veloce" richiesto.
            self._log("[simulato] valutazione testuale con il ripiego euristico al posto di Claude Code")
            codice = self._lancia_scout("scrivi")
        else:
            pcfg = self.config["pannello"]
            cc = pcfg.get("comando_claude") or {}
            eseguibile = cc.get("eseguibile", "claude")
            data_str = self.data_run.isoformat()
            argomenti = [str(a).replace("{DATA}", data_str) for a in cc.get("argomenti", [])]
            argv = [eseguibile, *argomenti]
            self._log("comando Claude: " + " ".join(argv))
            timeout_min = cc.get("timeout_min", 60)
            try:
                proc = subprocess.Popen(argv, cwd=str(self.root), stdout=self._log_fh, stderr=self._log_fh)
                with self._processi_lock:
                    self._processi.append(proc)
                codice_claude = proc.wait(timeout=timeout_min * 60)
            except (OSError, subprocess.TimeoutExpired) as exc:
                self._log(f"Claude Code non disponibile o in timeout ({exc}): si prosegue con il ripiego euristico")
                codice_claude = -1
            self._controlla_interruzione()

            percorso_report = self.output_dir / self.config["percorsi"]["formato_nome_report"].format(
                AAAA=f"{self.data_run.year:04d}", MM=f"{self.data_run.month:02d}", GG=f"{self.data_run.day:02d}"
            )
            if codice_claude == 0 and percorso_report.is_file():
                # Claude ha gia' lanciato da solo "scout.py scrivi" (§SKILL.md).
                codice = 0
            else:
                percorso_giudizi_tpl = cc.get("percorso_giudizi", "opportunities/OUTPUT/.giudizi_{DATA}.json")
                percorso_giudizi = self.root / percorso_giudizi_tpl.replace("{DATA}", data_str)
                if percorso_giudizi.is_file():
                    self._log(f"Claude non ha completato 'scrivi' da solo: lo rifaccio con {percorso_giudizi.name}")
                    codice = self._lancia_scout("scrivi", "--giudizi", str(percorso_giudizi))
                else:
                    self._log("nessun file di giudizi trovato: uso il ripiego euristico")
                    codice = self._lancia_scout("scrivi")

        self._controlla_interruzione()
        if codice != 0:
            self._scrivi_stato(valutazione={"stato": "errore"})
            return f"la valutazione ha fallito (codice {codice}): vedi il log"
        self._scrivi_stato(valutazione={"stato": "completato"})
        return None

    # ------------------------------------------------------------------ fase 4: controllo finale

    def _fase_controllo_finale(self) -> Optional[str]:
        self._scrivi_stato(fase="controllo_finale")
        self._log("Fase 4/4 - controllo finale")

        _cfgmod, util = self._util and (None, self._util)
        nome_file = self.config["percorsi"]["formato_nome_report"].format(
            AAAA=f"{self.data_run.year:04d}", MM=f"{self.data_run.month:02d}", GG=f"{self.data_run.day:02d}"
        )
        percorso_report = self.output_dir / nome_file
        if not percorso_report.is_file():
            return f"{nome_file} non e' stato scritto"
        try:
            report = self._util.leggi_json(percorso_report)
        except Exception as exc:
            return f"{nome_file} non e' un JSON valido: {exc}"

        sys.path.insert(0, str(_percorso_skill(self.root)))
        from prontobox import report as report_mod  # noqa

        errori = report_mod.valida_report(report)
        if errori:
            return "report non valido: " + "; ".join(errori[:5])

        percorso_summary = self.output_dir / "summary.json"
        if report.get("opportunita") and not percorso_summary.is_file():
            return "summary.json non e' stato aggiornato"

        self._log(f"controllo finale ok: {len(report.get('opportunita') or [])} opportunita' segnalate")
        return None

    # ------------------------------------------------------------------ orchestrazione

    def avvia(self) -> int:
        self._apri_log()
        self._installa_gestore_segnali()
        self._log(f"=== avvio run {self.data_run.isoformat()} (simula={self.simula}, solo_valutazione={self.solo_valutazione}) ===")

        errore_lock = self.acquisisci_lock()
        if errore_lock:
            self._log(f"ERRORE: {errore_lock}")
            return 2

        # Un flag residuo di una run precedente interrotta non deve fermare
        # subito questa run nuova.
        try:
            if self.flag_interrompi.is_file():
                self.flag_interrompi.unlink()
        except OSError:
            pass
        self._avvia_sorveglianza_interruzione()

        impedisci = bool(self.config.get("pannello", {}).get("impedisci_sospensione", True))
        if impedisci:
            self._impedisci_sospensione_windows(True)

        try:
            self._scrivi_stato(
                fase="controlli",
                data_run=self.data_run.isoformat(),
                simula=self.simula,
                hash_config=self.hash_config,
                avviata_il=self._util.ora_roma().isoformat(),
                log_file=str(self.log_path.relative_to(self.root)),
                errori=[],
            )

            if not self.solo_valutazione:
                errore = self._fase_controlli()
                self._controlla_interruzione()
                if errore:
                    self._scrivi_stato(fase="errore", fase_fallita="controlli", errori=[errore])
                    self._log(f"ERRORE fase 1: {errore}")
                    return 1

                if self.simula:
                    self._fase_scraping_simulata()
                else:
                    self._fase_scraping_reale()
                self._controlla_interruzione()

            errore = self._fase_valutazione()
            self._controlla_interruzione()
            if errore:
                # fase_fallita="valutazione": e' il caso in cui il pannello
                # mostra "Riprova solo la valutazione" (§7.2), perche' i dati
                # in INPUT/ sono gia' aggiornati e non serve rifare lo scraping.
                self._scrivi_stato(fase="errore", fase_fallita="valutazione", errori=[errore])
                self._log(f"ERRORE fase 3: {errore}")
                return 1

            errore = self._fase_controllo_finale()
            if errore:
                self._scrivi_stato(fase="errore", fase_fallita="controllo_finale", errori=[errore])
                self._log(f"ERRORE fase 4: {errore}")
                return 1

            self._scrivi_stato(fase="completata")
            self._log("=== run completata ===")
            return 0

        except Interrotta:
            self._termina_tutti_i_processi_figli()
            self._scrivi_stato(fase="interrotta")
            self._log("=== run interrotta su richiesta dell'utente ===")
            return 130
        except Exception as exc:  # ultima rete di sicurezza: mai un crash senza stato scritto
            self._termina_tutti_i_processi_figli()
            # fase_fallita = l'ultima fase nota prima del crash (es. "scraping"),
            # cosi' il pannello mostra un'etichetta leggibile invece di "sconosciuta".
            fase_in_corso = self._stato.get("fase")
            self._scrivi_stato(fase="errore", fase_fallita=fase_in_corso, errori=[f"errore interno: {exc}"])
            self._log(f"ERRORE interno: {exc}")
            import traceback

            traceback.print_exc(file=self._log_fh)
            return 1
        finally:
            if impedisci:
                self._impedisci_sospensione_windows(False)
            try:
                if self.flag_interrompi.is_file():
                    self.flag_interrompi.unlink()
            except OSError:
                pass
            self.rilascia_lock()
            if self._log_fh:
                self._log_fh.close()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Orchestratore di una run del pannello prontobox-scouting")
    ap.add_argument("--root", required=True)
    ap.add_argument("--config", default=None)
    ap.add_argument("--data", default=None, help="AAAA-MM-GG, default: oggi (Europe/Rome)")
    ap.add_argument("--simula", action="store_true")
    ap.add_argument("--solo-valutazione", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    _cfgmod, util = _importa_prontobox(root)
    data_run = date.fromisoformat(args.data) if args.data else util.oggi_roma()
    percorso_config = Path(args.config).resolve() if args.config else None

    runner = Runner(root, percorso_config, data_run, args.simula, args.solo_valutazione)
    return runner.avvia()


if __name__ == "__main__":
    raise SystemExit(main())
