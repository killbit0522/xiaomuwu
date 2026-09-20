param(
  [string]$TextbookRoot = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'textbook'),
  [string]$OutputPath = (Join-Path (Split-Path -Parent $PSScriptRoot) 'assets\catalog.json'),
  [switch]$Watch
)

$ErrorActionPreference = 'Stop'
$extensions = @('.txt', '.docx', '.zip', '.rar', '.7z', '.pdf', '.epub', '.mobi')

function Write-Catalog {
  if (-not (Test-Path -LiteralPath $TextbookRoot -PathType Container)) {
    throw "Textbook directory not found: $TextbookRoot"
  }

  $items = Get-ChildItem -LiteralPath $TextbookRoot -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
      $extensions -contains $_.Extension.ToLowerInvariant() -and
      $_.FullName -notmatch '[\\/](novel-library|node_modules|\.git)[\\/]'
    } |
    ForEach-Object {
      $rootPrefix = $TextbookRoot.TrimEnd('\', '/') + '\'
      $relative = $_.FullName.Substring($rootPrefix.Length).Replace('\', '/')
      $category = [IO.Path]::GetDirectoryName($relative)
      if ($null -eq $category) { $category = 'uncategorized' } else { $category = $category.Replace('\', ' / ') }
      $title = [IO.Path]::GetFileNameWithoutExtension($_.Name)
      $sha = [Security.Cryptography.SHA256]::Create()
      try { $hashBytes = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($relative)) } finally { $sha.Dispose() }
      $id = ([BitConverter]::ToString($hashBytes).Replace('-', '').ToLowerInvariant()).Substring(0, 16)
      [ordered]@{
        id = $id
        title = $title
        category = $category
        format = $_.Extension.TrimStart('.').ToLowerInvariant()
        size = $_.Length
        modifiedAt = $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
        searchablePath = $relative
        search = ($title + ' ' + $relative).ToLowerInvariant()
        downloadable = $true
        url = '/books/' + $relative
      }
    }

  $items = @($items | Sort-Object title, searchablePath)
  $outputDirectory = Split-Path -Parent $OutputPath
  if (-not (Test-Path -LiteralPath $outputDirectory)) { New-Item -ItemType Directory -Path $outputDirectory | Out-Null }
  $temporaryPath = "$OutputPath.tmp"
  $json = $items | ConvertTo-Json -Depth 4 -Compress
  [IO.File]::WriteAllText($temporaryPath, $json, [Text.UTF8Encoding]::new($false))
  Move-Item -LiteralPath $temporaryPath -Destination $OutputPath -Force
  Write-Host ("[{0}] Synced {1} books" -f (Get-Date -Format 'HH:mm:ss'), $items.Count)
}

Write-Catalog
if (-not $Watch) { exit 0 }

$watcher = [IO.FileSystemWatcher]::new($TextbookRoot)
$watcher.IncludeSubdirectories = $true
$watcher.NotifyFilter = [IO.NotifyFilters]'FileName, DirectoryName, LastWrite, Size'
$watcher.EnableRaisingEvents = $true
$pending = $false
$lastChange = Get-Date
$handlers = @()
foreach ($eventName in @('Changed', 'Created', 'Deleted', 'Renamed')) {
  $handlers += Register-ObjectEvent -InputObject $watcher -EventName $eventName -Action {
    $script:pending = $true
    $script:lastChange = Get-Date
  }
}

Write-Host "Watching $TextbookRoot. Press Ctrl+C to stop."
try {
  while ($true) {
    Start-Sleep -Milliseconds 700
    if ($pending -and ((Get-Date) - $lastChange).TotalMilliseconds -ge 900) {
      $pending = $false
      try { Write-Catalog } catch { Write-Warning $_ }
    }
  }
} finally {
  $handlers | Unregister-Event
  $watcher.Dispose()
}
