param(
    [Parameter(Mandatory=$true)][string]$GameData,
    [Parameter(Mandatory=$true)][string]$Engine,
    [Parameter(Mandatory=$true)][string]$Assets,
    [string]$Save = (Join-Path $PSScriptRoot 'out/profile'),
    [string]$BindAddress = '127.0.0.1',
    [int]$Port = 8080
)

$ErrorActionPreference='Stop'
$game=(Resolve-Path -LiteralPath $GameData).Path
$enginePath=(Resolve-Path -LiteralPath $Engine).Path
$prepared=(Resolve-Path -LiteralPath $Assets).Path
$verify=Join-Path (Split-Path -Parent $PSScriptRoot) 'tools/verify_game_data.py'
& python $verify $game
if($LASTEXITCODE -ne 0){throw 'Game data verification failed.'}
$env:NGU_LAYOUT='pc'
$env:NGU_CULTURE='en'
& python (Join-Path $PSScriptRoot 'server.py') --host $BindAddress --port $Port `
    --engine $enginePath --dll (Join-Path $game 'Managed/Assembly-CSharp.dll') --assets $prepared --save $Save
exit $LASTEXITCODE
