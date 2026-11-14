<#
.SYNOPSIS
  Team Tracker client kit for Windows: install the "tt" command line, online or offline.
.DESCRIPTION
  .\install.ps1 install [-Server URL] [-Workspace SLUG] [-Ca FILE]   (offline from .\wheelhouse when present)
  .\install.ps1 login [-Browser | -Token TOKEN -Handle @me]
  .\install.ps1 status | uninstall
  -Verbose shows every step; -Offline never uses the network.
  Exit codes: 0 ok, 1 failure, 2 usage, 3 prerequisite missing (Python 3.12), 4 offline and something is missing.
#>
[CmdletBinding()]
param(
  [Parameter(Position=0)][ValidateSet('install','login','status','uninstall')][string]$Command,
  [string]$Server, [string]$Workspace, [string]$Ca, [switch]$Browser, [string]$Token, [string]$Handle, [switch]$Offline
)
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Wheels = Join-Path $Here 'wheelhouse'
if (Test-Path $Wheels) { $Offline = $true }
$Conf = Join-Path $env:APPDATA 'tt'; New-Item -ItemType Directory -Force -Path $Conf | Out-Null
$Venv = Join-Path $env:LOCALAPPDATA 'tt\venv'
function Fail($code, $rc, $what, $why, $fix) {
  Write-Host "error $code`n  WHAT  $what`n  WHY   $why`n  FIX   $fix`n  DOCS  docs/guide/08-clients.md#client-kit" -ForegroundColor Red
  exit $rc
}
if (-not $Command) { Get-Help $MyInvocation.MyCommand.Path; exit 2 }
switch ($Command) {
  'install' {
    $py = (Get-Command py -ErrorAction SilentlyContinue)
    if ($py) { $pyexe = 'py'; $pyargs = @('-3.12') } else { $pyexe = 'python'; $pyargs = @() }
    & $pyexe @pyargs -c "import sys; sys.exit(sys.version_info < (3,12))" 2>$null
    if ($LASTEXITCODE -ne 0) { Fail 'TT-E100' 3 'Python 3.12+ is needed' 'no Python 3.12 found (py -3.12 / python)' 'install Python 3.12 from python.org (tick "Add to PATH"), then .\install.ps1 install' }
    Write-Verbose "python: $pyexe $pyargs"
    & $pyexe @pyargs -m venv $Venv
    $pip = Join-Path $Venv 'Scripts\pip.exe'
    if ($Offline) {
      if (-not (Test-Path $Wheels)) { Fail 'TT-E401' 4 'offline install without a wheelhouse' 'this kit has no wheelhouse folder' 'use the client-kit folder from a bundle' }
      & $pip install -q --no-index --find-links $Wheels tt-cli
    } else { & $pip install -q tt-cli --find-links $Wheels }
    if ($LASTEXITCODE -ne 0) { Fail 'TT-E120' 1 'installing tt failed' 'pip returned an error (above)' 're-run with -Verbose' }
    $bin = Join-Path $Venv 'Scripts'
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($userPath -notlike "*$bin*") { [Environment]::SetEnvironmentVariable('Path', "$userPath;$bin", 'User'); Write-Host "added $bin to your PATH (open a new terminal)" }
    $caFile = if ($Ca) { $Ca } elseif (Test-Path (Join-Path $Here 'ca\ca.pem')) { Join-Path $Here 'ca\ca.pem' } else { $null }
    if ($caFile) { Copy-Item $caFile (Join-Path $Conf 'ca.pem') -Force; Write-Host "CA bundle: $Conf\ca.pem" }
    if ($Server) { "server_url = `"$Server`"`nworkspace = `"$Workspace`"" | Set-Content (Join-Path $Conf 'kit-defaults.toml') }
    Write-Host "installed: $bin\tt.exe"
    Write-Host 'next: .\install.ps1 login -Browser'
  }
  'login' {
    $tt = Join-Path $Venv 'Scripts\tt.exe'
    if (-not (Test-Path $tt)) { Fail 'TT-E100' 3 'tt is not installed' 'run install first' '.\install.ps1 install' }
    $defaults = Join-Path $Conf 'kit-defaults.toml'
    if (-not $Server -and (Test-Path $defaults)) {
      $Server = (Select-String -Path $defaults -Pattern 'server_url = "(.*)"').Matches[0].Groups[1].Value
      $Workspace = (Select-String -Path $defaults -Pattern 'workspace = "(.*)"').Matches[0].Groups[1].Value
    }
    if (-not $Server -or -not $Workspace) { Fail 'TT-E012' 2 'login needs the server URL and workspace' 'no -Server/-Workspace given and none saved by install' '.\install.ps1 login -Server https://tracker.example.com -Workspace acme -Browser' }
    $a = @('login', '--url', $Server, '--workspace', $Workspace)
    if (Test-Path (Join-Path $Conf 'ca.pem')) { $a += @('--ca-bundle', (Join-Path $Conf 'ca.pem')) }
    if ($Browser) { $a += '--browser' } elseif ($Token) { $a += @('--token', $Token, '--handle', $Handle) }
    & $tt @a
  }
  'status' {
    $tt = Join-Path $Venv 'Scripts\tt.exe'
    if (Test-Path $tt) { Write-Host "tt: $tt" } else { Write-Host 'tt: not installed' }
  }
  'uninstall' {
    if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }
    Write-Host "tt removed (sign-in in $Conf is kept)"
  }
}
