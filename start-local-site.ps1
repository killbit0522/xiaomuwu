param(
  [int]$Port = 4173,
  [string]$TextbookRoot = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'textbook'),
  [string]$AdminKey = 'MUMU-ADMIN-2026'
)

$ErrorActionPreference = 'Stop'
$siteRoot = $PSScriptRoot
Write-Host "Site: http://127.0.0.1:$Port/pages/home.html"
Write-Host "Changes under textbook sync automatically while this service is running; the page refreshes its catalog within 10 seconds."
$lanAddresses = [Net.Dns]::GetHostAddresses([Net.Dns]::GetHostName()) |
  Where-Object { $_.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork -and -not $_.IPAddressToString.StartsWith('127.') }
foreach ($address in $lanAddresses) {
  Write-Host ("LAN: http://{0}:{1}/pages/home.html" -f $address.IPAddressToString, $Port)
}
$env:CABIN_ADMIN_KEY = $AdminKey
python (Join-Path $siteRoot 'scripts\server.py') --port $Port --site $siteRoot --textbook $TextbookRoot
