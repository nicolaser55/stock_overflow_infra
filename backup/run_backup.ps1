<#
.SYNOPSIS
    The scheduled backup (Task Scheduler, as SYSTEM): restic backup of the configured folders to the NAS, then a check; on Sundays
    forget + prune; on the 1st of each month a check that reads 1/12 of the stored data (all of it once a year).

.DESCRIPTION
    Runs the installed copy in the configuration folder (backup/install_backup.ps1 copies it there), never the repository clone, so
    no account that can edit the clone can have code run as SYSTEM.
      - The folders are read from a Windows volume snapshot (restic --use-fs-snapshot): files the runner is writing are copied as
        they were at one moment.
      - Any problem stops the run and is recorded: a missing setting or source folder, restic.exe not the recorded one, a file that
        could not be read (restic exit 3, an incomplete snapshot), a failed check or prune.
      - Folders named _restore_test and _backup_logs are never backed up.
      - The full restic output goes to <log dir>\backup_<run id>.log; <log dir>\last_status.json and status_<run id>.json hold counts,
        sizes, durations and exit codes only (no file name: names hold dates).
    Exit code: 0 done, 1 failed, 2 another backup was running.
    Retention: 14 daily, 8 weekly, 24 monthly, 10 yearly snapshots (decided 2026-11-12); the NAS keeps its own daily ZFS snapshots
    for 30 days, which the desktop cannot delete.
#>
param([string]$ConfigDir = $PSScriptRoot)
$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'backup_common.ps1')

$Start = Get-Date
$RunId = $Start.ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$Status = [ordered]@{ run_id = $RunId; started_utc = $Start.ToUniversalTime().ToString('o'); ok = $false; step = 'settings'; error = '' }
$StatusDir = $ConfigDir
$Mutex = New-Object System.Threading.Mutex($false, 'Global\so_backup')
# NEVER TWO BACKUPS AT ONCE (A MUTEX LEFT BY A KILLED RUN IS ABANDONED: IT IS OURS THEN)
try { $Got = $Mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $Got = $true }
if (-not $Got) { exit 2 }
try {
    # SETTINGS, LOG FOLDER, SOURCES
    $S = Get-SoBackupSetting $ConfigDir
    New-Item -ItemType Directory -Force -Path $S.LogDir | Out-Null
    $StatusDir = $S.LogDir
    $Log = Join-Path $S.LogDir "backup_$RunId.log"
    foreach ($Source in $S.Sources) { if (-not (Test-Path -LiteralPath $Source -PathType Container)) { throw "a source folder is missing (see sources.txt)" } }
    $Status.source_count = $S.Sources.Count
    Set-SoBackupEnv $S
    # BACKUP FROM A VOLUME SNAPSHOT
    $Status.step = 'backup'
    $R = Invoke-SoRestic $S (@('backup', '--use-fs-snapshot', '--json', '--tag', 'scheduled', '--exclude', '_restore_test', '--exclude', '_backup_logs') + $S.Sources)
    $R.Out | Out-File -LiteralPath $Log -Encoding utf8 -Append
    $Status.backup_exit = $R.Code
    $Status.backup_error_count = @($R.Out | Where-Object { $_ -match '"message_type":"error"' }).Count
    $SummaryLine = @($R.Out | Where-Object { $_ -match '"message_type":"summary"' }) | Select-Object -Last 1
    if ($SummaryLine) {
        $Sum = $SummaryLine | ConvertFrom-Json
        foreach ($Field in @('snapshot_id', 'files_new', 'files_changed', 'files_unmodified', 'total_files_processed', 'total_bytes_processed', 'data_added', 'total_duration')) {
            $Status[$Field] = $Sum.$Field
        }
    }
    if ($R.Code -ne 0 -or -not $SummaryLine) { throw "restic backup exit $($R.Code) (3 = some files could not be read; see the log)" }
    # STRUCTURE CHECK OF THE REPOSITORY
    $Status.step = 'check'
    $R = Invoke-SoRestic $S @('check')
    $R.Out | Out-File -LiteralPath $Log -Encoding utf8 -Append
    $Status.check_exit = $R.Code
    if ($R.Code -ne 0) { throw "restic check exit $($R.Code)" }
    # SUNDAYS: RETENTION
    if ($Start.DayOfWeek -eq [DayOfWeek]::Sunday) {
        $Status.step = 'forget'
        $R = Invoke-SoRestic $S @('forget', '--keep-daily', '14', '--keep-weekly', '8', '--keep-monthly', '24', '--keep-yearly', '10', '--prune')
        $R.Out | Out-File -LiteralPath $Log -Encoding utf8 -Append
        $Status.forget_exit = $R.Code
        if ($R.Code -ne 0) { throw "restic forget / prune exit $($R.Code)" }
    }
    # 1ST OF THE MONTH: READ ONE TWELFTH OF THE DATA (EVERY BYTE ONCE A YEAR)
    if ($Start.Day -eq 1) {
        $Status.step = 'read_data'
        $R = Invoke-SoRestic $S @('check', "--read-data-subset=$($Start.Month)/12")
        $R.Out | Out-File -LiteralPath $Log -Encoding utf8 -Append
        $Status.read_data_exit = $R.Code
        if ($R.Code -ne 0) { throw "restic check --read-data-subset exit $($R.Code)" }
    }
    $Status.step = 'done'
    $Status.ok = $true
} catch {
    $Status.error = "$($_.Exception.Message)"
} finally {
    Clear-SoBackupEnv
    $End = Get-Date
    $Status.ended_utc = $End.ToUniversalTime().ToString('o')
    $Status.duration_seconds = [int]($End - $Start).TotalSeconds
    try {
        $Json = $Status | ConvertTo-Json -Depth 3
        [IO.File]::WriteAllText((Join-Path $StatusDir "status_$RunId.json"), $Json, (New-Object Text.UTF8Encoding $false))
        [IO.File]::WriteAllText((Join-Path $StatusDir 'last_status.json'), $Json, (New-Object Text.UTF8Encoding $false))
    } catch { }
    $Mutex.ReleaseMutex()
}
if ($Status.ok) { exit 0 } else { exit 1 }
