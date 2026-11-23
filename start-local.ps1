param(
    [Parameter(Mandatory=$true)][string]$GameData,
    [Parameter(Mandatory=$true)][string]$Assets,
    [string]$Runtime,
    [string]$Save,
    [string]$BindAddress = '127.0.0.1',
    [int]$Port = 8080
)

$ErrorActionPreference = 'Stop'
$parent = Split-Path -Parent $PSScriptRoot
$root = if ($env:NGUIDLE_RUST) { $env:NGUIDLE_RUST }
    elseif (Test-Path -LiteralPath (Join-Path $parent 'nguidle-rust/Cargo.toml')) { Join-Path $parent 'nguidle-rust' }
    else { $parent }
if (!(Test-Path -LiteralPath (Join-Path $root 'tools/environment.ps1'))) {
    throw 'Set NGUIDLE_RUST to the engine checkout.'
}
. (Join-Path $root 'tools/environment.ps1')
$paths = Get-NguPaths
$game = (Resolve-Path -LiteralPath $GameData).Path
$prepared = (Resolve-Path -LiteralPath $Assets).Path
$build = Join-Path $paths.Work 'prepared/web'
$target = Join-Path $paths.Build 'wasm'
if (!$Save) {
    $legacyProfile = Join-Path $PSScriptRoot 'out/profile'
    $Save = if (Test-Path -LiteralPath $legacyProfile) { $legacyProfile } else { Join-Path $paths.Work 'saves/web' }
}

& python (Join-Path $root 'tools/verify_game_data.py') $game
if ($LASTEXITCODE -ne 0) { throw 'Game data verification failed.' }
& npm.cmd ci --prefix $PSScriptRoot --omit=dev --ignore-scripts --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw 'Browser dependencies could not be installed.' }
if (!$Runtime) {
    Push-Location -LiteralPath $root
    try {
        & cargo build --target-dir $target --target wasm32-wasip1 --locked --release -p ngu-browser
    } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw 'Could not build the browser runtime.' }
    $Runtime = Join-Path $target 'wasm32-wasip1/release/ngu_browser.wasm'
}
& python (Join-Path $PSScriptRoot 'pack_assets.py') $prepared (Join-Path $game 'Managed/Assembly-CSharp.dll') (Join-Path $build 'game.pak')
if ($LASTEXITCODE -ne 0) { throw 'Could not package the game data.' }
Copy-Item -LiteralPath $Runtime -Destination (Join-Path $build 'runtime.wasm') -Force
& python (Join-Path $PSScriptRoot 'server.py') --host $BindAddress --port $Port --build $build --assets $prepared --save $Save
exit $LASTEXITCODE
