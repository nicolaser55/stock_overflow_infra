<#
.SYNOPSIS
    Restore test (run by hand, administrator PowerShell): restores a random sample of files from the latest snapshot and compares
    each with the live file. Prints counts only.

.DESCRIPTION
    - Picks up to -SampleCount files (default 60) of the latest snapshot that were last changed more than a day before it and are at
      most 200 MB, so that the live copy should still be identical.
    - Restores them into -TestRoot, which must be a folder named _restore_test directly inside the quarantine root (the root holds
      store01_rawzone and no development-view marker): the restored files are holdout data, so they stay where agents cannot read.
      The folder must not exist; it is removed at the end.
    - For each file: restored (yes / no), live copy unchanged since the snapshot (same size and modification time) or changed or
      unreadable (skipped), and SHA-256 equal or different.
    Exit code: 0 when at least one file was compared, every compared file is equal and every sampled file was restored; 1 otherwise.

.EXAMPLE
    & 'C:\ProgramData\so_backup\restore_test.ps1' -TestRoot 'D:\<folder>\quarantine\_restore_test'
#>
param(
    [Parameter(Mandatory = $true)][string]$TestRoot,
    [string]$ConfigDir = $PSScriptRoot,
    [int]$SampleCount = 60
)
$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'backup_common.ps1')

# ADMINISTRATOR, AND A TEST FOLDER INSIDE THE QUARANTINE ROOT THAT DOES NOT EXIST YET
$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $IsAdmin) { Write-Host "STOP: run this in an administrator PowerShell"; exit 1 }
$TestRoot = [IO.Path]::GetFullPath($TestRoot).TrimEnd('\')
$Parent = Split-Path -Parent $TestRoot
if ((Split-Path -Leaf $TestRoot) -ne '_restore_test') { Write-Host "STOP: the test folder must be named _restore_test"; exit 1 }
if (-not (Test-Path -LiteralPath (Join-Path $Parent 'store01_rawzone') -PathType Container)) { Write-Host "STOP: the test folder must be directly inside the quarantine root"; exit 1 }
if ((Test-Path -LiteralPath (Join-Path $Parent 'VIEW.json')) -or (Test-Path -LiteralPath (Join-Path $Parent 'view_manifest.json'))) { Write-Host "STOP: that root is a development view"; exit 1 }
if (Test-Path -LiteralPath $TestRoot) { Write-Host "STOP: $TestRoot exists; remove it first"; exit 1 }

$Count = [ordered]@{ candidate = 0; sampled = 0; restored = 0; not_restored = 0; compared = 0; equal = 0; different = 0; skipped_changed = 0; skipped_unreadable = 0 }
$Ok = $false
try {
    $S = Get-SoBackupSetting $ConfigDir
    Set-SoBackupEnv $S
    # THE LATEST SNAPSHOT
    $R = Invoke-SoRestic $S @('snapshots', 'latest', '--json')
    if ($R.Code -ne 0) { throw "restic snapshots exit $($R.Code)" }
    $JsonLine = $R.Out | Where-Object { $_.TrimStart().StartsWith('[') } | Select-Object -First 1
    if (-not $JsonLine) { throw "restic snapshots gave no JSON list" }
    $Snap = ($JsonLine | ConvertFrom-Json) | Select-Object -First 1
    if (-not $Snap) { throw "no snapshot in the repository" }
    $SnapTime = [datetime]::Parse($Snap.time).ToUniversalTime()
    Write-Host "Latest snapshot: $($Snap.short_id), $([int]((Get-Date).ToUniversalTime() - $SnapTime).TotalHours) hour(s) old"
    # CANDIDATES: FILES CHANGED MORE THAN A DAY BEFORE THE SNAPSHOT, 1 BYTE TO 200 MB
    $R = Invoke-SoRestic $S @('ls', $Snap.id, '--json')
    if ($R.Code -ne 0) { throw "restic ls exit $($R.Code)" }
    $Cut = $SnapTime.AddDays(-1)
    $Candidate = @(foreach ($Line in $R.Out) {
        if ($Line -notmatch '"type":"file"') { continue }
        $Node = $Line | ConvertFrom-Json
        if ($Node.size -ge 1 -and $Node.size -le 200MB -and [datetime]::Parse($Node.mtime).ToUniversalTime() -lt $Cut) { $Node }
    })
    $Count.candidate = $Candidate.Count
    if ($Candidate.Count -eq 0) { throw "no file old enough to compare (run the test a day after the first backup)" }
    $Sample = @($Candidate | Get-Random -Count ([Math]::Min($SampleCount, $Candidate.Count)))
    $Count.sampled = $Sample.Count
    # RESTORE THE SAMPLE
    $IncludeList = @($Sample | ForEach-Object { '--include'; $_.path })
    $R = Invoke-SoRestic $S (@('restore', $Snap.id, '--target', $TestRoot) + $IncludeList)
    if ($R.Code -ne 0) { throw "restic restore exit $($R.Code)" }
    # COMPARE EACH FILE WITH THE LIVE ONE (A SNAPSHOT PATH /D/a/b IS LIVE D:\a\b AND RESTORED <test root>\D\a\b)
    foreach ($Node in $Sample) {
        if ($Node.path -notmatch '^/([A-Za-z])/(.+)$') { $Count.not_restored++; continue }
        $Rel = $Matches[2] -replace '/', '\'
        $Live = "$($Matches[1]):\$Rel"
        $Restored = Join-Path $TestRoot "$($Matches[1])\$Rel"
        if (-not (Test-Path -LiteralPath $Restored -PathType Leaf)) { $Count.not_restored++; continue }
        $Count.restored++
        try { $LiveItem = Get-Item -LiteralPath $Live -ErrorAction Stop } catch { $Count.skipped_changed++; continue }
        $SnapMtime = [datetime]::Parse($Node.mtime).ToUniversalTime()
        if ($LiveItem.Length -ne $Node.size -or [Math]::Abs(($LiveItem.LastWriteTimeUtc - $SnapMtime).TotalSeconds) -gt 2) { $Count.skipped_changed++; continue }
        try { $LiveHash = (Get-FileHash -LiteralPath $Live -Algorithm SHA256 -ErrorAction Stop).Hash } catch { $Count.skipped_unreadable++; continue }
        $RestoredHash = (Get-FileHash -LiteralPath $Restored -Algorithm SHA256).Hash
        $Count.compared++
        if ($LiveHash -eq $RestoredHash) { $Count.equal++ } else { $Count.different++ }
    }
    $Ok = ($Count.compared -gt 0 -and $Count.different -eq 0 -and $Count.not_restored -eq 0)
} catch {
    Write-Host "STOP: $($_.Exception.Message)"
} finally {
    Clear-SoBackupEnv
    if (Test-Path -LiteralPath $TestRoot) { Remove-Item -LiteralPath $TestRoot -Recurse -Force -ErrorAction SilentlyContinue }
}
$Count.GetEnumerator() | ForEach-Object { Write-Host ("{0,-20} {1}" -f $_.Key, $_.Value) }
if (Test-Path -LiteralPath $TestRoot) { Write-Host "NOTE: $TestRoot could not be removed completely; remove it by hand"; $Ok = $false }
if ($Ok) { Write-Host "OK restore test: every compared file restored identical"; exit 0 } else { Write-Host "NOT OK: see the counts above"; exit 1 }
