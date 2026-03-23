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
  Write-Log "RUN: $Exe $($CmdArgs -join ' ')"
  $prevErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  $hasNativePref = $null -ne (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue)
  if ($hasNativePref) {
    $prevNativePref = $PSNativeCommandUseErrorActionPreference
    $PSNativeCommandUseErrorActionPreference = $false
  }

  try {
    $output = & $Exe @CmdArgs 2>&1
    $exitCode = $LASTEXITCODE
  }
  finally {
    $ErrorActionPreference = $prevErrorActionPreference
    if ($hasNativePref) {
      $PSNativeCommandUseErrorActionPreference = $prevNativePref
    }
  }

  foreach ($line in $output) {
    Write-Log "  $line"
  }

  if ($exitCode -ne 0) {
    throw "$Exe $($CmdArgs -join ' ') failed with exit code $exitCode"
  }
}

function Invoke-PythonUpdate {
  param([string]$ScriptPath)

  if (Get-Command py -ErrorAction SilentlyContinue) {
    Invoke-Checked -Exe "py" -CmdArgs @("-3", $ScriptPath)
    return
  }

  Invoke-Checked -Exe "python" -CmdArgs @($ScriptPath)
}

Write-Log "=== Daily update start ==="

Push-Location $Repo
try {
  Invoke-Checked -Exe "git" -CmdArgs @("-c", "rebase.autoStash=true", "pull", "--rebase", "origin", "main")
  Invoke-PythonUpdate -ScriptPath $PyScript

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
