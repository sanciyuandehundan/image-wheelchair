# Installer bootstrap (called by the install .cmd in the project root).
# 1. Download uv (Astral's Python package manager), pinned by SHA-256.
# 2. Let uv install a private Python 3.13 under .runtime\python and create .venv.
# 3. Hand over to scripts\install.py (ComfyUI, PyTorch, models).
# GitHub is slow or blocked in some regions: every GitHub download is speed-tested against a few public GitHub
# proxies and the fastest one is used. Only files with a known SHA-256 go through a proxy (uv is pinned below;
# uv itself verifies the Python builds it downloads).
# Kept ASCII-only on purpose: Windows PowerShell 5.1 misreads non-ASCII .ps1 files without a BOM.
# Native programs (curl, uv, python) are judged by their exit codes; with 'Stop' here, Windows PowerShell 5.1
# would treat their normal stderr messages as fatal errors when the output is redirected.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is extremely slow with the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root '.runtime'
$uvVersion = '0.13.0'
$uvSha256 = '088962f9e7b7bd9ea740c04c650b2a21c8928c345bd99ac24350dc924dba656c'
$uvUrl = "https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-x86_64-pc-windows-msvc.zip"
$githubPrefixes = @('', 'https://ghfast.top/', 'https://gh-proxy.com/', 'https://gh.llkk.cc/')
$uvDir = Join-Path $runtime "uv-$uvVersion"
$uv = Join-Path $uvDir 'uv.exe'
$venv = Join-Path $root '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
$curl = Join-Path $env:SystemRoot 'System32\curl.exe'
New-Item -ItemType Directory -Force -Path $runtime -ErrorAction Stop | Out-Null

# Rank direct GitHub and the proxies by how fast the first 1 MB of the uv zip arrives; unreachable ones go last.
function Get-GithubPrefixes {
    $timed = foreach ($prefix in $githubPrefixes) {
        $speed = 0
        if (Test-Path -LiteralPath $curl) {
            $out = & $curl -s -L -r 0-1048575 --connect-timeout 8 --max-time 20 -o NUL -w '%{http_code} %{speed_download}' "$prefix$uvUrl"
            $parts = "$out".Split(' ')
            if ($parts.Count -eq 2 -and ($parts[0] -eq '206' -or $parts[0] -eq '200')) { $speed = [double]$parts[1] }
        }
        $name = if ($prefix) { $prefix } else { 'github.com' }
        Write-Host ("  {0}: {1:N0} KB/s" -f $name, ($speed / 1024))
        [pscustomobject]@{ Prefix = $prefix; Speed = $speed }
    }
    return @($timed | Sort-Object -Property Speed -Descending | ForEach-Object { $_.Prefix })
}

# Download one file, trying each source in turn, and check its SHA-256.
function Get-VerifiedFile([string[]]$Urls, [string]$OutFile, [string]$Sha256) {
    for ($round = 1; $round -le 3; $round++) {
        foreach ($url in $Urls) {
            if (Test-Path -LiteralPath $curl) {
                & $curl -fsSL --connect-timeout 15 --speed-limit 20480 --speed-time 30 -o $OutFile $url
                $ok = ($LASTEXITCODE -eq 0)
            } else {
                try { Invoke-WebRequest -Uri $url -OutFile $OutFile -UseBasicParsing -ErrorAction Stop; $ok = $true } catch { $ok = $false }
            }
            if ($ok -and (Get-FileHash -LiteralPath $OutFile -Algorithm SHA256).Hash.ToLower() -eq $Sha256) { return }
            Write-Host "  failed from $($url.Split('/')[2]), trying the next source"
            Remove-Item -LiteralPath $OutFile -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds (5 * $round)
    }
    Write-Host 'Could not download uv. Check your network or proxy, then run the installer again.'
    exit 1
}

$needUv = -not (Test-Path -LiteralPath $uv)
$needPython = -not (Test-Path -LiteralPath $python)
$prefixes = @('')
if ($needUv -or $needPython) {
    Write-Host 'Testing GitHub download speed ...'
    $prefixes = Get-GithubPrefixes
}

if ($needUv) {
    Write-Host "Downloading uv $uvVersion ..."
    $zip = Join-Path $runtime 'uv.zip'
    Get-VerifiedFile @($prefixes | ForEach-Object { "$_$uvUrl" }) $zip $uvSha256
    Expand-Archive -LiteralPath $zip -DestinationPath $uvDir -Force -ErrorAction Stop
    Remove-Item -LiteralPath $zip
}

# Python and packages live inside the project; uv's download cache stays in its default place.
$env:UV_PYTHON_INSTALL_DIR = Join-Path $runtime 'python'
$env:UV_LINK_MODE = 'copy'   # the cache and the project may be on different drives
if ($needPython) {
    Write-Host 'Installing Python 3.13 and creating .venv ...'
    $created = $false
    foreach ($prefix in $prefixes) {
        if ($prefix) {
            $env:UV_PYTHON_INSTALL_MIRROR = $prefix + 'https://github.com/astral-sh/python-build-standalone/releases/download'
        } else {
            Remove-Item Env:UV_PYTHON_INSTALL_MIRROR -ErrorAction SilentlyContinue
        }
        & $uv venv $venv --python 3.13 --managed-python --clear
        if ($LASTEXITCODE -eq 0) { $created = $true; break }
        Write-Host '  failed, trying the next source'
    }
    if (-not $created) {
        Write-Host 'Could not create the Python environment. Check your network, then run the installer again.'
        exit 1
    }
}

& $python -X utf8 -s (Join-Path $PSScriptRoot 'install.py') --uv $uv @args
exit $LASTEXITCODE
