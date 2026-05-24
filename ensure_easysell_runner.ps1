$ErrorActionPreference = "Stop"

$runnerRoot = $env:EASYSELL_RUNNER_ROOT
if ([string]::IsNullOrWhiteSpace($runnerRoot)) {
  $runnerRoot = "C:\Users\Pc\actions-runner"
}

if (-not (Test-Path -LiteralPath $runnerRoot)) {
  throw "Runner root not found: $runnerRoot"
}

$runnerRoot = (Resolve-Path -LiteralPath $runnerRoot).ProviderPath
$runnerBin = Join-Path $runnerRoot "bin"
$runCmd = Join-Path $runnerRoot "run.cmd"

if (-not (Test-Path -LiteralPath $runCmd)) {
  throw "Runner command not found: $runCmd"
}

function Get-RunnerListener {
  @(
    Get-Process -Name "Runner.Listener" -ErrorAction SilentlyContinue |
      Where-Object {
        try {
          $_.Path -and $_.Path.StartsWith($runnerBin, [System.StringComparison]::OrdinalIgnoreCase)
        } catch {
          $true
        }
      }
  )
}

$listener = Get-RunnerListener

if ($listener.Count -gt 0) {
  Write-Host "EasySell GitHub runner already running: $($listener[0].Id)"
  exit 0
}

Write-Host "Starting EasySell GitHub runner from $runnerRoot"
Start-Process -FilePath $runCmd -WorkingDirectory $runnerRoot -WindowStyle Hidden

Start-Sleep -Seconds 5

$listener = Get-RunnerListener

if ($listener.Count -eq 0) {
  throw "EasySell GitHub runner did not start. Check C:\Users\Pc\actions-runner\_diag."
}

Write-Host "EasySell GitHub runner started: $($listener[0].Id)"
