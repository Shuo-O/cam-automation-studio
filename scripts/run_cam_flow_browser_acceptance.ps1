[CmdletBinding()]
param(
    [int]$Port = 0,
    [switch]$Headed
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$specPath = Join-Path $repoRoot "tests\browser\cam-flow-studio.spec.cjs"
$artifactRoot = Join-Path $repoRoot "tests\browser\fixtures\cam-flow\artifacts"

function Resolve-ExistingFile {
    param(
        [string[]]$Candidates,
        [string]$Label
    )

    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return [System.IO.Path]::GetFullPath($candidate)
        }
    }
    throw "Unable to locate $Label. Set the documented CAM_FLOW_* environment override."
}

function Resolve-CommandPath {
    param([string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    return $null
}

$codexRuntime = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies"
$nodePath = Resolve-ExistingFile -Label "Node.js" -Candidates @(
    $env:CAM_FLOW_NODE,
    (Resolve-CommandPath "node"),
    (Join-Path $codexRuntime "node\bin\node.exe")
)
$pythonPath = Resolve-ExistingFile -Label "Python" -Candidates @(
    $env:CAM_FLOW_PYTHON,
    (Join-Path $codexRuntime "python\python.exe"),
    (Resolve-CommandPath "python")
)
$nodeModules = if ($env:CAM_FLOW_NODE_MODULES) {
    $env:CAM_FLOW_NODE_MODULES
}
else {
    Join-Path (Split-Path -Parent (Split-Path -Parent $nodePath)) "node_modules"
}
if (-not (Test-Path -LiteralPath (Join-Path $nodeModules "playwright") -PathType Container)) {
    throw "Playwright is not available under '$nodeModules'. Set CAM_FLOW_NODE_MODULES."
}

$chromePath = Resolve-ExistingFile -Label "Chrome or Edge" -Candidates @(
    $env:CAM_FLOW_CHROME,
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
)

if ($Port -eq 0) {
    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        0
    )
    $listener.Start()
    try {
        $Port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally {
        $listener.Stop()
    }
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Port must be in the range 1..65535."
}

$runId = [Guid]::NewGuid().ToString("N")
$dataDir = Join-Path ([System.IO.Path]::GetTempPath()) "cam-flow-browser-$runId"
$serverStdout = Join-Path $dataDir "server.stdout.log"
$serverStderr = Join-Path $dataDir "server.stderr.log"
$artifactBoundary = [System.IO.Path]::GetFullPath(
    (Join-Path $repoRoot "tests\browser\fixtures\cam-flow")
)
$resolvedArtifactRoot = [System.IO.Path]::GetFullPath($artifactRoot)
if (-not $resolvedArtifactRoot.StartsWith(
    $artifactBoundary,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Artifact path escaped the T12 fixture boundary: $resolvedArtifactRoot"
}
if (Test-Path -LiteralPath $resolvedArtifactRoot) {
    Remove-Item -LiteralPath $resolvedArtifactRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $artifactRoot, $dataDir | Out-Null

$previousAppData = $env:CAM_APP_DATA_DIR
$previousNodePath = $env:NODE_PATH
$previousBaseUrl = $env:CAM_FLOW_BASE_URL
$previousChrome = $env:CAM_FLOW_CHROME
$previousArtifacts = $env:CAM_FLOW_BROWSER_ARTIFACTS
$previousHeaded = $env:CAM_FLOW_HEADED
$server = $null
$exitCode = 1

try {
    $env:CAM_APP_DATA_DIR = $dataDir
    $env:NODE_PATH = $nodeModules
    $env:CAM_FLOW_BASE_URL = "http://127.0.0.1:$Port"
    $env:CAM_FLOW_CHROME = $chromePath
    $env:CAM_FLOW_BROWSER_ARTIFACTS = $artifactRoot
    $env:CAM_FLOW_HEADED = if ($Headed) { "1" } else { "0" }

    $server = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList @(
            "-m",
            "cam_automation",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "$Port"
        ) `
        -WorkingDirectory $repoRoot `
        -WindowStyle Hidden `
        -PassThru `
        -RedirectStandardOutput $serverStdout `
        -RedirectStandardError $serverStderr

    $ready = $false
    for ($attempt = 0; $attempt -lt 75; $attempt += 1) {
        if ($server.HasExited) {
            break
        }
        try {
            $response = Invoke-WebRequest `
                -Uri "$($env:CAM_FLOW_BASE_URL)/api/health" `
                -UseBasicParsing `
                -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        }
        catch {
            Start-Sleep -Milliseconds 200
        }
    }
    if (-not $ready) {
        $details = if (Test-Path -LiteralPath $serverStderr) {
            Get-Content -Raw -LiteralPath $serverStderr
        }
        else {
            "No server stderr was captured."
        }
        throw "CAM Flow Studio did not become ready. $details"
    }

    & $nodePath --test $specPath
    $exitCode = $LASTEXITCODE
}
finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id
        Wait-Process -Id $server.Id -ErrorAction SilentlyContinue
    }

    $env:CAM_APP_DATA_DIR = $previousAppData
    $env:NODE_PATH = $previousNodePath
    $env:CAM_FLOW_BASE_URL = $previousBaseUrl
    $env:CAM_FLOW_CHROME = $previousChrome
    $env:CAM_FLOW_BROWSER_ARTIFACTS = $previousArtifacts
    $env:CAM_FLOW_HEADED = $previousHeaded

    $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    $resolvedDataDir = [System.IO.Path]::GetFullPath($dataDir)
    if ($resolvedDataDir.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedDataDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "CAM Flow browser artifacts: $artifactRoot"
exit $exitCode
