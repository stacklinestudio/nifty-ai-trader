# Brief 13 Part 2: real daily wrapper for the "NiftyAITrader-InstrumentArchive"
# Windows Scheduled Task. Runs `python main.py instruments`, which fails
# closed (data/instrument_archive.py::run_daily_archive) on a day nobody
# has completed that day's real Kite login yet -- this wrapper's only job
# is to run it with the right working directory/interpreter and keep a
# real log of each real attempt, not to add any new logic of its own.

Set-Location "C:\Users\prasanth\Desktop\nifty-ai-trader"
$logDir = "data\private\instrument_archives\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("run_{0:yyyy-MM-dd_HHmmss}.log" -f (Get-Date))
& ".venv\Scripts\python.exe" "main.py" "instruments" 2>&1 | Out-File -FilePath $logFile -Encoding utf8
