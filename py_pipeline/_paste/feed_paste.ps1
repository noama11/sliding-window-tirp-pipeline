# Feed the paste set to the research room, one file at a time, via the clipboard.
# Run this on YOUR machine -- the one with the remote desktop open.
#
#   .\feed_paste.ps1                     # start at the beginning
#   .\feed_paste.ps1 -Start 14           # resume from file 14
#   .\feed_paste.ps1 -Only 'pattern.py'  # re-send one file that failed
#
# How a transfer goes:
#
#   1. This puts the next file on the clipboard and tells you what it sent.
#   2. Switch to the remote desktop window, press Enter in receive.py's prompt.
#   3. receive.py writes the file and checks it against MANIFEST.txt.
#   4. Come back here, press Enter for the next one.
#
# Before any of this works, two files have to go in by hand (Notepad, Save As,
# Encoding UTF-8): MANIFEST.txt and receive.py. Everything else rides the
# clipboard.
#
# The header line this prepends carries the destination path, so nothing has to
# be typed on the far side:
#
#     ###TIRP:pyengine/mediator/pattern.py

param(
    [string]$Set   = (Join-Path $PSScriptRoot 'out\paste_set'),
    [int]$Start    = 1,
    [string]$Only  = ''
)

$manifest = Join-Path $Set 'MANIFEST.txt'
if (-not (Test-Path $manifest)) {
    throw "no MANIFEST.txt in $Set -- run: python make_paste_set.py"
}

# Manifest order is paste order; tak_2700.json is assembled in the room from
# tak_parts/, so it is sent as its chunks rather than as itself.
$bootstrap = @('MANIFEST.txt', 'receive.py')   # these go in by hand, first
$files = @()
foreach ($line in Get-Content $manifest) {
    if ($line -match '^\s*#' -or -not $line.Trim()) { continue }
    $rel = ($line -split '\s+', 3)[2].Trim()
    if ($rel -eq 'tak_2700.json') { continue }      # assembled in the room
    if ($bootstrap -contains $rel) { continue }     # already there
    $files += $rel
}
foreach ($part in (Get-ChildItem (Join-Path $Set 'tak_parts') -Filter '*.txt' -ErrorAction SilentlyContinue | Sort-Object Name)) {
    $files += "tak_parts/$($part.Name)"
}

if ($Only) {
    $files = @($files | Where-Object { $_ -like "*$Only*" })
    if (-not $files) { throw "nothing in the paste set matches '$Only'" }
    $Start = 1
}

$total = $files.Count
Write-Host ''
Write-Host "Feeding $total files from $Set" -ForegroundColor Cyan
Write-Host 'In the room, run:  python receive.py --loop' -ForegroundColor Cyan
Write-Host 'Then: Enter here -> Enter there -> Enter here ...   (q quits)'
Write-Host ''

for ($i = $Start; $i -le $total; $i++) {
    $rel  = $files[$i - 1]
    $path = Join-Path $Set ($rel -replace '/', '\')
    if (-not (Test-Path $path)) { Write-Host "  MISSING $rel" -ForegroundColor Red; continue }

    # Read as one string and force LF, so what lands on the far side is exactly
    # what MANIFEST.txt was hashed over.
    $body = [System.IO.File]::ReadAllText($path) -replace "`r`n", "`n"
    Set-Clipboard -Value ("###TIRP:$rel`n" + $body)

    $kb = [math]::Round((Get-Item $path).Length / 1KB, 1)
    Write-Host ("[{0,3}/{1}] {2,-46} {3,7} KB  -> clipboard" -f $i, $total, $rel, $kb) -ForegroundColor Green
    $ans = Read-Host '        Enter for next, q to stop, r to resend'
    if ($ans -eq 'q') { Write-Host "`nstopped at $i. Resume with: .\feed_paste.ps1 -Start $i`n"; return }
    if ($ans -eq 'r') { $i-- }
}

Write-Host ''
Write-Host 'All files sent.' -ForegroundColor Cyan
Write-Host 'In the room, finish with:  python verify.py --assemble'
Write-Host ''
