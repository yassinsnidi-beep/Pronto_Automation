@echo off
REM ============================================================
REM run_tutti_gli_scraper.bat
REM ============================================================
REM Lancia la run completa (36 citta) di tutti i 10 scraper.
REM
REM Strategia:
REM   1. Gli 8 script "veloci" (solo requests, nessun browser) partono
REM      TUTTI INSIEME in finestre minimizzate separate: sono siti
REM      diversi, nessun conflitto, e finiscono comunque prima.
REM   2. I 2 script "pesanti" (Idealista, Immobiliare - usano un vero
REM      browser Chrome) girano IN SEQUENZA in questa stessa finestra,
REM      per non sovraccaricare il PC con troppi Chrome insieme.
REM
REM Ogni script scrive comunque il proprio log dedicato
REM (es. scraper_idealista.log) indipendentemente da questa finestra.
REM ============================================================

cd /d "%~dp0"

echo ============================================================
echo   RUN COMPLETA SU 36 CITTA - inizio: %date% %time%
echo ============================================================
echo.

echo [1/2] Avvio in background gli 8 script veloci...
REM NOTA: FallcoAste, Astalegale e WorldCapital usano --no-resume perche' i
REM dati di test salvati PRIMA dei fix di qualita' (mq/prezzo/perizia mancanti)
REM andrebbero altrimenti saltati dal resume invece che sostituiti con la
REM versione completa. Gli altri script tengono il resume normale: i dati di
REM test gia' salvati sono corretti, la run completa aggiunge solo il resto.
start /min "Asteannunci"        cmd /c "python scraper_asteannunci_master.py"
start /min "Medianord"          cmd /c "python scraper_medianord_master.py"
start /min "FallcoAste"         cmd /c "python scraper_fallcoaste_master.py --no-resume"
start /min "Asteflorio"         cmd /c "python scraper_asteflorio_master.py"
start /min "WorldCapital"       cmd /c "python scraper_worldcapital_master.py --no-resume"
start /min "Astalegale"         cmd /c "python scraper_astalegale_master.py --no-resume"
start /min "CaseAsta"           cmd /c "python scraper_caseasta_master.py"
start /min "VenditeGiudiziarie" cmd /c "python scraper_venditegiudiziarie_master.py"

echo     -^> Partiti in 8 finestre minimizzate (guarda la barra delle applicazioni).
echo     -^> Ognuno scrive il proprio file scraper_*.log in questa cartella.
echo.

echo [2/2] Avvio in sequenza i 2 script piu pesanti (Idealista, Immobiliare)...
echo     Restano in questa finestra, richiedono piu tempo.
echo.

echo --- IDEALISTA (headless, max 60 pagine per citta/categoria) ---
python scraper_idealista_master.py --headless=true --max-pages 60
echo.

echo --- IMMOBILIARE (headless, max 60 pagine per citta/categoria) ---
python scraper_immobiliare_master.py --headless=true --max-pages 60
echo.

echo ============================================================
echo   RUN COMPLETATA - fine: %date% %time%
echo   Controlla i file in ..\opportunities\INPUT\ASTE\ (aste) e ..\opportunities\INPUT\IMM_ID\ (idealista/immobiliare), e i log scraper_*.log
echo   (verifica anche le 8 finestre minimizzate: si chiudono da sole
echo    a lavoro finito, se sono ancora aperte stanno ancora lavorando)
echo ============================================================
pause
