@echo off
setlocal

set "TOKENSHARE_SLIM_V2_REPO=%~dp0"
set "PYTHONPATH=%~dp0src"

where conda >nul 2>&1
if errorlevel 1 (
    echo ERROR: conda was not found on PATH.
    exit /b 1
)

conda run --no-capture-output -n tokenshare python -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo ERROR: the conda environment 'tokenshare' is missing or unusable.
    exit /b 1
)

conda run --no-capture-output -n tokenshare python -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Tkinter is unavailable in the 'tokenshare' environment.
    exit /b 1
)

conda run --no-capture-output -n tokenshare python -m tokenshare.experiments.slim_v2.gui
set "TOKENSHARE_SLIM_V2_EXIT=%ERRORLEVEL%"
if not "%TOKENSHARE_SLIM_V2_EXIT%"=="0" echo ERROR: Slim V2 GUI exited with code %TOKENSHARE_SLIM_V2_EXIT%.
exit /b %TOKENSHARE_SLIM_V2_EXIT%
