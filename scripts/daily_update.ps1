$ErrorActionPreference = "Stop"

$Repo = "C:\Users\av_ch\Documents\GitHub\hsi-dashboard"
$PyScript = Join-Path $Repo "scripts\update_hsi5f.py"
$LogDir = Join-Path $Repo "logs"
$LogFile = Join-Path $LogDir "daily_update.log"

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Write-Log {
  param([string]$Msg)
  $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $Msg"
  $line | Tee-Object -FilePath $LogFile -Append
}

function Invoke-Checked {
  param(
    [string]$Exe,
    [string[]]$CmdArgs
  )
  & $Exe @CmdArgs
  if ($LASTEXITCODE -ne 0) {
    throw "$Exe $($CmdArgs -join ' ') failed with exit code $LASTEXITCODE"
  }
}

Write-Log "=== Daily update start ==="

Push-Location $Repo
try {
  Invoke-Checked -Exe "git" -CmdArgs @("pull", "--rebase", "origin", "main")

  & python $PyScript
  if ($LASTEXITCODE -ne 0) {
    throw "Python update script failed with exit code $LASTEXITCODE"
  }

  git add -- "data/hsi5f.csv"
  git diff --cached --quiet
  if ($LASTEXITCODE -eq 0) {
    Write-Log "No data change. Skip commit/push."
    exit 0
  }

  $msg = "Daily data update: $(Get-Date -Format 'yyyy-MM-dd')"
  Invoke-Checked -Exe "git" -CmdArgs @("commit", "-m", $msg)
  Invoke-Checked -Exe "git" -CmdArgs @("push", "origin", "main")

  Write-Log "Commit + push completed."
}
catch {
  Write-Log "ERROR: $($_.Exception.Message)"
  exit 1
}
finally {
  Pop-Location
  Write-Log "=== Daily update end ===`n"
}
