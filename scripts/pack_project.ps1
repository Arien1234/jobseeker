$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$destination = Join-Path $root "jobhunter-ai.zip"
if (Test-Path $destination) {
    Remove-Item $destination -Force
}
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::Open($destination, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    $excluded = @("browser_data", "data", "logs", ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".idea", ".vscode")
    Get-ChildItem -Path $root -Recurse -File | ForEach-Object {
        $relative = $_.FullName.Substring($root.Length).TrimStart("\", "/")
        $parts = $relative -split "[\\/]"
        if ($parts | Where-Object { $excluded -contains $_ }) {
            return
        }
        if ($_.Name -eq ".env" -or $_.Name -eq "secrets.json" -or $_.Name -eq "profile.json" -or $_.Extension -eq ".pdf" -or $_.Name -like "*.db" -or $_.Name -like "*.db-*") {
            return
        }
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $_.FullName, ($relative -replace "\\", "/")) | Out-Null
    }
}
finally {
    $archive.Dispose()
}
Write-Output $destination
