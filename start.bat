@echo off
setlocal
set "PYTHONUTF8=1"
pushd "%~dp0"
if errorlevel 1 goto folder_error

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "scripts\launch.py"
  goto finished
)

py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 (
  py -3 "scripts\launch.py"
  goto finished
)

python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 (
  python "scripts\launch.py"
  goto finished
)

echo Python 3.11 or newer was not found.
echo Install 64-bit Python from https://www.python.org/downloads/windows/
echo Enable the Python launcher or Add Python to PATH, then run start.bat again.
set "KNIGHTLAB_EXIT_CODE=1"
goto close

:finished
set "KNIGHTLAB_EXIT_CODE=%ERRORLEVEL%"
if not "%KNIGHTLAB_EXIT_CODE%"=="0" echo Knightlab stopped with an error. See the message above.

:close
popd
pause
exit /b %KNIGHTLAB_EXIT_CODE%

:folder_error
echo The project folder could not be opened. Extract the ZIP before starting Knightlab.
pause
exit /b 1
