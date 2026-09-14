# Forte Bid Desk runner for PowerShell.
# Sets PYTHONPATH, loads ANTHROPIC_API_KEY from the out-of-repo credentials file
# (only needed for UNCACHED Claude calls), then runs whatever you pass.
#
#   .\run.ps1 serve.py                  # -> http://localhost:8000
#   .\run.ps1 serve.py --precompute     # cache estimates + routing metrics
#   .\run.ps1 -m pytest -q              # run the test suite
$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "src"

$cred = Join-Path $env:LOCALAPPDATA "forte\credentials.env"
if (Test-Path $cred) {
    Get-Content $cred | ForEach-Object {
        if ($_ -match '^\s*([^#=]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim())
        }
    }
}

python @args
