# Put one drop chunk on the clipboard. Run this on YOUR machine.
#
#   .\copy_part.ps1        walk through every chunk, Enter after each paste
#   .\copy_part.ps1 3      just chunk 3, to re-send one that failed its check
#   .\copy_part.ps1 -Setup SETUP_ROOM.ps1, the first thing that goes in
#
# In the room: .\next.ps1 -> Ctrl+V -> Ctrl+S -> close, once per chunk.

param(
    [int]$Part = 0,
    [switch]$Setup,
    [string]$Drop = (Join-Path $PSScriptRoot 'out\drop')
)

if (-not (Test-Path $Drop)) { throw "no drop at $Drop -- run: python make_drop.py" }

function Send-One($path) {
    # ReadAllText, not Get-Content, so the text goes across as one string with
    # nothing re-joined or re-terminated on the way.
    Set-Clipboard -Value ([System.IO.File]::ReadAllText($path))
    $kb = [math]::Round((Get-Item $path).Length / 1KB, 1)
    Write-Host ("  {0,-16} {1,7} KB  -> clipboard" -f (Split-Path $path -Leaf), $kb) -ForegroundColor Green
}

if ($Setup) {
    Send-One (Join-Path $Drop 'SETUP_ROOM.ps1')
    Write-Host '  In the room: paste into PowerShell, or into Notepad and run it.'
    return
}

$parts = @(Get-ChildItem $Drop -Filter 'p*.b64' | Sort-Object Name)
if (-not $parts) { throw "no p*.b64 in $Drop -- run: python make_drop.py" }

if ($Part -gt 0) {
    if ($Part -gt $parts.Count) { throw "there are only $($parts.Count) chunks" }
    Send-One $parts[$Part - 1].FullName
    return
}

Write-Host ''
Write-Host "$($parts.Count) chunks in $Drop" -ForegroundColor Cyan
Write-Host 'In the room, for each one:  .\next.ps1  ->  Ctrl+V  ->  Ctrl+S  ->  close'
Write-Host ''
for ($i = 1; $i -le $parts.Count; $i++) {
    Write-Host ("[{0}/{1}]" -f $i, $parts.Count) -NoNewline
    Send-One $parts[$i - 1].FullName
    if ($i -lt $parts.Count) {
        $ans = Read-Host '        Enter for next, q to stop, r to resend'
        if ($ans -eq 'q') { Write-Host "`nstopped. Resume with: .\copy_part.ps1 $($i + 1)`n"; return }
        if ($ans -eq 'r') { $i-- }
    }
}
Write-Host ''
Write-Host 'All chunks sent. In the room:  python unpack.py' -ForegroundColor Cyan
Write-Host ''
