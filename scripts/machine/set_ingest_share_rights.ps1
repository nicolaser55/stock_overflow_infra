<#
.SYNOPSIS
    Narrows the rights of the account the laptop uses to reach the quarantine share: Modify on <quarantine>\store01_rawzone only.

.DESCRIPTION
    Run on the desktop, in an administrator PowerShell. Dry run unless -Apply.

    Target state (allow-list model: grants only, no deny entries are added or removed):
      - <quarantine> root:              the account may list this folder only (RX, no inheritance), so the share opens;
      - <quarantine>\store01_rawzone:  Modify, inherited by everything below (raw, staging, _ingest records, backups);
      - every other folder of the quarantine (pipeline zones, refresh logs, ...): no access for the account;
      - the SMB share:                  Change for the account (was Full: Full also allows changing permissions over SMB).
    Entries of every other account (owner, administrators, SYSTEM, the full denies of the agent and runner accounts) are not touched.

    The script prints the current entries, the changes, and the entries read back afterwards, and exits 1 if the read-back
    does not show the target state. The read-back also looks at every folder two levels below the quarantine root: an entry of
    the account anywhere outside store01_rawzone (an old explicit grant, for example) or a folder whose rights cannot be read
    fails it. -CheckOnly runs that read-back alone and changes nothing.

.EXAMPLE
    .\scripts\machine\set_ingest_share_rights.ps1 -QuarantinePath "D:\<folder>\quarantine" -Account "<account>" -ShareName "<share>"
    .\scripts\machine\set_ingest_share_rights.ps1 -QuarantinePath "D:\<folder>\quarantine" -Account "<account>" -ShareName "<share>" -Apply
    .\scripts\machine\set_ingest_share_rights.ps1 -QuarantinePath "D:\<folder>\quarantine" -Account "<account>" -ShareName "<share>" -CheckOnly
#>
param(
    [Parameter(Mandatory = $true)][string]$QuarantinePath,
    [Parameter(Mandatory = $true)][string]$Account,
    [Parameter(Mandatory = $true)][string]$ShareName,
    [switch]$Apply,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

# RESOLVE THE NAMES
$QualifiedAccount = if ($Account.Contains("\")) { $Account } else { "$env:COMPUTERNAME\$Account" }
$RawzonePath = Join-Path $QuarantinePath "store01_rawzone"
if (-not (Test-Path -LiteralPath $RawzonePath -PathType Container)) { throw "No store01_rawzone folder in $QuarantinePath" }
$Share = Get-SmbShare -Name $ShareName
if ($Share.Path.TrimEnd("\") -ne (Resolve-Path -LiteralPath $QuarantinePath).Path.TrimEnd("\")) { throw "The share $ShareName points to $($Share.Path), not to $QuarantinePath" }

# FUNCTION: SHOW THE ENTRIES OF THE ACCOUNT
function Show-State([string]$Label) {
    Write-Host "`n--- $Label ---"
    foreach ($Path in @($QuarantinePath, $RawzonePath)) {
        $Lines = (icacls $Path) | Where-Object { $_ -match [regex]::Escape($QualifiedAccount) }
        Write-Host ("{0}: {1}" -f $Path, ($(if ($Lines) { ($Lines | ForEach-Object { $_.Trim() }) -join " | " } else { "(no entry)" })))
    }
    Get-SmbShareAccess -Name $ShareName | Where-Object AccountName -eq $QualifiedAccount | ForEach-Object { Write-Host "share ${ShareName}: $($_.AccessControlType) $($_.AccessRight)" }
}

# FUNCTION: FOLDERS (TWO LEVELS BELOW THE ROOT, OUTSIDE store01_rawzone) WHERE THE ACCOUNT HAS AN ENTRY, OR WHOSE RIGHTS CANNOT BE READ
function Get-OutsideEntryList {
    $RawPrefix = $RawzonePath.TrimEnd("\")
    $ListErr = @()
    $Level1 = @(Get-ChildItem -LiteralPath $QuarantinePath -Directory -Force -ErrorAction SilentlyContinue -ErrorVariable +ListErr)
    $Level2 = @($Level1 | ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Directory -Force -ErrorAction SilentlyContinue -ErrorVariable +ListErr })
    $Bad = @()
    # A FOLDER THAT CANNOT BE LISTED CANNOT BE CHECKED (FAIL CLOSED)
    if ($ListErr.Count -gt 0) { $Bad += "($($ListErr.Count) folder(s) could not be listed)" }
    foreach ($Folder in @($Level1 + $Level2)) {
        $Full = $Folder.FullName.TrimEnd("\")
        if ($Full -ieq $RawPrefix -or $Full.StartsWith($RawPrefix + "\", [System.StringComparison]::OrdinalIgnoreCase)) { continue }
        $Lines = icacls $Full
        if ($LASTEXITCODE -ne 0) { $Bad += "$Full (rights unreadable)"; continue }
        if ($Lines | Where-Object { $_ -match [regex]::Escape($QualifiedAccount) }) { $Bad += $Full }
    }
    return ,$Bad
}

# FUNCTION: THE FULL READ-BACK (TRUE WHEN THE TARGET STATE HOLDS)
function Test-TargetState {
    $RootLines = @((icacls $QuarantinePath) | Where-Object { $_ -match [regex]::Escape($QualifiedAccount) })
    $RawLines = @((icacls $RawzonePath) | Where-Object { $_ -match [regex]::Escape($QualifiedAccount) })
    $ShareRight = (Get-SmbShareAccess -Name $ShareName | Where-Object AccountName -eq $QualifiedAccount).AccessRight
    $Outside = Get-OutsideEntryList
    foreach ($Path in $Outside) { Write-Host "  entry (or unreadable rights) outside store01_rawzone: $Path" }
    return (($RootLines.Count -eq 1) -and ($RootLines[0] -match ":\(RX\)\s*$") -and ($RawLines -match "\(OI\)\(CI\)\(M\)") -and
            (-not ($RawLines -match "\(I\)")) -and ($ShareRight -eq "Change") -and ($Outside.Count -eq 0))
}

Show-State "current entries of $QualifiedAccount"

if ($CheckOnly) {
    if (Test-TargetState) { Write-Host "`nOK: root list-only, store01_rawzone Modify, share Change, no entry elsewhere."; exit 0 }
    Write-Host "`nNOT OK: the current state differs from the target state (see above)."; exit 1
}

Write-Host "`nPlanned changes:"
Write-Host "  1. icacls `"$QuarantinePath`" /remove:g $QualifiedAccount      (removes the grant on the root, and with it the inherited grant below)"
Write-Host "  2. icacls `"$QuarantinePath`" /grant ${QualifiedAccount}:(RX)  (this folder only: list the root, no access to its contents)"
Write-Host "  3. icacls `"$RawzonePath`" /grant ${QualifiedAccount}:(OI)(CI)(M)"
Write-Host "  4. share ${ShareName}: Full -> Change for $QualifiedAccount"

if (-not $Apply) {
    Write-Host "`n[DRY RUN] Nothing changed. Re-run with -Apply."
    exit 0
}

# APPLY (GRANT ON store01_rawzone FIRST, SO THE ACCOUNT NEVER LOSES ITS WORKING FOLDER IN BETWEEN); STOP AT THE FIRST FAILED COMMAND
icacls $RawzonePath /grant "${QualifiedAccount}:(OI)(CI)(M)" | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "STOP: icacls grant on store01_rawzone failed"; exit 1 }
icacls $QuarantinePath /remove:g $QualifiedAccount | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "STOP: icacls remove on the root failed"; exit 1 }
icacls $QuarantinePath /grant "${QualifiedAccount}:(RX)" | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "STOP: icacls grant on the root failed"; exit 1 }
Revoke-SmbShareAccess -Name $ShareName -AccountName $QualifiedAccount -Force | Out-Null
Grant-SmbShareAccess -Name $ShareName -AccountName $QualifiedAccount -AccessRight Change -Force | Out-Null

Show-State "entries read back"

# CHECK THE READ-BACK (ROOT, store01_rawzone, SHARE, AND NO ENTRY ELSEWHERE)
if (Test-TargetState) { Write-Host "`nOK: root list-only, store01_rawzone Modify, share Change, no entry elsewhere." } else { Write-Host "`nNOT OK: the read-back differs from the target state (see above)."; exit 1 }
