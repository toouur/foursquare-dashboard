[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$python  = "C:/Users/toouur/AppData/Local/Programs/Python/Python312/python.exe"
$script  = "$PSScriptRoot/enrich_overlaps.py"
$csv     = "G:/FoursquareDashboardClaude/local_parsing/checkins.csv"
$logFile = "G:/FoursquareDashboardClaude/local_parsing/enrich_log_$(Get-Date -Format 'yyyyMMdd_HHmmss').txt"

Write-Host "Log: $logFile"
Write-Host "Starting..."

& $python -u $script --csv $csv 2>&1 | ForEach-Object {
    $line = $_ -replace "`r", ""
    Write-Host $line
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

Write-Host "Done. Log saved to $logFile"
