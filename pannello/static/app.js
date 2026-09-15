/* Prontobox Scouting - pannello settimanale - logica dell'interfaccia (§7.4-7.5)
 * Nessuna libreria esterna. Interroga /api/stato ogni ~2s e ridisegna solo
 * la schermata attiva. Tutto lo stato vive in variabili JS (niente
 * localStorage/sessionStorage: il pannello deve restare semplice e
 * affidabile anche se il browser blocca lo storage). */

(function () {
  "use strict";

  var TOKEN = window.PB_TOKEN || "";
  var INTERVALLO_POLL_MS = 2000;

  var stato = {
    schermo: "home",
    ultimoStatoApi: null,
    reportCorrente: null,
    reportCorrenteFile: null,
    storicoCache: null,
    runInCorsoAlAvvio: false,
    // La schermata Avanzamento si ridisegna da zero ad ogni poll (ogni
    // INTERVALLO_POLL_MS): senza ricordare qui se il riquadro "Registro
    // dettagliato" era aperto, un <details> nuovo nasce sempre chiuso e
    // l'utente lo vede richiudersi da solo pochi secondi dopo averlo aperto.
    logAperto: false,
  };

  // ------------------------------------------------------------------ util

  function el(tag, attrs, figli) {
    var e = document.createElement(tag);
    attrs = attrs || {};
    for (var k in attrs) {
      if (!Object.prototype.hasOwnProperty.call(attrs, k)) continue;
      if (k === "class") e.className = attrs[k];
      else if (k === "html") e.innerHTML = attrs[k];
      else if (k.indexOf("on") === 0 && typeof attrs[k] === "function") e.addEventListener(k.slice(2), attrs[k]);
      else e.setAttribute(k, attrs[k]);
    }
    (figli || []).forEach(function (f) {
      if (f === null || f === undefined) return;
      if (typeof f === "string") e.appendChild(document.createTextNode(f));
      else e.appendChild(f);
    });
    return e;
  }

  function svuota(nodo) {
    while (nodo.firstChild) nodo.removeChild(nodo.firstChild);
  }

  function fmtNum(v, decimali) {
    if (v === null || v === undefined || Number.isNaN(v)) return "n.d.";
    decimali = decimali || 0;
    return Number(v).toLocaleString("it-IT", { minimumFractionDigits: decimali, maximumFractionDigits: decimali });
  }

  function fmtEur(v) {
    return v === null || v === undefined ? "n.d." : fmtNum(v) + " €";
  }

  function fmtMq(v) {
    return v === null || v === undefined ? "n.d." : fmtNum(v) + " m²";
  }

  function fmtData(v) {
    if (!v) return "n.d.";
    var d = new Date(v);
    if (Number.isNaN(d.getTime())) return String(v);
    return d.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit", year: "numeric" });
  }

  function fmtDataOra(v) {
    if (!v) return "n.d.";
    var d = new Date(v);
    if (Number.isNaN(d.getTime())) return String(v);
    return d.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit", year: "numeric" }) + " alle " +
      d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
  }

  function oggiIso() {
    var d = new Date();
    var mese = String(d.getMonth() + 1).padStart(2, "0");
    var giorno = String(d.getDate()).padStart(2, "0");
    return d.getFullYear() + "-" + mese + "-" + giorno;
  }

  // ------------------------------------------------------------------ chiamate API

  function apiGet(percorso) {
    return fetch(percorso, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function apiPost(percorso, corpo) {
    corpo = corpo || {};
    corpo.token = TOKEN;
    return fetch(percorso, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corpo),
    }).then(function (r) {
      return r.json().then(function (dati) {
        if (!r.ok) {
          var errore = new Error(dati.errore || dati.messaggio || "HTTP " + r.status);
          errore.dati = dati;
          throw errore;
        }
        return dati;
      });
    });
  }

  // ------------------------------------------------------------------ toast

  function mostraToast(testo, tipo) {
    var contenitore = document.getElementById("contenitore-toast");
    var t = el("div", { class: "toast" + (tipo ? " " + tipo : "") }, [testo]);
    contenitore.appendChild(t);
    setTimeout(function () {
      if (t.parentNode) t.parentNode.removeChild(t);
    }, 5000);
  }

  // ------------------------------------------------------------------ modale conferma

  function chiediConferma(titolo, messaggio, testoOk) {
    return new Promise(function (resolve) {
      var sfondo = document.getElementById("modale-conferma");
      var corpo = document.getElementById("modale-corpo");
      svuota(corpo);
      corpo.appendChild(el("h3", {}, [titolo]));
      corpo.appendChild(el("p", { class: "testo-attenuato" }, [messaggio]));
      var azioni = el("div", { class: "modale-azioni" });
      var annulla = el("button", { class: "pulsante" }, ["Annulla"]);
      var conferma = el("button", { class: "pulsante primario" }, [testoOk || "Conferma"]);
      azioni.appendChild(annulla);
      azioni.appendChild(conferma);
      corpo.appendChild(azioni);
      function chiudi(risultato) {
        sfondo.setAttribute("hidden", "hidden");
        resolve(risultato);
      }
      annulla.onclick = function () { chiudi(false); };
      conferma.onclick = function () { chiudi(true); };
      sfondo.removeAttribute("hidden");
    });
  }

  // ------------------------------------------------------------------ mappe etichette

  var ETICHETTE_FASE = {
    controlli: "Controlli iniziali",
    scraping: "Raccolta dati (scraping)",
    valutazione: "Valutazione",
    controllo_finale: "Controllo finale",
    completata: "Completata",
    errore: "Errore",
    interrotta: "Interrotta",
  };

  var PASSI_STEPPER = ["controlli", "scraping", "valutazione", "controllo_finale", "completata"];

  var ETICHETTE_STATO_FONTE = {
    attesa: "in attesa",
    in_corso: "in corso",
    completato: "completato",
    errore: "errore",
    interrotto: "interrotto",
  };

  function classeBadgeEsito(esito) {
    if (esito === "PROMUOVI") return "badge-promuovi";
    if (esito === "DA APPROFONDIRE") return "badge-approfondire";
    if (esito === "MONITORA") return "badge-monitora";
    if (esito === "SCARTA") return "badge-scarta";
    return "badge-canale";
  }

  function badgeEsito(esito) {
    return el("span", { class: "badge " + classeBadgeEsito(esito) }, [esito || "n.d."]);
  }

  function badgeStatoSegnalazione(s) {
    var classe = s === "RISEGNALATA" ? "badge-risegnalata" : "badge-nuova";
    return el("span", { class: "badge " + classe }, [s === "RISEGNALATA" ? "Risegnalata" : "Nuova"]);
  }

  function badgeCanale(c) {
    return el("span", { class: "badge badge-canale" }, [c || "?"]);
  }

  function badgeStatoFonte(s) {
    return el("span", { class: "badge badge-stato-" + (s || "attesa") }, [ETICHETTE_STATO_FONTE[s] || s || "?"]);
  }

  // ------------------------------------------------------------------ navigazione schede

  function passaASchermo(nome, opzioni) {
    stato.schermo = nome;
    opzioni = opzioni || {};
    document.querySelectorAll(".schermo").forEach(function (s) {
      s.toggleAttribute("hidden", s.id !== "schermo-" + nome);
    });
    document.querySelectorAll("nav.schede button").forEach(function (b) {
      b.classList.toggle("attiva", b.dataset.schermo === nome);
    });
    if (nome === "risultati" && !opzioni.mantieniReport) {
      caricaUltimoReportEDisegna();
    }
    if (nome === "storico") {
      caricaStorico();
    }
  }

  document.querySelectorAll("nav.schede button").forEach(function (b) {
    b.addEventListener("click", function () { passaASchermo(b.dataset.schermo); });
  });

  // ------------------------------------------------------------------ schermata Home

  function disegnaHome(s) {
    var radice = document.getElementById("schermo-home");
    svuota(radice);

    if (s.simula_forzata) {
      radice.appendChild(el("div", { class: "avviso-banner" }, [
        "⚠️ Pannello avviato in modalità SIMULATA: premendo Avvia non verrà lanciato nessuno scraper reale né Claude Code, verranno usati i dati di prova.",
      ]));
    }

    var giorniFa = s.ultima_run ? s.ultima_run.giorni_fa : null;
    var sogliaPromemoria = s.giorni_promemoria || 7;
    if (giorniFa !== null && giorniFa !== undefined && giorniFa >= sogliaPromemoria) {
      radice.appendChild(el("div", { class: "avviso-banner" }, [
        "🔔 Sono passati " + giorniFa + " giorni dall'ultimo report: questa settimana manca ancora la scansione.",
      ]));
    } else if (giorniFa === null || giorniFa === undefined) {
      radice.appendChild(el("div", { class: "avviso-banner" }, [
        "Non risulta ancora nessun report: premi Avvia per generare il primo.",
      ]));
    }

    // --- card avvio ---
    var inCorso = s.lock_attivo;
    var cardAvvio = el("div", { class: "card" });
    cardAvvio.appendChild(el("h2", {}, ["Scansione settimanale"]));
    cardAvvio.appendChild(el("p", { class: "testo-attenuato" }, [
      "Avvia lo scraping completo di tutte le fonti (aste + idealista/immobiliare) e la valutazione con Claude Code. " +
      "L'operazione può durare da alcuni minuti a più di un'ora a seconda del numero di annunci.",
    ]));

    var rigaBottoni = el("div", { class: "riga-flessibile" });
    var bottoneAvvia = el("button", { class: "pulsante primario grande" }, [inCorso ? "Run già in corso..." : "▶ Avvia scansione settimanale"]);
    bottoneAvvia.disabled = inCorso;
    bottoneAvvia.onclick = function () { avviaRunDaHome(false); };
    rigaBottoni.appendChild(bottoneAvvia);

    if (inCorso) {
      var vaiAvanzamento = el("button", { class: "pulsante" }, ["Vedi avanzamento →"]);
      vaiAvanzamento.onclick = function () { passaASchermo("avanzamento"); };
      rigaBottoni.appendChild(vaiAvanzamento);
    }
    cardAvvio.appendChild(rigaBottoni);
    radice.appendChild(cardAvvio);

    // --- card valutazione fallita: riprova solo quella fase ---
    var run = s.run;
    if (!inCorso && run && run.fase === "errore" && run.fase_fallita === "valutazione") {
      var cardRiprova = el("div", { class: "card" });
      cardRiprova.appendChild(el("h2", {}, ["⚠️ L'ultima valutazione non è riuscita"]));
      cardRiprova.appendChild(el("p", { class: "testo-attenuato" }, [
        "Lo scraping era andato a buon fine, ma la valutazione (Claude Code) ha fallito. Puoi riprovare solo quel passaggio senza rifare tutto lo scraping.",
      ]));
      var bRiprova = el("button", { class: "pulsante primario" }, ["Riprova solo la valutazione"]);
      bRiprova.onclick = function () { riprovaValutazione(); };
      cardRiprova.appendChild(bRiprova);
      radice.appendChild(cardRiprova);
    }

    // --- card ultimo report ---
    var cardUltimo = el("div", { class: "card" });
    cardUltimo.appendChild(el("h2", {}, ["Ultimo report"]));
    if (!s.ultima_run) {
      cardUltimo.appendChild(el("p", { class: "vuoto" }, ["Nessun report ancora generato."]));
    } else {
      var u = s.ultima_run;
      cardUltimo.appendChild(el("p", { class: "testo-attenuato" }, [
        "Generato il " + fmtDataOra(u.generato_il) + (u.giorni_fa !== null && u.giorni_fa !== undefined ? " (" + u.giorni_fa + " giorni fa)" : ""),
      ]));
      var griglia = el("div", { class: "statistiche-griglia" });
      griglia.appendChild(el("div", { class: "statistica" }, [el("div", { class: "numero" }, [String(u.n_segnalate != null ? u.n_segnalate : "-")]), el("div", { class: "etichetta" }, ["segnalate"])]));
      griglia.appendChild(el("div", { class: "statistica" }, [el("div", { class: "numero" }, [String(u.n_nuove != null ? u.n_nuove : "-")]), el("div", { class: "etichetta" }, ["nuove"])]));
      griglia.appendChild(el("div", { class: "statistica" }, [el("div", { class: "numero" }, [String(u.n_risegnalate != null ? u.n_risegnalate : "-")]), el("div", { class: "etichetta" }, ["risegnalate"])]));
      cardUltimo.appendChild(griglia);

      if (u.top3 && u.top3.length) {
        cardUltimo.appendChild(el("h3", {}, ["Le prime 3 di questa settimana"]));
        var lista = el("div", { class: "top3-lista" });
        u.top3.forEach(function (o, i) {
          lista.appendChild(el("div", { class: "top3-riga" }, [
            el("span", { class: "posizione" }, [String(i + 1)]),
            el("span", { class: "comune" }, [o.comune || o.id || "?"]),
            badgeCanale(o.canale),
            el("span", { class: "voto" }, [o.voto != null ? String(o.voto) : "n.d."]),
            badgeEsito(o.esito),
          ]));
        });
        cardUltimo.appendChild(lista);
      }

      var bVediTutti = el("button", { class: "pulsante" }, ["Vedi tutti i risultati →"]);
      bVediTutti.onclick = function () { passaASchermo("risultati"); };
      cardUltimo.appendChild(el("div", { style: "margin-top: 12px" }, [bVediTutti]));
    }
    radice.appendChild(cardUltimo);
  }

  function avviaRunDaHome() {
    apiPost("/api/avvia", { simula: false, data: oggiIso() }).then(function (r) {
      if (r.ok) {
        mostraToast("Scansione avviata.", "successo");
        passaASchermo("avanzamento");
        aggiornaStatoENoti();
      } else {
        mostraToast(r.messaggio || "Impossibile avviare la scansione.", "errore");
      }
    }).catch(function (e) {
      mostraToast("Errore di comunicazione con il pannello: " + e.message, "errore");
    });
  }

  function riprovaValutazione() {
    apiPost("/api/riprova_valutazione", { simula: false }).then(function (r) {
      if (r.ok) {
        mostraToast("Nuovo tentativo di valutazione avviato.", "successo");
        passaASchermo("avanzamento");
        aggiornaStatoENoti();
      } else {
        mostraToast(r.messaggio || "Impossibile riavviare la valutazione.", "errore");
      }
    }).catch(function (e) {
      mostraToast("Errore di comunicazione con il pannello: " + e.message, "errore");
    });
  }

  // ------------------------------------------------------------------ schermata Avanzamento

  function disegnaAvanzamento(s) {
    var radice = document.getElementById("schermo-avanzamento");
    svuota(radice);

    var run = s.run;
    if (!run) {
      radice.appendChild(el("div", { class: "card" }, [el("p", { class: "vuoto" }, ["Nessuna scansione è mai stata avviata da questo pannello."])]));
      return;
    }

    var card = el("div", { class: "card" });
    var titolo = el("div", { class: "riga-flessibile" });
    titolo.appendChild(el("h2", {}, ["Avanzamento scansione del " + fmtData(run.data_run)]));
    if (s.lock_attivo) {
      var bInterrompi = el("button", { class: "pulsante pericolo" }, ["■ Interrompi"]);
      bInterrompi.onclick = function () { interrompiRun(); };
      titolo.appendChild(bInterrompi);
    }
    card.appendChild(titolo);

    if (run.simula) {
      card.appendChild(el("div", { class: "avviso-banner" }, ["Run in modalità simulata (dati di prova)."]));
    }

    // stepper
    var stepper = el("div", { class: "stepper" });
    var faseCorrenteIdx = PASSI_STEPPER.indexOf(run.fase);
    var faseFallitaIdx = run.fase_fallita ? PASSI_STEPPER.indexOf(run.fase_fallita) : -1;
    PASSI_STEPPER.forEach(function (nomeFase, i) {
      var classe = "passo";
      if (run.fase === "errore" && i === faseFallitaIdx) classe += " fallito";
      else if (run.fase === "errore" && i < faseFallitaIdx) classe += " fatto";
      else if (run.fase === "interrotta" && i <= faseCorrenteIdx) classe += " fallito";
      else if (i < faseCorrenteIdx || run.fase === "completata") classe += " fatto";
      else if (i === faseCorrenteIdx) classe += " attivo";
      stepper.appendChild(el("div", { class: classe }, [ETICHETTE_FASE[nomeFase] || nomeFase]));
    });
    card.appendChild(stepper);

    if (run.fase === "errore") {
      var banner = el("div", { class: "errore-banner" });
      banner.appendChild(el("strong", {}, ["Errore nella fase: " + (ETICHETTE_FASE[run.fase_fallita] || run.fase_fallita || "sconosciuta")]));
      if (run.errori && run.errori.length) {
        var lista = el("ul");
        run.errori.forEach(function (e) { lista.appendChild(el("li", {}, [String(e)])); });
        banner.appendChild(lista);
      }
      card.appendChild(banner);
    }
    if (run.fase === "interrotta") {
      card.appendChild(el("div", { class: "avviso-banner" }, ["Scansione interrotta su richiesta dell'utente."]));
    }
    if (run.fase === "completata") {
      card.appendChild(el("div", { class: "avviso-banner", style: "background:var(--ok-sfondo);color:var(--ok);border-color:var(--ok)" }, [
        "✓ Scansione completata. ",
      ]));
      var bVedi = el("button", { class: "pulsante primario" }, ["Vedi i risultati →"]);
      bVedi.onclick = function () { passaASchermo("risultati"); };
      card.appendChild(bVedi);
    }

    radice.appendChild(card);

    // colonne fonti
    if (run.scraper || run.immid) {
      var due = el("div", { class: "due-colonne" });
      due.appendChild(disegnaTabellaFonti("Portali d'asta (ASTE)", run.scraper));
      due.appendChild(disegnaTabellaFonti("Idealista / Immobiliare (IMM_ID)", run.immid));
      radice.appendChild(due);
    }

    if (run.valutazione) {
      var cardVal = el("div", { class: "card" });
      cardVal.appendChild(el("h3", {}, ["Valutazione (Claude Code)"]));
      cardVal.appendChild(badgeStatoFonte(run.valutazione.stato));
      radice.appendChild(cardVal);
    }

    // log
    var cardLog = el("div", { class: "card" });
    var dettagli = el("details", { class: "log-dettagli" });
    dettagli.open = stato.logAperto;
    dettagli.appendChild(el("summary", {}, ["Registro dettagliato (log)"]));
    var areaLog = el("div", { class: "log-area", id: "area-log" }, ["Caricamento..."]);
    dettagli.appendChild(areaLog);
    dettagli.addEventListener("toggle", function () {
      stato.logAperto = dettagli.open;
      if (dettagli.open) caricaLog(run.data_run);
    });
    cardLog.appendChild(dettagli);
    radice.appendChild(cardLog);
    // L'elemento e' ricreato ad ogni ridisegno (vedi svuota() sopra): se era
    // aperto prima del ridisegno, impostare .open sopra NON fa scattare
    // l'evento "toggle" (l'elemento e' appena stato creato, non ha cambiato
    // stato), quindi il log va ricaricato esplicitamente qui.
    if (stato.logAperto) caricaLog(run.data_run);
  }

  function disegnaTabellaFonti(titolo, fonti) {
    var card = el("div", { class: "card" });
    card.appendChild(el("h3", {}, [titolo]));
    if (!fonti || !Object.keys(fonti).length) {
      card.appendChild(el("p", { class: "vuoto" }, ["In attesa..."]));
      return card;
    }
    var tabella = el("table", { class: "tabella-fonti" });
    var thead = el("thead", {}, [el("tr", {}, [el("th", {}, ["Fonte"]), el("th", {}, ["Stato"]), el("th", {}, ["Annunci"])])]);
    tabella.appendChild(thead);
    var tbody = el("tbody");
    Object.keys(fonti).forEach(function (nome) {
      var f = fonti[nome];
      var tr = el("tr", {}, [
        el("td", {}, [nome]),
        el("td", {}, [badgeStatoFonte(f.stato)]),
        el("td", {}, [f.annunci_trovati != null ? fmtNum(f.annunci_trovati) : "–"]),
      ]);
      tbody.appendChild(tr);
    });
    tabella.appendChild(tbody);
    card.appendChild(tabella);
    return card;
  }

  function caricaLog(runId) {
    var area = document.getElementById("area-log");
    if (!area) return;
    // Il log si ricarica ogni pochi secondi mentre il riquadro e' aperto: se
    // l'utente era gia' in fondo (o e' il primo caricamento) lo si segue
    // automaticamente, altrimenti si rispetta la posizione a cui ha scorso
    // per leggere le righe precedenti, senza forzarlo in fondo ad ogni giro.
    var eraInFondo = area.scrollHeight - area.scrollTop - area.clientHeight < 40;
    apiGet("/api/log?run=" + encodeURIComponent(runId)).then(function (r) {
      area.textContent = r.log || "(registro vuoto)";
      if (eraInFondo) area.scrollTop = area.scrollHeight;
    }).catch(function () {
      area.textContent = "Impossibile leggere il registro.";
    });
  }

  function interrompiRun() {
    chiediConferma("Interrompere la scansione?", "I processi in corso verranno terminati. I dati già scaricati non andranno persi.", "Interrompi").then(function (ok) {
      if (!ok) return;
      apiPost("/api/interrompi", {}).then(function (r) {
        mostraToast(r.messaggio || (r.ok ? "Interruzione richiesta." : "Errore."), r.ok ? "successo" : "errore");
      }).catch(function (e) {
        mostraToast("Errore di comunicazione: " + e.message, "errore");
      });
    });
  }

  // ------------------------------------------------------------------ schermata Risultati

  function caricaUltimoReportEDisegna() {
    var radice = document.getElementById("schermo-risultati");
    svuota(radice);
    radice.appendChild(el("p", { class: "vuoto" }, ["Caricamento..."]));

    apiGet("/api/stato").then(function (s) {
      if (!s.ultima_run) {
        svuota(radice);
        radice.appendChild(el("div", { class: "card" }, [el("p", { class: "vuoto" }, ["Nessun report ancora disponibile. Avvia una scansione dalla Home."])]));
        return;
      }
      caricaEDisegnaReport(s.ultima_run.file);
    }).catch(function (e) {
      svuota(radice);
      radice.appendChild(el("div", { class: "errore-banner" }, ["Impossibile contattare il pannello: " + e.message]));
    });
  }

  function caricaEDisegnaReport(nomeFile) {
    var radice = document.getElementById("schermo-risultati");
    apiGet("/api/report?file=" + encodeURIComponent(nomeFile)).then(function (report) {
      stato.reportCorrente = report;
      stato.reportCorrenteFile = nomeFile;
      disegnaReport(report);
    }).catch(function (e) {
      svuota(radice);
      radice.appendChild(el("div", { class: "errore-banner" }, ["Impossibile leggere il report: " + e.message]));
    });
  }

  function disegnaReport(report) {
    var radice = document.getElementById("schermo-risultati");
    svuota(radice);
    var meta = report.meta || {};
    var stat = meta.statistiche || {};

    var cardIntestazione = el("div", { class: "card" });
    var riga = el("div", { class: "report-intestazione" });
    riga.appendChild(el("div", {}, [
      el("h2", {}, ["Report del " + fmtData(meta.report_id ? meta.report_id.split("_").reverse().join("/") : null) + " — " + (meta.file || "")]),
      el("p", { class: "testo-attenuato" }, ["Generato il " + fmtDataOra(meta.generato_il)]),
    ]));
    var bStorico = el("button", { class: "pulsante" }, ["Sfoglia altri report →"]);
    bStorico.onclick = function () { passaASchermo("storico"); };
    riga.appendChild(bStorico);
    cardIntestazione.appendChild(riga);

    if (meta.note && meta.note.length) {
      meta.note.forEach(function (n) {
        cardIntestazione.appendChild(el("div", { class: "nota-fallback" }, ["ℹ️ " + n]));
      });
    }

    var seg = stat.segnalate || {};
    var griglia = el("div", { class: "statistiche-griglia" });
    griglia.appendChild(el("div", { class: "statistica" }, [el("div", { class: "numero" }, [String(seg.totale != null ? seg.totale : "-")]), el("div", { class: "etichetta" }, ["segnalate"])]));
    griglia.appendChild(el("div", { class: "statistica" }, [el("div", { class: "numero" }, [String(seg.nuove != null ? seg.nuove : "-")]), el("div", { class: "etichetta" }, ["nuove"])]));
    griglia.appendChild(el("div", { class: "statistica" }, [el("div", { class: "numero" }, [String(seg.risegnalate != null ? seg.risegnalate : "-")]), el("div", { class: "etichetta" }, ["risegnalate"])]));
    griglia.appendChild(el("div", { class: "statistica" }, [el("div", { class: "numero" }, [String(stat.record_letti != null ? stat.record_letti : "-")]), el("div", { class: "etichetta" }, ["annunci esaminati"])]));
    cardIntestazione.appendChild(griglia);
    radice.appendChild(cardIntestazione);

    var opp = report.opportunita || [];
    if (!opp.length) {
      radice.appendChild(el("div", { class: "card" }, [el("p", { class: "vuoto" }, ["Nessuna opportunità segnalata in questo report."])]));
    } else {
      opp.forEach(function (o) { radice.appendChild(disegnaCardOpportunita(o)); });
    }

    // scarti per motivo + monitora, in dettaglio pieghevole
    if (stat.scarti_per_motivo || (report.monitora && report.monitora.length)) {
      var cardDettaglio = el("div", { class: "card" });
      var dettagli = el("details");
      dettagli.appendChild(el("summary", { style: "cursor:pointer;font-weight:600" }, [
        "Altre " + ((stat.scarti_per_motivo ? Object.values(stat.scarti_per_motivo).reduce(function (a, b) { return a + b; }, 0) : 0)) + " scartate e " + ((report.monitora || []).length) + " da monitorare",
      ]));
      if (stat.scarti_per_motivo) {
        dettagli.appendChild(el("h3", { style: "margin-top:12px" }, ["Motivi di scarto"]));
        var tabMot = el("table", { class: "tabella-fonti" });
        var tbody = el("tbody");
        Object.keys(stat.scarti_per_motivo).forEach(function (m) {
          tbody.appendChild(el("tr", {}, [el("td", {}, [m]), el("td", {}, [String(stat.scarti_per_motivo[m])])]));
        });
        tabMot.appendChild(tbody);
        dettagli.appendChild(tabMot);
      }
      if (report.monitora && report.monitora.length) {
        dettagli.appendChild(el("h3", { style: "margin-top:12px" }, ["Da monitorare (" + report.monitora.length + ")"]));
        report.monitora.slice(0, 30).forEach(function (m) {
          dettagli.appendChild(el("p", { style: "font-size:13px;margin:4px 0" }, [
            (m.id || "?") + " — " + ((m.snapshot || {}).comune || "comune n.d.") + " — " + (m.motivo || ""),
          ]));
        });
      }
      cardDettaglio.appendChild(dettagli);
      radice.appendChild(cardDettaglio);
    }
  }

  function disegnaCardOpportunita(o) {
    var snap = o.snapshot || {};
    var card = el("div", { class: "opportunita-card" });

    var testata = el("div", { class: "opportunita-testata" });
    testata.appendChild(el("span", { class: "posizione" }, ["#" + o.posizione]));
    testata.appendChild(el("span", { class: "comune" }, [(snap.comune || "Comune n.d.") + (snap.provincia ? " (" + snap.provincia + ")" : "")]));
    testata.appendChild(badgeCanale(o.canale));
    testata.appendChild(badgeStatoSegnalazione(o.stato_segnalazione));
    testata.appendChild(badgeEsito(o.esito));
    testata.appendChild(el("span", { class: "opportunita-voto" }, [o.voto != null ? String(o.voto) : "n.d."]));
    card.appendChild(testata);

    var dati = el("div", { class: "opportunita-dati" });
    function campo(etichetta, valore) {
      dati.appendChild(el("div", {}, [el("div", { class: "etichetta" }, [etichetta]), el("div", { class: "valore" }, [valore])]));
    }
    campo("Prezzo", fmtEur(snap.prezzo_eur));
    campo("Superficie", fmtMq(snap.superficie_mq));
    campo("Tipologia", snap.categoria || "n.d.");
    if (snap.scadenza_asta) campo("Scadenza asta", fmtData(snap.scadenza_asta));
    if (snap.tribunale) campo("Tribunale", snap.tribunale);
    card.appendChild(dati);

    if (o.flag && o.flag.length) {
      var chip = el("div", { class: "chip-riga" });
      o.flag.forEach(function (f) { chip.appendChild(el("span", { class: "chip" }, [f])); });
      card.appendChild(chip);
    }

    if (o.stato_segnalazione === "RISEGNALATA" && o.motivi_risegnalazione && o.motivi_risegnalazione.length) {
      card.appendChild(el("div", { class: "motivi-risegnalazione" }, ["↑ Risegnalata perché: " + o.motivi_risegnalazione.join("; ")]));
    }

    if (o.valutazione_testuale) {
      var dettagli = el("details", { class: "valutazione-testuale" });
      dettagli.appendChild(el("summary", {}, ["Leggi la valutazione completa"]));
      dettagli.appendChild(el("div", { class: "testo" }, [o.valutazione_testuale]));
      card.appendChild(dettagli);
    }

    if (o.url) {
      card.appendChild(el("div", { style: "margin-top:8px" }, [
        el("a", { class: "link-annuncio", href: o.url, target: "_blank", rel: "noopener noreferrer" }, ["Apri annuncio originale ↗"]),
      ]));
    }

    return card;
  }

  // ------------------------------------------------------------------ schermata Storico

  function caricaStorico() {
    var radice = document.getElementById("schermo-storico");
    svuota(radice);

    var cardRicerca = el("div", { class: "card" });
    cardRicerca.appendChild(el("h2", {}, ["Cerca nello storico"]));
    var campo = el("input", { class: "campo-ricerca", type: "text", placeholder: "Cerca per comune o id annuncio..." });
    cardRicerca.appendChild(campo);
    var risultatiRicerca = el("div", { style: "margin-top:12px" });
    cardRicerca.appendChild(risultatiRicerca);
    radice.appendChild(cardRicerca);

    var timer = null;
    campo.addEventListener("input", function () {
      clearTimeout(timer);
      var q = campo.value.trim();
      if (!q) { svuota(risultatiRicerca); return; }
      timer = setTimeout(function () {
        apiGet("/api/cerca?q=" + encodeURIComponent(q)).then(function (r) {
          disegnaRisultatiRicerca(risultatiRicerca, r.risultati || []);
        }).catch(function () {
          svuota(risultatiRicerca);
          risultatiRicerca.appendChild(el("p", { class: "vuoto" }, ["Ricerca non riuscita."]));
        });
      }, 250);
    });

    var cardElenco = el("div", { class: "card" });
    cardElenco.appendChild(el("h2", {}, ["Report precedenti"]));
    var contenitoreTabella = el("div", { id: "storico-tabella" }, [el("p", { class: "vuoto" }, ["Caricamento..."])]);
    cardElenco.appendChild(contenitoreTabella);
    radice.appendChild(cardElenco);

    apiGet("/api/storico").then(function (r) {
      stato.storicoCache = r.report || [];
      disegnaTabellaStorico(contenitoreTabella, stato.storicoCache);
    }).catch(function () {
      svuota(contenitoreTabella);
      contenitoreTabella.appendChild(el("p", { class: "vuoto" }, ["Impossibile leggere lo storico."]));
    });
  }

  function disegnaRisultatiRicerca(contenitore, risultati) {
    svuota(contenitore);
    if (!risultati.length) {
      contenitore.appendChild(el("p", { class: "vuoto" }, ["Nessun risultato."]));
      return;
    }
    var tabella = el("table", { class: "tabella-storico" });
    tabella.appendChild(el("thead", {}, [el("tr", {}, [
      el("th", {}, ["Comune"]), el("th", {}, ["Id"]), el("th", {}, ["Data report"]), el("th", {}, ["Voto"]), el("th", {}, ["Esito"]),
    ])]));
    var tbody = el("tbody");
    risultati.forEach(function (r) {
      var tr = el("tr", {}, [
        el("td", {}, [r.comune || "n.d."]),
        el("td", {}, [r.id || "?"]),
        el("td", {}, [fmtData(r.generato_il)]),
        el("td", {}, [r.voto != null ? String(r.voto) : "n.d."]),
        el("td", {}, [badgeEsito(r.esito)]),
      ]);
      tr.onclick = function () {
        caricaEDisegnaReport(r.file);
        passaASchermo("risultati", { mantieniReport: true });
      };
      tbody.appendChild(tr);
    });
    tabella.appendChild(tbody);
    contenitore.appendChild(tabella);
  }

  function disegnaTabellaStorico(contenitore, elenco) {
    svuota(contenitore);
    if (!elenco.length) {
      contenitore.appendChild(el("p", { class: "vuoto" }, ["Nessun report ancora generato."]));
      return;
    }
    var tabella = el("table", { class: "tabella-storico" });
    tabella.appendChild(el("thead", {}, [el("tr", {}, [
      el("th", {}, ["Data"]), el("th", {}, ["Segnalate"]), el("th", {}, ["Nuove"]), el("th", {}, ["Risegnalate"]),
    ])]));
    var tbody = el("tbody");
    elenco.forEach(function (r) {
      var tr = el("tr", {}, [
        el("td", {}, [fmtDataOra(r.generato_il)]),
        el("td", {}, [r.n_segnalate != null ? String(r.n_segnalate) : "n.d."]),
        el("td", {}, [r.n_nuove != null ? String(r.n_nuove) : "n.d."]),
        el("td", {}, [r.n_risegnalate != null ? String(r.n_risegnalate) : "n.d."]),
      ]);
      tr.onclick = function () {
        caricaEDisegnaReport(r.file);
        passaASchermo("risultati", { mantieniReport: true });
      };
      tbody.appendChild(tr);
    });
    tabella.appendChild(tbody);
    contenitore.appendChild(tabella);
  }

  // ------------------------------------------------------------------ ciclo di aggiornamento

  function aggiornaStatoENoti() {
    apiGet("/api/stato").then(function (s) {
      stato.ultimoStatoApi = s;
      aggiornaIndicatoreScheda(s);
      var badgeSimula = document.getElementById("badge-simula");
      if (badgeSimula) badgeSimula.toggleAttribute("hidden", !s.simula_forzata);
      if (stato.schermo === "home") disegnaHome(s);
      if (stato.schermo === "avanzamento") disegnaAvanzamento(s);
    }).catch(function () {
      // silenzioso: il pannello riproverà al prossimo giro
    });
  }

  function aggiornaIndicatoreScheda(s) {
    var bottoneAvanzamento = document.querySelector('nav.schede button[data-schermo="avanzamento"]');
    if (!bottoneAvanzamento) return;
    var esistente = bottoneAvanzamento.querySelector(".pallino");
    if (s.lock_attivo) {
      if (!esistente) bottoneAvanzamento.appendChild(el("span", { class: "pallino" }));
    } else if (esistente) {
      esistente.remove();
    }
  }

  // ------------------------------------------------------------------ avvio

  function avvia() {
    passaASchermo("home");
    aggiornaStatoENoti();
    setInterval(aggiornaStatoENoti, INTERVALLO_POLL_MS);
  }

  document.addEventListener("DOMContentLoaded", avvia);
})();
