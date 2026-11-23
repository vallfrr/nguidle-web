param(
    [Parameter(Mandatory=$true)][string]$GameData,
    [string]$Assets,
    [string]$Runtime,
    [string]$Save,
    [string]$BindAddress = '127.0.0.1',
    [int]$Port = 8080
)

$ErrorActionPreference = 'Stop'
$parent = Split-Path -Parent $PSScriptRoot
$workspace = if ($env:NGUIDLE_HOME) { $env:NGUIDLE_HOME }
    elseif ((Split-Path -Leaf $parent) -eq 'repos') { Split-Path -Parent $parent }
    else { $PSScriptRoot }
$work = if ($env:NGU_WORK) { $env:NGU_WORK } else { Join-Path $workspace 'work' }
$buildRoot = if ($env:NGU_BUILD) { $env:NGU_BUILD } else { Join-Path $workspace 'build' }
$game = (Resolve-Path -LiteralPath $GameData).Path
$build = Join-Path $work 'prepared/web'
$target = Join-Path $buildRoot 'wasm'
$engineTools = Join-Path $build 'engine-tools'
& python -B (Join-Path $PSScriptRoot 'engine.py') --stage $engineTools
if ($LASTEXITCODE -ne 0) { throw 'Could not stage the pinned engine tools.' }
& python -B (Join-Path $engineTools 'verify_game_data.py') $game
if ($LASTEXITCODE -ne 0) { throw 'Game data verification failed.' }

if ($Assets) {
    $prepared = (Resolve-Path -LiteralPath $Assets).Path
} else {
    $prepared = Join-Path $build 'assets'
    $venv = Join-Path $workspace 'venvs/prep-web'
    $prepPython = Join-Path $venv 'Scripts/python.exe'
    if (!(Test-Path -LiteralPath $prepPython)) {
        & python -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the web asset environment.' }
    }
    & $prepPython -m pip install --disable-pip-version-check -r (Join-Path $engineTools 'requirements-prep.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Could not install the web asset tools.' }
    & $prepPython -B (Join-Path $engineTools 'nguprep.py') --game-data $game --out $prepared
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the web assets.' }
    if ($env:NGU_FETCH_MILESTONE_ICONS -ne '0') {
        & $prepPython -B (Join-Path $engineTools 'fetch-achievement-icons.py') $prepared
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Milestone icons were unavailable. The game will show their names.' }
    }
}
if (!$Save) {
    $legacyProfile = Join-Path $PSScriptRoot 'out/profile'
    $Save = if (Test-Path -LiteralPath $legacyProfile) { $legacyProfile } else { Join-Path $work 'saves/web' }
}

& npm.cmd ci --prefix $PSScriptRoot --omit=dev --ignore-scripts --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw 'Browser dependencies could not be installed.' }
if (!$Runtime) {
    Push-Location -LiteralPath $PSScriptRoot
    try { & cargo build --target-dir $target --target wasm32-wasip1 --locked --release }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw 'Could not build the browser runtime.' }
    $Runtime = Join-Path $target 'wasm32-wasip1/release/ngu_browser.wasm'
}
& python (Join-Path $PSScriptRoot 'pack_assets.py') $prepared (Join-Path $game 'Managed/Assembly-CSharp.dll') (Join-Path $build 'game.pak')
if ($LASTEXITCODE -ne 0) { throw 'Could not package the game data.' }
$destination = Join-Path $build 'runtime.wasm'
if ([IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Runtime).Path) -ne [IO.Path]::GetFullPath($destination)) {
    Copy-Item -LiteralPath $Runtime -Destination $destination -Force
}
& python (Join-Path $PSScriptRoot 'server.py') --host $BindAddress --port $Port --build $build --assets $prepared --save $Save
exit $LASTEXITCODE
