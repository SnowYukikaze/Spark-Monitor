@echo off
rem ============================================================
rem  Spark Monitor launcher
rem
rem  IMPORTANT: keep this file ASCII-only and CRLF-terminated.
rem  cmd.exe reads .bat files as ANSI(GBK); non-ASCII comments
rem  can break the parser, and the window closes with no effect.
rem ============================================================
setlocal
cd /d "%~dp0"

set "APP=%~dp0my_devices.py"
if not exist "%APP%" goto noapp

set "PICK="
set "PICK_A="

rem ---- 1) pythonw.exe already on PATH -----------------------
set "PW="
for %%D in (pythonw.exe) do if not defined PW set "PW=%%~$PATH:D"
if defined PW call :probe "%PW%"

rem ---- 2) py launcher (pyw.exe -3) --------------------------
if not defined PICK call :probe_launcher

rem ---- 3) common install locations, newest first ------------
if not defined PICK call :probe_common

if not defined PICK goto nopython

if defined PICK_A (
    start "" "%PICK%" %PICK_A% "%APP%"
) else (
    start "" "%PICK%" "%APP%"
)
exit /b 0


rem ------------------------------------------------------------
rem %1 = path to pythonw.exe; its sibling python.exe is used to
rem verify that this interpreter can actually import tkinter.
:probe
set "PWX=%~1"
set "PYX=%PWX:pythonw.exe=python.exe%"
if not exist "%PYX%" exit /b 0
"%PYX%" -c "import tkinter" >nul 2>&1
if errorlevel 1 exit /b 0
set "PICK=%PWX%"
exit /b 0

:probe_launcher
set "PYW="
for %%D in (pyw.exe) do if not defined PYW set "PYW=%%~$PATH:D"
if not defined PYW exit /b 0
py -3 -c "import tkinter" >nul 2>&1
if errorlevel 1 exit /b 0
set "PICK=%PYW%"
set "PICK_A=-3"
exit /b 0

:probe_common
for %%V in (314 313 312 311 310) do (
    if not defined PICK if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\pythonw.exe" (
        "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" -c "import tkinter" >nul 2>&1
        if not errorlevel 1 set "PICK=%LOCALAPPDATA%\Programs\Python\Python%%V\pythonw.exe"
    )
)
exit /b 0


:noapp
echo.
echo  [ERROR] my_devices.py was not found next to this script.
echo.
echo  Unzip the whole package first, and keep my_devices.py,
echo  the ez24g folder and this .bat file together.
echo.
pause
exit /b 1

:nopython
echo.
echo  [ERROR] No usable Python 3.10+ (with tkinter) was found.
echo.
echo  Option 1 - install Python:
echo      https://www.python.org/downloads/
echo      During setup tick "Add python.exe to PATH"
echo      and keep "tcl/tk and IDLE" enabled.
echo.
echo  Option 2 - skip Python entirely:
echo      use the prebuilt SparkMonitor.exe instead.
echo.
pause
exit /b 1
