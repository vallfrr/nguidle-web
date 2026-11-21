param(
    [Parameter(Mandatory=$true)][string]$GameData,
    [Parameter(Mandatory=$true)][string]$Assets,
    [string]$Runtime,
    [string]$Save = (Join-Path $PSScriptRoot 'out/profile'),
    [string]$BindAddress = '127.0.0.1',
    [int]$Port = 8080
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$game = (Resolve-Path -LiteralPath $GameData).Path
$prepared = (Resolve-Path -LiteralPath $Assets).Path
$build = Join-Path $PSScriptRoot 'out/browser'
$target = Join-Path $PSScriptRoot 'out/cargo'

& python (Join-Path $root 'tools/verify_game_data.py') $game
if ($LASTEXITCODE -ne 0) { throw 'Game data verification failed.' }
& npm ci --prefix $PSScriptRoot --omit=dev --ignore-scripts --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw 'Browser dependencies could not be installed.' }
if (!$Runtime) {
    & cargo build --manifest-path (Join-Path $root 'Cargo.toml') --target-dir $target --target wasm32-wasip1 --locked --release -p ngu-browser
    if ($LASTEXITCODE -ne 0) { throw 'Could not build the browser runtime.' }
    $Runtime = Join-Path $target 'wasm32-wasip1/release/ngu_browser.wasm'
}
& python (Join-Path $PSScriptRoot 'pack_assets.py') $prepared (Join-Path $game 'Managed/Assembly-CSharp.dll') (Join-Path $build 'game.pak')
if ($LASTEXITCODE -ne 0) { throw 'Could not package the game data.' }
Copy-Item -LiteralPath $Runtime -Destination (Join-Path $build 'runtime.wasm') -Force
& python (Join-Path $PSScriptRoot 'server.py') --host $BindAddress --port $Port --build $build --assets $prepared --save $Save
exit $LASTEXITCODE
