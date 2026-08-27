@echo off
setlocal

if defined PYTHONPATH (
    set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%~dp0src"
)
if not defined TOKENSHARE_EXPERIMENTS_PYTHON set "TOKENSHARE_EXPERIMENTS_PYTHON=python"
"%TOKENSHARE_EXPERIMENTS_PYTHON%" -m tokenshare.experiments.gui %*
set "TOKENSHARE_EXPERIMENTS_EXIT=%ERRORLEVEL%"
exit /b %TOKENSHARE_EXPERIMENTS_EXIT%
