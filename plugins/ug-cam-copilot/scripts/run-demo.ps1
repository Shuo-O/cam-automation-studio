param(
  [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$app = Join-Path $PSScriptRoot "..\app\powermill_ai_demo.py"
$candidates = @()

if ($env:UGCAM_PYTHON) {
  $candidates += [pscustomobject]@{ Executable = $env:UGCAM_PYTHON; Prefix = @() }
}

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand) {
  $candidates += [pscustomobject]@{ Executable = $pythonCommand.Source; Prefix = @() }
}

$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
  $candidates += [pscustomobject]@{ Executable = $pyLauncher.Source; Prefix = @("-3") }
}

$runtimePattern = Join-Path $env:USERPROFILE ".cache\codex-runtimes\*\dependencies\python\python.exe"
Get-ChildItem -Path $runtimePattern -File -ErrorAction SilentlyContinue | ForEach-Object {
  $candidates += [pscustomobject]@{ Executable = $_.FullName; Prefix = @() }
}

foreach ($candidate in $candidates) {
  if (-not (Test-Path -LiteralPath $candidate.Executable -PathType Leaf)) {
    continue
  }
  $version = & $candidate.Executable @($candidate.Prefix) --version 2>&1
  if ("$version" -notmatch "^Python 3\.") {
    continue
  }
  & $candidate.Executable @($candidate.Prefix) $app --port $Port
  exit $LASTEXITCODE
}

throw "Python 3.10+ is required. Set UGCAM_PYTHON to a real Python executable and rerun."
