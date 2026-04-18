param(
    [Parameter(Mandatory=$true)]
    [string]$TargetFile
)

$content = Get-Content $TargetFile -Raw -Encoding UTF8
$content = $content.Replace("â€—", "—").Replace("â€™", "'").Replace("â€˜", "'")
[System.IO.File]::WriteAllText(
    (Resolve-Path $TargetFile),
    $content,
    (New-Object System.Text.UTF8Encoding($false))
)
Write-Host "Fixed encoding in $TargetFile" -ForegroundColor Green
