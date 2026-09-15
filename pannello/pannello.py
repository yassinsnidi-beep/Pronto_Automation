#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pannello/pannello.py - server locale del pannello settimanale (§7 della spec).

Espone una piccola API JSON e serve le pagine statiche di pannello/static/.
Non contiene NESSUNA logica di valutazione (§7.5): legge solo
opportunities/OUTPUT/report_*.json, summary.json e .run_state.json, e lancia
pannello/runner.py come processo separato quando l'utente preme Avvia.

Uso:
    python pannello/pannello.py [--root CARTELLA] [--porta N] [--simula]
    (--simula: usa i fixture della skill al posto di scraper veri e di
    Claude Code, per provare il pannello in meno di un minuto, §7.5)

Sicurezza (§7.5): il server ascolta SOLO su 127.0.0.1. Le azioni che
cambiano stato (avvia, interrompi, riprova, rilancia) sono POST con un
token generato all'avvio e incorporato nella pagina: nessun altro sito puo'
attivarle.
"""

from __future__ import annotations

import argparse
import json
import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

PANNELLO_DIR = Path(__file__).resolve().parent
RADICE_DEFAULT = PANNELLO_DIR.parent
STATIC_DIR = PANNELLO_DIR / "static"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def _percorso_skill(root: Path) -> Path:
    return root / ".claude" / "skills" / "prontobox-scouting"


def _importa_prontobox(root: Path):
    p = str(_percorso_skill(root))
    if p not in sys.path:
        sys.path.insert(0, p)
    from prontobox import config as cfgmod  # noqa
    from prontobox import util  # noqa

    return cfgmod, util


class StatoServer:
    """Stato condiviso del processo pannello.py: root, config, token."""

    def __init__(self, root: Path, forza_simula: bool) -> None:
        self.root = root
        self.forza_simula = forza_simula
        self.cfgmod, self.util = _importa_prontobox(root)
        self.percorso_config = _percorso_skill(root) / "config.yaml"
        self.config = self.cfgmod.carica_config(self.percorso_config)
        self.output_dir = root / self.config["percorsi"]["output_dir"]
        self.lock_path = self.output_dir / ".run.lock"
        self.flag_interrompi = self.output_dir / ".interrompi_richiesta"
        self.token = secrets.token_hex(24)
        self._lock_ricarica = threading.Lock()
        self._lock_avvio = threading.Lock()

    def ricarica_config_se_cambiata(self) -> None:
        with self._lock_ricarica:
            self.config = self.cfgmod.carica_config(self.percorso_config)
            self.output_dir = self.root / self.config["percorsi"]["output_dir"]
            self.lock_path = self.output_dir / ".run.lock"
            self.flag_interrompi = self.output_dir / ".interrompi_richiesta"

    # ---------------------------------------------------------------- lock

    def _pid_vivo(self, pid: int) -> bool:
        try:
            import os

            os.kill(pid, 0)
        except OSError:
            return False
        except AttributeError:  # pragma: no cover
            return True
        return True

    def run_attiva(self) -> Optional[dict]:
        """Ritorna le info del lock se una run e' effettivamente in corso
        (pid vivo), altrimenti None (anche se il file lock esiste ma e'
        residuo di un processo morto: lo consideriamo libero, come fa
        runner.py stesso all'avvio successivo)."""
        if not self.lock_path.is_file():
            return None
        try:
            info = self.util.leggi_json(self.lock_path)
            pid = int(info.get("pid"))
        except Exception:
            return None
        if self._pid_vivo(pid):
            return info
        return None

    # ---------------------------------------------------------------- letture di stato

    def leggi_stato_run(self) -> Optional[dict]:
        percorso = self.output_dir / ".run_state.json"
        if not percorso.is_file():
            return None
        try:
            return self.util.leggi_json(percorso)
        except Exception:
            return None

    def leggi_log(self, run_id: str, max_righe: int = 500) -> str:
        # run_id arriva dal client come data ISO ("AAAA-MM-GG", es. dal
        # campo run.data_run di /api/stato); il file di log e' invece
        # nominato da runner.py con util.id_run() ("AAAA_MM_GG"): si
        # normalizza qui cosi' entrambi i formati funzionano.
        nome_file = f"run_{run_id.replace('-', '_')}.log"
        percorso = self.output_dir / "logs" / nome_file
        if not percorso.is_file():
            return ""
        try:
            testo = percorso.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        righe = testo.splitlines()
        if len(righe) > max_righe:
            righe = righe[-max_righe:]
        return "\n".join(righe)

    def _nome_report(self, d: date) -> str:
        return self.config["percorsi"]["formato_nome_report"].format(
            AAAA=f"{d.year:04d}", MM=f"{d.month:02d}", GG=f"{d.day:02d}"
        )

    def report_oggi_esiste(self) -> bool:
        return (self.output_dir / self._nome_report(self.util.oggi_roma())).is_file()

    def elenco_report(self) -> list[dict]:
        """Per la schermata Storico: un rigo per ogni report_*.json presente,
        piu' leggero di riaprirli tutti per intero (legge solo `meta` e le
        statistiche, non l'elenco delle opportunita')."""
        risultato = []
        if not self.output_dir.is_dir():
            return risultato
        for f in sorted(self.output_dir.glob("report_*.json"), reverse=True):
            try:
                dati = self.util.leggi_json(f)
            except Exception:
                continue
            meta = dati.get("meta", {})
            stat = meta.get("statistiche", {})
            risultato.append(
                {
                    "file": f.name,
                    "report_id": meta.get("report_id"),
                    "generato_il": meta.get("generato_il"),
                    "n_segnalate": (stat.get("segnalate") or {}).get("totale"),
                    "n_nuove": (stat.get("segnalate") or {}).get("nuove"),
                    "n_risegnalate": (stat.get("segnalate") or {}).get("risegnalate"),
                }
            )
        return risultato

    def ultimo_report_riassunto(self) -> Optional[dict]:
        elenco = self.elenco_report()
        if not elenco:
            return None
        ultimo = elenco[0]
        percorso = self.output_dir / ultimo["file"]
        try:
            dati = self.util.leggi_json(percorso)
        except Exception:
            return ultimo
        opp = dati.get("opportunita") or []
        ultimo = dict(ultimo)
        ultimo["top3"] = [
            {
                "id": o.get("id"),
                "canale": o.get("canale"),
                "voto": o.get("voto"),
                "esito": o.get("esito"),
                "comune": (o.get("snapshot") or {}).get("comune"),
                "stato_segnalazione": o.get("stato_segnalazione"),
            }
            for o in opp[:3]
        ]
        try:
            generato = datetime.fromisoformat(ultimo["generato_il"])
            ultimo["giorni_fa"] = (self.util.oggi_roma() - generato.date()).days
        except Exception:
            ultimo["giorni_fa"] = None
        return ultimo

    def cerca_id_nello_storico(self, query: str) -> list[dict]:
        """Ricerca semplice per la schermata Storico (§7.4): tutte le
        comparse di un id o comune nei report passati, con data e voto."""
        query_norm = query.strip().lower()
        trovate: list[dict] = []
        if not query_norm:
            return trovate
        for f in sorted(self.output_dir.glob("report_*.json")):
            try:
                dati = self.util.leggi_json(f)
            except Exception:
                continue
            for o in dati.get("opportunita") or []:
                comune = ((o.get("snapshot") or {}).get("comune") or "").lower()
                if query_norm in (o.get("id") or "").lower() or query_norm in comune:
                    trovate.append(
                        {
                            "file": f.name,
                            "id": o.get("id"),
                            "comune": (o.get("snapshot") or {}).get("comune"),
                            "voto": o.get("voto"),
                            "esito": o.get("esito"),
                            "prezzo_eur": (o.get("snapshot") or {}).get("prezzo_eur"),
                            "generato_il": (dati.get("meta") or {}).get("generato_il"),
                        }
                    )
        return trovate

    # ---------------------------------------------------------------- avvio run

    def avvia_run(self, simula: bool, solo_valutazione: bool, data: Optional[str] = None) -> tuple[bool, str]:
        # Il file di lock e' scritto da runner.py stesso, poco dopo essere
        # partito (§7.5): tra il subprocess.Popen() qui sotto e quel primo
        # scrivi_json_atomico() del figlio c'e' una finestra - piccola ma
        # reale - in cui run_attiva() non vede ancora nessun lock. Due
        # richieste POST /api/avvia ravvicinate (doppio clic, due schede del
        # browser) potrebbero cosi' passare entrambe il controllo e avviare
        # due runner in parallelo sugli stessi file di output. Il lock
        # Python qui sotto serializza le richieste sullo stesso processo
        # pannello.py, e resta acquisito finche' non si e' confermato che il
        # figlio ha scritto il proprio lock (o e' terminato subito, es. per
        # un errore di avvio): a quel punto run_attiva() e' di nuovo
        # affidabile per la richiesta successiva.
        with self._lock_avvio:
            attiva = self.run_attiva()
            if attiva:
                return False, f"una run e' gia' in corso (avviata il {attiva.get('avviato_il', '?')})"

            argv = [
                sys.executable,
                str(PANNELLO_DIR / "runner.py"),
                "--root",
                str(self.root),
                "--data",
                data or self.util.oggi_roma().isoformat(),
            ]
            if simula or self.forza_simula:
                argv.append("--simula")
            if solo_valutazione:
                argv.append("--solo-valutazione")

            # Su Windows, senza CREATE_NEW_PROCESS_GROUP il runner condivide la
            # stessa console di questo processo (pannello.py): un Ctrl+C premuto
            # nella finestra del pannello arriverebbe anche al runner, interrompendo
            # in silenzio la catena IMM_ID (che controlla il flag ad ogni fonte)
            # senza fermare gli scraper aste gia' avviati (che non lo controllano a
            # meta') ne' lasciare traccia nel log. Un gruppo di processi separato
            # isola il runner da questo, lasciando "Interrompi" (via file) come
            # unico modo per fermarlo intenzionalmente (vedi commento in runner.py).
            kwargs: dict = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

            try:
                proc = subprocess.Popen(
                    argv,
                    cwd=str(self.root),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    **kwargs,
                )
            except OSError as exc:
                return False, f"impossibile avviare il runner: {exc}"

            scadenza = time.monotonic() + 5.0
            while time.monotonic() < scadenza:
                if proc.poll() is not None:
                    break  # il processo e' gia' terminato (es. errore immediato)
                try:
                    info = self.util.leggi_json(self.lock_path)
                    if int(info.get("pid")) == proc.pid:
                        break  # il figlio ha preso in mano il proprio lock: da qui in poi run_attiva() lo vede
                except Exception:
                    pass
                time.sleep(0.05)

            return True, "run avviata"

    def richiedi_interruzione(self) -> tuple[bool, str]:
        attiva = self.run_attiva()
        if not attiva:
            return False, "nessuna run in corso da interrompere"
        try:
            self.util.scrivi_json_atomico({}, self.flag_interrompi, indent=None)
        except Exception as exc:
            return False, f"impossibile scrivere il segnale di interruzione: {exc}"
        return True, "interruzione richiesta"


class Handler(BaseHTTPRequestHandler):
    server_version = "ProntoboxScouting/2.0"
    stato: StatoServer  # impostato da ServerConTokens sotto

    def log_message(self, fmt: str, *args: Any) -> None:  # silenzia il log di default nel terminale utente
        pass

    # ------------------------------------------------------------------ util risposta

    def _json(self, payload: Any, codice: int = 200) -> None:
        corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(codice)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _testo(self, testo: str, tipo: str = "text/plain; charset=utf-8", codice: int = 200) -> None:
        corpo = testo.encode("utf-8")
        self.send_response(codice)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _corpo_json(self) -> dict:
        lunghezza = int(self.headers.get("Content-Length", "0") or "0")
        if lunghezza <= 0:
            return {}
        grezzo = self.rfile.read(lunghezza)
        try:
            dati = json.loads(grezzo.decode("utf-8"))
            return dati if isinstance(dati, dict) else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _token_valido(self, dati: dict) -> bool:
        token = dati.get("token") or self.headers.get("X-Prontobox-Token") or ""
        return secrets.compare_digest(str(token), self.stato.token)

    # ------------------------------------------------------------------ file statici

    def _serve_statico(self, percorso_rel: str) -> None:
        if percorso_rel in ("", "/"):
            percorso_rel = "index.html"
        percorso_rel = percorso_rel.lstrip("/")
        candidato = (STATIC_DIR / percorso_rel).resolve()
        try:
            candidato.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._testo("Percorso non valido", codice=400)
            return
        if not candidato.is_file():
            self._testo("Non trovato", codice=404)
            return

        contenuto = candidato.read_bytes()
        if candidato.name == "index.html":
            contenuto = contenuto.replace(b"__TOKEN__", self.stato.token.encode("ascii"))
        mime = MIME.get(candidato.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(contenuto)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(contenuto)

    # ------------------------------------------------------------------ GET

    def do_GET(self) -> None:  # noqa: N802 (nome richiesto da BaseHTTPRequestHandler)
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if url.path == "/api/stato":
            self._api_stato()
            return
        if url.path == "/api/report":
            self._api_report(query)
            return
        if url.path == "/api/storico":
            self._api_storico()
            return
        if url.path == "/api/cerca":
            self._api_cerca(query)
            return
        if url.path == "/api/log":
            self._api_log(query)
            return
        if url.path.startswith("/api/"):
            self._testo("Non trovato", codice=404)
            return

        self._serve_statico(url.path)

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        dati = self._corpo_json()

        if url.path == "/api/avvia":
            if not self._token_valido(dati):
                self._json({"ok": False, "errore": "token non valido"}, codice=403)
                return
            self.stato.ricarica_config_se_cambiata()
            ok, msg = self.stato.avvia_run(simula=bool(dati.get("simula")), solo_valutazione=False, data=dati.get("data"))
            self._json({"ok": ok, "messaggio": msg})
            return

        if url.path == "/api/riprova_valutazione":
            if not self._token_valido(dati):
                self._json({"ok": False, "errore": "token non valido"}, codice=403)
                return
            self.stato.ricarica_config_se_cambiata()
            ok, msg = self.stato.avvia_run(simula=bool(dati.get("simula")), solo_valutazione=True, data=dati.get("data"))
            self._json({"ok": ok, "messaggio": msg})
            return

        if url.path == "/api/interrompi":
            if not self._token_valido(dati):
                self._json({"ok": False, "errore": "token non valido"}, codice=403)
                return
            ok, msg = self.stato.richiedi_interruzione()
            self._json({"ok": ok, "messaggio": msg})
            return

        self._testo("Non trovato", codice=404)

    # ------------------------------------------------------------------ implementazione API

    def _api_stato(self) -> None:
        s = self.stato
        attiva = s.run_attiva()
        run_state = s.leggi_stato_run()
        payload = {
            "lock_attivo": attiva is not None,
            "run": run_state,
            "report_oggi_esiste": s.report_oggi_esiste(),
            "ultima_run": s.ultimo_report_riassunto(),
            "giorni_promemoria": s.config.get("pannello", {}).get("giorni_promemoria", 7),
            "simula_forzata": s.forza_simula,
            "ora_server": s.util.ora_roma().isoformat(),
        }
        self._json(payload)

    def _api_report(self, query: dict) -> None:
        nomi = query.get("file")
        if not nomi:
            self._json({"errore": "parametro 'file' mancante"}, codice=400)
            return
        nome = nomi[0]
        if "/" in nome or "\\" in nome or not nome.startswith("report_") or not nome.endswith(".json"):
            self._json({"errore": "nome file non valido"}, codice=400)
            return
        percorso = self.stato.output_dir / nome
        if not percorso.is_file():
            self._json({"errore": "report non trovato"}, codice=404)
            return
        try:
            dati = self.stato.util.leggi_json(percorso)
        except Exception as exc:
            self._json({"errore": f"report illeggibile: {exc}"}, codice=500)
            return
        self._json(dati)

    def _api_storico(self) -> None:
        self._json({"report": self.stato.elenco_report()})

    def _api_cerca(self, query: dict) -> None:
        q = (query.get("q") or [""])[0]
        self._json({"risultati": self.stato.cerca_id_nello_storico(q)})

    def _api_log(self, query: dict) -> None:
        run_id = (query.get("run") or [""])[0]
        if not run_id or "/" in run_id or "\\" in run_id:
            self._json({"errore": "parametro 'run' mancante o non valido"}, codice=400)
            return
        self._json({"log": self.stato.leggi_log(run_id)})


def _porta_libera(porta_preferita: int, tentativi: int = 50) -> int:
    for offset in range(tentativi):
        porta = porta_preferita + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", porta))
            except OSError:
                continue
            return porta
    return porta_preferita


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Pannello settimanale prontobox-scouting")
    ap.add_argument("--root", default=str(RADICE_DEFAULT), help="Cartella Pronto_Automation (default: la cartella superiore a pannello/)")
    ap.add_argument("--porta", type=int, default=None, help="Porta preferita (default: dal config.yaml, 8765)")
    ap.add_argument("--simula", action="store_true", help="Ogni run avviata da questo pannello usa i fixture invece di scraper/Claude veri (§7.5)")
    ap.add_argument("--no-browser", action="store_true", help="Non aprire automaticamente il browser")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if not (root / ".claude" / "skills" / "prontobox-scouting" / "scout.py").is_file():
        print(f"ERRORE: non trovo la skill in {root} (mi aspetto .claude/skills/prontobox-scouting/scout.py)", file=sys.stderr)
        return 1

    stato = StatoServer(root, forza_simula=args.simula)
    porta_preferita = args.porta or stato.config.get("pannello", {}).get("porta", 8765)
    porta = _porta_libera(porta_preferita)
    if porta != porta_preferita:
        print(f"Porta {porta_preferita} occupata: uso la {porta}.")

    Handler.stato = stato
    httpd = ThreadingHTTPServer(("127.0.0.1", porta), Handler)

    url = f"http://127.0.0.1:{porta}/"
    print("=" * 60)
    print("  Prontobox Scouting - pannello settimanale")
    print("=" * 60)
    print(f"  Cartella: {root}")
    print(f"  Indirizzo: {url}")
    if args.simula:
        print("  Modalita' SIMULATA (--simula): nessuno scraper reale verra' lanciato.")
    print("  Lascia questa finestra aperta finche' stai usando il pannello.")
    print("  Premi Ctrl+C per chiuderlo.")
    print("=" * 60)

    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
