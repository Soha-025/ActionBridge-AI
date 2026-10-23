<#
  make_member5_zip.ps1 — package the ActionBridge-AI working tree for handoff.

  Run from the folder that CONTAINS the repo:
      powershell -NoProfile -ExecutionPolicy Bypass -File .\make_member5_zip.ps1

  What it does:
    1. selects every file under ActionBridge-AI\ except caches, virtualenvs, build
       output, databases, real .env files and .git
    2. writes to a staging path first, so an existing verified ZIP is never
       overwritten before the new one passes every check
    3. verifies: all required paths present, no excluded path leaked, then EXTRACTS
       the staged archive into a temp folder and compares every packaged file by
       SHA256 against the source tree
    4. only on a full pass does it move the staged archive into place, keeping the
       previous archive as ActionBridge-AI-Member5-Final.zip.prev-<timestamp>

  Exit 0 = PASS. Exit 1 = a verification failed; the archive you already had was
  not touched and the staged copy is left at <zip>.building for inspection.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Both assemblies: loading only FileSystem is not enough on every PowerShell host.
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$rootDir = $PSScriptRoot
$srcDir  = Join-Path $rootDir 'ActionBridge-AI'
$zipPath = Join-Path $rootDir 'ActionBridge-AI-Member5-Final.zip'
$staged  = "$zipPath.building"
$topFold = 'ActionBridge-AI'

if (-not (Test-Path -LiteralPath $srcDir -PathType Container)) {
  throw "Project folder not found: $srcDir"
}

# Anything under these directory names is never packaged, at any depth.
$skipDirs = @('.git', 'node_modules', '.venv', 'venv', 'dist', '__pycache__',
              '.pytest_cache', '.mypy_cache', '.ruff_cache', 'coverage_html',
              '.idea', '.vscode', '.qoder')

# Files dropped by leaf name or pattern, wherever they sit.
$skipExact = @('.env', 'ActionBridge-AI-Member5-Final.zip',
               'ActionBridge-AI-Member5-Final.zip.building')
$skipWild  = @('*.db', '*.db.backup-*', '*.sqlite', '*.sqlite3', '*.log', '*.tmp',
               '*.pyc', '.DS_Store', 'Thumbs.db')

function Test-IsSkipped([string]$rel) {
  $parts = $rel -split '[\\/]'
  for ($i = 0; $i -lt $parts.Length - 1; $i++) {
    if ($skipDirs -contains $parts[$i]) { return $true }
  }
  $leaf = $parts[$parts.Length - 1]
  if ($skipExact -contains $leaf) { return $true }
  foreach ($pattern in $skipWild) { if ($leaf -like $pattern) { return $true } }
  return $false
}

$srcLen = $srcDir.TrimEnd('\').Length + 1
$all = @(Get-ChildItem -LiteralPath $srcDir -Recurse -File -Force)
$files = @($all | ForEach-Object {
  $rel = $_.FullName.Substring($srcLen)
  if (-not (Test-IsSkipped $rel)) {
    [pscustomobject]@{ Full = $_.FullName; Rel = $rel; Bytes = $_.Length }
  }
})
if ($files.Count -eq 0) { throw 'Nothing selected for packaging - check the filters.' }

Write-Host "Packaging $($files.Count) files from $srcDir"

if (Test-Path -LiteralPath $staged) { Remove-Item -LiteralPath $staged -Force }
$archive = [System.IO.Compression.ZipFile]::Open($staged, [System.IO.Compression.ZipArchiveMode]::Create)
try {
  foreach ($f in $files) {
    $entryName = "$topFold/" + ($f.Rel -replace '\\', '/')
    [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $f.Full, $entryName)
  }
} finally {
  $archive.Dispose()
}

# ---------- 1. required paths and exclusions, read back from the archive ----------
$required = @(
  'README.md', 'LICENSE', '.gitignore', 'FRICTION_LOG.md', 'step1_today_initial.png',
  'docs/dashboard_qa.md', 'docs/test_evidence.md', 'docs/HANDOFF_MEMBER5.md',
  'docs/demo_script.md', 'docs/integration_checklist.md', 'docs/architecture.png',
  'docs/create_architecture.py',
  'backend/main.py', 'backend/services.py', 'backend/agent.py', 'backend/mcp_server.py',
  'backend/seed.py', 'backend/llm_provider.py', 'backend/demo_provider.py',
  'backend/gemini_provider.py', 'backend/requirements.txt', 'backend/requirements-dev.txt',
  'backend/.env.example', 'backend/test_main.py', 'backend/test_services.py',
  'backend/test_agent.py', 'backend/test_mcp_tools.py', 'backend/test_seed.py',
  'backend/test_fallback.py', 'backend/test_gemini_provider.py',
  'frontend/package.json', 'frontend/package-lock.json', 'frontend/index.html',
  'frontend/tsconfig.json', 'frontend/vite.config.ts', 'frontend/.env.example',
  'frontend/src/main.tsx', 'frontend/src/App.tsx', 'frontend/src/api.ts',
  'frontend/src/types.ts', 'frontend/src/demoData.ts', 'frontend/src/format.ts',
  'frontend/src/hooks/useSpeech.ts',
  'frontend/src/components/TodayPlan.tsx', 'frontend/src/components/CommitmentsPanel.tsx',
  'frontend/src/components/ConflictsPanel.tsx', 'frontend/src/components/ConflictChecker.tsx',
  'frontend/src/components/PanelState.tsx', 'frontend/src/components/ChatPanel.tsx',
  'frontend/src/components/ConfirmModal.tsx', 'frontend/src/components/actionbridge.css'
)

$check = [System.IO.Compression.ZipFile]::OpenRead($staged)
try {
  $entries = @($check.Entries | ForEach-Object { $_.FullName })
  $rel = @($entries | ForEach-Object { $_.Substring("$topFold/".Length) })
  $missing = @($required | Where-Object { $rel -notcontains $_ })
  $forbidden = @($rel | Where-Object {
    $p = $_ -split '/'
    ($p -contains '.git') -or ($p -contains 'node_modules') -or ($p -contains '.venv') -or
    ($p -contains 'venv') -or ($p -contains 'dist') -or ($p -contains '__pycache__') -or
    ($p -contains '.pytest_cache') -or ($_ -like '*.db') -or ($_ -like '*.sqlite') -or
    ($_ -like '*.pyc') -or ($_ -eq '.env')
  })
} finally {
  $check.Dispose()
}

# ---------- 2. extraction check: unpack into a temp dir and hash-compare ----------
$tmp = Join-Path $env:TEMP ('m5zip_' + [Guid]::NewGuid().ToString('N').Substring(0, 12))
New-Item -ItemType Directory -Path $tmp | Out-Null
$extractRoot = Join-Path $tmp 'extracted'
$bad = @()
$extracted = 0
try {
  [System.IO.Compression.ZipFileExtensions]::ExtractToDirectory($staged, $extractRoot)
  $extracted = @(Get-ChildItem -LiteralPath $extractRoot -Recurse -File).Count
  foreach ($f in $files) {
    $target = Join-Path $extractRoot (("$topFold/" + ($f.Rel -replace '\\', '/')) -replace '/', '\')
    if (-not (Test-Path -LiteralPath $target)) { $bad += "not extracted: $($f.Rel)"; continue }
    $srcHash = (Get-FileHash -LiteralPath $f.Full -Algorithm SHA256).Hash
    $outHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
    if ($srcHash -ne $outHash) { $bad += "hash mismatch: $($f.Rel)" }
  }
} finally {
  Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

$pass = ($missing.Count -eq 0) -and ($forbidden.Count -eq 0) -and ($bad.Count -eq 0) -and
        ($extracted -eq $files.Count) -and ($entries.Count -eq $files.Count)

# ---------- 3. promote the staged archive only after a full pass ----------
$kept = ''
if ($pass) {
  if (Test-Path -LiteralPath $zipPath) {
    $stamp = (Get-Item -LiteralPath $zipPath).LastWriteTime.ToString('yyyyMMdd-HHmmss')
    $kept = "$zipPath.prev-$stamp"
    Move-Item -LiteralPath $zipPath -Destination $kept -Force
  }
  Move-Item -LiteralPath $staged -Destination $zipPath -Force
}

$info = Get-Item -LiteralPath $(if ($pass) { $zipPath } else { $staged })
Write-Host ""
Write-Host "Archive  : $($info.FullName)"
Write-Host "Built    : $($info.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))  Size: $([math]::Round($info.Length / 1KB, 1)) KB"
Write-Host "Entries  : $($entries.Count)  (selected $($files.Count), skipped $($all.Count - $files.Count) local-only files)"
if ($missing.Count -eq 0) {
  Write-Host "Required : OK - all $($required.Count) expected paths present"
} else {
  Write-Host "Required : MISSING -> $($missing -join ', ')"
}
if ($forbidden.Count -eq 0) {
  Write-Host "Excluded : OK - no .git / node_modules / venv / dist / caches / *.db / *.pyc / .env"
} else {
  Write-Host "Excluded : LEAKED -> $((($forbidden | Select-Object -First 10) -join ', '))"
}
if ($bad.Count -eq 0 -and $extracted -eq $files.Count) {
  Write-Host "Extract  : OK - $extracted files unpacked, every one SHA256-identical to source"
} else {
  $preview = if ($bad.Count -gt 5) { (($bad | Select-Object -First 5) -join '; ') + " (+$($bad.Count - 5) more)" } else { $bad -join '; ' }
  Write-Host "Extract  : PROBLEM - unpacked $extracted of $($files.Count); $preview"
}
if ($kept) { Write-Host "Previous archive kept at: $kept" }

if ($pass) {
  Write-Host "Result   : PASS"
  exit 0
}
Write-Host "Result   : FAIL - existing archive untouched; staged copy left at $staged"
exit 1
