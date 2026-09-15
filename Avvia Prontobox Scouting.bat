@echo off
setlocal
title Prontobox Scouting - Pannello settimanale
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    set "PYEXE=python"
    goto :trovato
)

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYEXE=py"
    goto :trovato
)

echo.
echo ============================================================
echo   ERRORE: non trovo Python installato su questo PC.
echo   Installa Python da https://www.python.org/downloads/
echo   (durante l'installazione spunta "Add python.exe to PATH")
echo   e poi fai doppio clic di nuovo su questo file.
echo ============================================================
echo.
pause
exit /b 1

:trovato
if not exist ".claude\skills\prontobox-scouting\scout.py" (
    echo.
    echo ============================================================
    echo   ERRORE: non trovo la skill prontobox-scouting in questa
    echo   cartella. Sposta questo file dentro la cartella
    echo   Pronto_Automation, accanto alla cartella "pannello".
    echo ============================================================
    echo.
    pause
    exit /b 1
)

%PYEXE% -c "import yaml" >nul 2>nul
if not %errorlevel%==0 (
    echo.
    echo ============================================================
    echo   Prima esecuzione: installo una libreria necessaria (PyYAML^).
    echo   Serve solo una volta, ci vogliono pochi secondi...
    echo ============================================================
    echo.
    %PYEXE% -m pip install --quiet --disable-pip-version-check pyyaml
    %PYEXE% -c "import yaml" >nul 2>nul
    if not %errorlevel%==0 (
        REM Su alcuni PC l'installazione "di sistema" richiede permessi da
        REM amministratore: si ritenta con --user, che non li richiede.
        %PYEXE% -m pip install --quiet --disable-pip-version-check --user pyyaml
        %PYEXE% -c "import yaml" >nul 2>nul
    )
    if not %errorlevel%==0 (
        echo.
        echo ============================================================
        echo   ERRORE: non sono riuscito a installare PyYAML da solo
        echo   (forse manca la connessione a Internet, o pip non e'
        echo   disponibile^). Prova ad aprire il Prompt dei comandi in
        echo   questa cartella ed eseguire a mano:
        echo.
        echo       %PYEXE% -m pip install pyyaml
        echo.
        echo   poi fai doppio clic di nuovo su questo file.
        echo ============================================================
        echo.
        pause
        exit /b 1
    )
    echo Fatto.
    echo.
)

echo ============================================================
echo   Prontobox Scouting - avvio del pannello settimanale
echo ============================================================
echo.
echo Tra pochi secondi il pannello si aprira' nel browser.
echo NON chiudere questa finestra finche' stai usando il pannello:
echo chiuderla interrompe anche il pannello.
echo.
echo Per chiudere il pannello: chiudi questa finestra, oppure premi
echo CTRL+C qui dentro.
echo.

%PYEXE% pannello\pannello.py

echo.
echo Il pannello si e' chiuso.
pause
