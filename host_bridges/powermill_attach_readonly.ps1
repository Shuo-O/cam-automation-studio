param(
    [Parameter(Mandatory = $true)][string]$PMAutomationAssembly,
    [Parameter(Mandatory = $true)][int]$TargetProcessId,
    [Parameter(Mandatory = $true)][string]$InstanceId,
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [Parameter(Mandatory = $true)][string]$Material,
    [Parameter(Mandatory = $true)][string]$Output,
    [Parameter(Mandatory = $false)][string]$CompilerPath
)

$ErrorActionPreference = "Stop"
$source = Join-Path $PSScriptRoot "powermill_attach_readonly.cs"
$buildDirectory = Join-Path ([IO.Path]::GetTempPath()) ("cam-pm-export-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $buildDirectory -Force | Out-Null
$exe = Join-Path $buildDirectory "powermill-attach-readonly.exe"
if ($CompilerPath) {
    $csc = (Get-Item -LiteralPath $CompilerPath -ErrorAction Stop).FullName
} else {
    $compilerCandidates = @(
        (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
        (Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe")
    )
    $csc = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $csc) {
        throw "csc.exe was not found. Pass -CompilerPath explicitly."
    }
}
$frameworkRoot = Split-Path -Parent $csc
$systemWebExtensions = Join-Path $frameworkRoot "System.Web.Extensions.dll"
$microsoftCSharp = Join-Path $frameworkRoot "Microsoft.CSharp.dll"

try {
    & $csc /nologo /target:exe /out:$exe /reference:$PMAutomationAssembly /reference:$systemWebExtensions /reference:$microsoftCSharp $source
    if ($LASTEXITCODE -ne 0) { throw "csc.exe failed with exit code $LASTEXITCODE" }
    $apiDirectory = Split-Path -Parent (Resolve-Path -LiteralPath $PMAutomationAssembly)
    # Keep SDK binaries out of the customer package while making this
    # temporary executable self-resolving at runtime.
    Get-ChildItem -LiteralPath $apiDirectory -Filter "*.dll" -File |
        Copy-Item -Destination $buildDirectory -Force
    & $exe --api-dir $buildDirectory --target-pid $TargetProcessId --instance-id $InstanceId --project-id $ProjectId --material $Material --output $Output
    if ($LASTEXITCODE -ne 0) { throw "PowerMill read-only exporter failed with exit code $LASTEXITCODE" }
} finally {
    Remove-Item -LiteralPath $buildDirectory -Recurse -Force -ErrorAction SilentlyContinue
}
