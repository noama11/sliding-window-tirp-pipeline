# Run a pipeline command and report wall clock + PEAK memory.
#
#   .\measure_run.ps1 -Args 'run_pipeline.py','--window','2015','--keep-abstractions'
#   .\measure_run.ps1 -Args 'run_pipeline.py' -Log 'full_run.log'
#
# Needs nothing beyond Windows PowerShell and the python already on PATH.
#
# Two Windows traps this works around, both of which silently report a wrong
# number rather than failing:
#
#   1. Windows discards a process's memory counters the moment it exits, so
#      $p.PeakWorkingSet64 reads 0 after WaitForExit(). Memory has to be read
#      while the process is still alive.
#   2. Some python.exe launchers -- a venv's in particular -- are stubs that
#      spawn the real interpreter as a CHILD. Measuring the pid Start-Process
#      returns then reports the stub's ~16 MB no matter what the run does. So
#      this walks the whole process tree and takes the largest peak in it.
#
# Windows maintains PeakWorkingSet64 itself, so the poll interval affects only
# how quickly a new child is discovered, not the accuracy of the peak.

param(
    [Parameter(Mandatory = $true)][string[]]$Args,
    [string]$Log = 'run.log',
    [int]$PollMs = 250
)

$py = (Get-Command python).Source
if (-not $py) { throw "python not found on PATH" }

$err = [System.IO.Path]::ChangeExtension($Log, '.err')
$start = Get-Date
$p = Start-Process $py -ArgumentList $Args -NoNewWindow -PassThru `
                       -RedirectStandardOutput $Log -RedirectStandardError $err
$null = $p.Handle          # cache the handle so ExitCode survives the exit

function Get-TreePids([int]$RootPid) {
    # $RootPid plus every descendant, breadth-first.
    $all = @($RootPid)
    $frontier = @($RootPid)
    while ($frontier.Count -gt 0) {
        $filter = ($frontier | ForEach-Object { "ParentProcessId=$_" }) -join ' OR '
        $kids = @(Get-CimInstance Win32_Process -Filter $filter -ErrorAction SilentlyContinue |
                  Select-Object -ExpandProperty ProcessId)
        $kids = @($kids | Where-Object { $all -notcontains $_ })
        if ($kids.Count -eq 0) { break }
        $all += $kids
        $frontier = $kids
    }
    return $all
}

$peak = 0
$measured = @{}
while (-not $p.HasExited) {
    foreach ($procId in (Get-TreePids $p.Id)) {
        try {
            $g = Get-Process -Id $procId -ErrorAction Stop
            if ($g.PeakWorkingSet64 -gt $peak) { $peak = $g.PeakWorkingSet64 }
            $measured[$procId] = $g.ProcessName
        } catch { }         # exited between enumeration and read
    }
    Start-Sleep -Milliseconds $PollMs
}
$p.WaitForExit()
$elapsed = (Get-Date) - $start

Write-Host ''
Write-Host ('-' * 52)
Write-Host ("command    : python {0}" -f ($Args -join ' '))
Write-Host ("exit code  : {0}" -f $p.ExitCode)
Write-Host ("wall clock : {0:n1} s   ({1:n2} min)" -f $elapsed.TotalSeconds, $elapsed.TotalMinutes)
Write-Host ("peak RAM   : {0:n0} MB   (max over {1} process(es) in the tree)" -f ($peak / 1MB), $measured.Count)
Write-Host ("log        : {0}" -f $Log)
Write-Host ('-' * 52)

if ($p.ExitCode -ne 0) {
    Write-Host ''
    Write-Host 'FAILED -- last 20 lines of output:' -ForegroundColor Red
    Get-Content $Log -Tail 20
    if ((Test-Path $err) -and (Get-Item $err).Length -gt 0) {
        Write-Host '--- stderr ---' -ForegroundColor Red
        Get-Content $err -Tail 20
    }
}
exit $p.ExitCode
