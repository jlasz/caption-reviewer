@echo off
setlocal
if "%~1"=="" (
  echo Drag your dataset folder onto this launcher, or run:
  echo   run_caption_reviewer.bat "path\to\dataset"
  pause
  exit /b 1
)
py -3 "%~dp0caption_reviewer.py" "%~f1\."
if errorlevel 1 (
  echo.
  echo Caption Reviewer could not start. Make sure Python 3.10 or newer is installed.
  pause
)
