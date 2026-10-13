# Read legacy Word documents without modifying originals. Requires desktop Microsoft Word.
param(
    [Parameter(Mandatory=$true)][string]$SourceFolder,
    [string]$PrivateDirectory = (Join-Path $PSScriptRoot '..\data\private')
)
$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path -LiteralPath $SourceFolder).Path
$extractRoot = Join-Path ([IO.Path]::GetFullPath($PrivateDirectory)) 'extracted'
New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null
$wordApp = $null
$readCount = 0
$failedCount = 0
try {
    $wordApp = New-Object -ComObject Word.Application
    $wordApp.Visible = $false
    $wordApp.DisplayAlerts = 0
    $wordApp.AutomationSecurity = 3 # Disable macros in every opened document.
    foreach ($sourceDoc in (Get-ChildItem -LiteralPath $sourceRoot -Recurse -File -Filter '*.doc')) {
        $document = $null
        try {
            $fileHash = (Get-FileHash -LiteralPath $sourceDoc.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            $target = Join-Path $extractRoot ($fileHash + '.txt')
            if (Test-Path -LiteralPath $target) { $readCount++; continue }
            $document = $wordApp.Documents.Open($sourceDoc.FullName, $false, $true, $false)
            $body = $document.Content.Text.Replace([string][char]7, "`t").Replace("`r", "`n").Replace([string][char]11, "`n")
            if ([string]::IsNullOrWhiteSpace($body)) { throw 'No extractable document text' }
            if ((Get-FileHash -LiteralPath $sourceDoc.FullName -Algorithm SHA256).Hash.ToLowerInvariant() -ne $fileHash) {
                throw 'Source is still being downloaded'
            }
            [IO.File]::WriteAllText($target, $body, [Text.UTF8Encoding]::new($false))
            $readCount++
        } catch {
            $failedCount++
            Write-Warning ("Read failed: " + $sourceDoc.Name + ' / ' + $_.Exception.Message)
        } finally {
            if ($null -ne $document) {
                $document.Close(0)
                [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
            }
        }
    }
} finally {
    if ($null -ne $wordApp) {
        $wordApp.Quit(0)
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($wordApp)
    }
}
Write-Output ("Legacy documents read: " + $readCount + '; failed: ' + $failedCount + '. Run the catalog import again.')
