<#
.SYNOPSIS
    Installs (or updates) the backup on this machine: copies the backup scripts into the configuration folder, writes the machine
    settings there, and registers the daily task that runs the backup as SYSTEM. Administrator PowerShell; dry run unless -Apply.

.DESCRIPTION
    Before it: restic.exe and restic.exe.sha256, restic_password.txt, so_backup_ed25519 and known_hosts must already be in the
    configuration folder (they are set up by hand once; see backup/README.md).
    It then:
      1. checks every source folder and the log folder's parent exist, and the repository is sftp:<user>@<host>:<absolute path>;
      2. copies backup_common.ps1, run_backup.ps1 and restore_test.ps1 from this clone into the configuration folder and checks the
         copies' SHA-256;
      3. writes repository.txt, sources.txt and log_dir.txt there;
      4. gives every file the folder's access only (SYSTEM and Administrators, allow-list), owner SYSTEM, and reads it back;
      5. registers the task (daily at -At, as SYSTEM, highest privileges, 12 h limit, never two at once, starts late if missed) that
         runs the installed run_backup.ps1, and reads it back.
    Safe to run twice (the copies, settings and task are replaced). Run it again after every change to the backup scripts.

.EXAMPLE
    .\backup\install_backup.ps1 -Repository 'sftp:<user>@<nas>:/mnt/<pool>/<dataset>/restic' -LogDir 'D:\<folder>\quarantine\_backup_logs' `
        -SourcePath 'D:\<folder>\quarantine', 'D:\<folder>\backup_records' -Apply
#>
param(
    [Parameter(Mandatory = $true)][string]$Repository,
    [Parameter(Mandatory = $true)][string]$LogDir,
    [Parameter(Mandatory = $true)][string[]]$SourcePath,
    [string]$ConfigDir = 'C:\ProgramData\so_backup',
    [string]$TaskName = 'SO Backup',
    [string]$At = '04:10',
    [switch]$Apply
)
$ErrorActionPreference = 'Continue'

# ADMINISTRATOR, PREREQUISITES, SETTINGS
$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $IsAdmin) { Write-Host "STOP: run this in an administrator PowerShell"; exit 1 }
foreach ($Name in @('restic.exe', 'restic.exe.sha256', 'restic_password.txt', 'so_backup_ed25519', 'known_hosts')) {
    if (-not (Test-Path -LiteralPath (Join-Path $ConfigDir $Name))) { Write-Host "STOP: $Name is not in $ConfigDir (see backup/README.md)"; exit 1 }
}
if ($Repository -notmatch '^sftp:([^@/\s]+)@([^:\s]+):(/\S+)$') { Write-Host "STOP: -Repository must be sftp:<user>@<host>:<absolute path>"; exit 1 }
foreach ($Source in $SourcePath) { if (-not (Test-Path -LiteralPath $Source -PathType Container)) { Write-Host "STOP: source folder not found: $Source"; exit 1 } }
if (-not (Test-Path -LiteralPath (Split-Path -Parent $LogDir) -PathType Container)) { Write-Host "STOP: the parent of -LogDir does not exist"; exit 1 }
$Here = $PSScriptRoot
$ScriptList = @('backup_common.ps1', 'run_backup.ps1', 'restore_test.ps1')
foreach ($Name in $ScriptList) { if (-not (Test-Path -LiteralPath (Join-Path $Here $Name))) { Write-Host "STOP: $Name is not next to this script"; exit 1 } }

Write-Host "Plan:"
Write-Host "  copy $($ScriptList -join ', ') into $ConfigDir"
Write-Host "  repository: $Repository"
Write-Host "  log folder: $LogDir"
Write-Host "  sources ($($SourcePath.Count)):"; $SourcePath | ForEach-Object { Write-Host "    $_" }
Write-Host "  task '$TaskName': daily at $At as SYSTEM, running $ConfigDir\run_backup.ps1"
if (-not $Apply) { Write-Host "`n[DRY RUN] Nothing changed. Re-run with -Apply."; exit 0 }

# 1. COPY THE SCRIPTS AND CHECK THE COPIES
foreach ($Name in $ScriptList) {
    $From = Join-Path $Here $Name; $To = Join-Path $ConfigDir $Name
    Copy-Item -LiteralPath $From -Destination $To -Force
    if ((Get-FileHash -LiteralPath $From -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $To -Algorithm SHA256).Hash) { Write-Host "STOP: the copy of $Name differs"; exit 1 }
}
# 2. THE SETTINGS (UTF-8 WITHOUT BYTE ORDER MARK)
$Enc = New-Object Text.UTF8Encoding $false
[IO.File]::WriteAllText((Join-Path $ConfigDir 'repository.txt'), $Repository, $Enc)
[IO.File]::WriteAllText((Join-Path $ConfigDir 'log_dir.txt'), $LogDir, $Enc)
[IO.File]::WriteAllText((Join-Path $ConfigDir 'sources.txt'), (($SourcePath -join "`r`n") + "`r`n"), $Enc)
New-Item -ItemType Directory -Force -Path (Join-Path $ConfigDir 'cache') | Out-Null
# 3. ACCESS: THE FOLDER'S ONLY (SYSTEM, ADMINISTRATORS), OWNER SYSTEM; READ BACK
# Compared by SID, not by name. /grant:r only replaces the grants of the SIDs it names, so any other explicit entry on the folder
# (Explorer adds one for the signed-in user when an administrator clicks "Continue" to open the folder) is removed one by one.
$Allowed = @('S-1-5-18', 'S-1-5-32-544')
function Get-SoOwnerSid($Acl) {
    try { return (New-Object Security.Principal.NTAccount $Acl.Owner).Translate([Security.Principal.SecurityIdentifier]).Value } catch { return "$($Acl.Owner)" }
}
function Get-SoAceSid($Ace) {
    try { return $Ace.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } catch { return "$($Ace.IdentityReference)" }
}
icacls $ConfigDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "STOP: could not set the folder ACL"; exit 1 }
$Extra = @((Get-Acl -LiteralPath $ConfigDir).Access | ForEach-Object { Get-SoAceSid $_ } | Where-Object { $_ -notin $Allowed } | Sort-Object -Unique)
foreach ($Sid in $Extra) {
    if ($Sid -notmatch '^S-1-[0-9-]+$') { Write-Host "STOP: an entry on $ConfigDir has no SID ($Sid); remove it by hand"; exit 1 }
    icacls $ConfigDir /remove "*$Sid" | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "STOP: could not remove an entry from $ConfigDir"; exit 1 }
}
Write-Host "Removed $($Extra.Count) extra entr$(if ($Extra.Count -eq 1) { 'y' } else { 'ies' }) from the folder ACL"
Get-ChildItem -LiteralPath $ConfigDir -Recurse -Force | ForEach-Object { icacls $_.FullName /reset | Out-Null }
icacls $ConfigDir /setowner '*S-1-5-18' /T | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "STOP: could not set the owner"; exit 1 }
foreach ($Item in @(Get-Item -LiteralPath $ConfigDir) + @(Get-ChildItem -LiteralPath $ConfigDir -Recurse -Force)) {
    $Acl = Get-Acl -LiteralPath $Item.FullName
    $Others = @($Acl.Access | Where-Object { (Get-SoAceSid $_) -notin $Allowed })
    if ($Others.Count -gt 0) { Write-Host "STOP: $($Item.FullName) is readable by $($Others[0].IdentityReference)"; exit 1 }
    if ((Get-SoOwnerSid $Acl) -ne 'S-1-5-18') { Write-Host "STOP: $($Item.FullName) is owned by $($Acl.Owner)"; exit 1 }
}
Write-Host "OK $ConfigDir and every file in it: SYSTEM and Administrators only, owner SYSTEM"
# 4. THE TASK
$PowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$(Join-Path $ConfigDir 'run_backup.ps1')`""
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$Principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 12) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Description 'Stock Overflow: restic backup to the NAS (stock_overflow_infra/backup)' -Force | Out-Null
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $Task -or $Task.Principal.UserId -notmatch 'SYSTEM$' -or $Task.Principal.RunLevel -ne 'Highest' -or $Task.Actions[0].Arguments -notmatch [regex]::Escape((Join-Path $ConfigDir 'run_backup.ps1'))) {
    Write-Host "STOP: the task did not read back as registered"; exit 1
}
Write-Host "OK task '$TaskName': daily at $At as SYSTEM, highest privileges, runs $ConfigDir\run_backup.ps1"
exit 0
