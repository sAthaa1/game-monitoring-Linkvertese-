@echo off
title Bot Setup
echo ================================
echo  Bot Setup Script
echo ================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [*] Installing Python...
    powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.0/python-3.12.0-amd64.exe' -OutFile '%TEMP%\python_installer.exe'"
    %TEMP%\python_installer.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
    echo [+] Python installed. Reopen this script after install finishes.
    pause
    exit
) else (
    echo [+] Python found.
)

:: Check if Git is installed
git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [*] Installing Git...
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/git-for-windows/git/releases/download/v2.44.0.windows.1/Git-2.44.0-64-bit.exe' -OutFile '%TEMP%\git_installer.exe'"
    %TEMP%\git_installer.exe /VERYSILENT /NORESTART
    echo [+] Git installed.
) else (
    echo [+] Git found.
)

:: Clone repo if not already cloned
if not exist "game-monitoring-Linkvertese-" (
    echo [*] Cloning repo...
    git clone https://github.com/sAthaa1/game-monitoring-Linkvertese-
    echo [+] Repo cloned.
) else (
    echo [+] Repo already exists, pulling latest...
    cd game-monitoring-Linkvertese-
    git pull
    cd ..
)

cd game-monitoring-Linkvertese-

:: Install dependencies
echo [*] Installing dependencies...
pip install -r requirements.txt -q
echo [+] Dependencies installed.

:: Create .env if missing
if not exist ".env" (
    copy .env.example .env
    echo.
    echo [!] .env file created. Please fill in your values now.
    echo     Opening .env in notepad...
    notepad .env
    echo.
    echo     Press any key after saving .env to start the bot.
    pause
)

:: Run the bot with auto-restart
echo.
echo [+] Starting bot...
echo     Press Ctrl+C to stop.
echo.
:loop
python main.py
echo.
echo [!] Bot stopped. Restarting in 5 seconds... (Ctrl+C to cancel)
timeout /t 5
goto loop
