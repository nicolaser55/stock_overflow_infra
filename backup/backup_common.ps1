<#
Shared by run_backup.ps1 and restore_test.ps1 (dot-sourced). Reads the machine settings kept in the configuration folder next to
the installed copies of the scripts (default C:\ProgramData\so_backup, readable by SYSTEM and Administrators only). Nothing in this
file is machine-specific: the one-time setup (backup/README.md) and backup/install_backup.ps1 write the settings.

Configuration folder:
    restic.exe, restic.exe.sha256   restic and the SHA-256 recorded at installation (checked before every use)
    restic_password.txt             the repository password (a copy is in Nicolas's password manager)
    so_backup_ed25519, known_hosts  the SSH key of the NAS account and the NAS's verified host key
    repository.txt                  sftp:<user>@<host>:<absolute path>
    sources.txt                     one folder per line (# starts a comment)
    log_dir.txt                     where the logs and status files go (inside the quarantine: never readable by agents)
    cache\                          restic's cache
#>

# FUNCTION: READ AND CHECK THE SETTINGS (THROWS ON ANY PROBLEM: THE CALLER STOPS)
function Get-SoBackupSetting([string]$ConfigDir) {
    $S = @{
        ConfigDir      = $ConfigDir
        Restic         = Join-Path $ConfigDir 'restic.exe'
        ResticSha      = Join-Path $ConfigDir 'restic.exe.sha256'
        PasswordFile   = Join-Path $ConfigDir 'restic_password.txt'
        Key            = Join-Path $ConfigDir 'so_backup_ed25519'
        KnownHosts     = Join-Path $ConfigDir 'known_hosts'
        RepositoryFile = Join-Path $ConfigDir 'repository.txt'
        SourceFile     = Join-Path $ConfigDir 'sources.txt'
        LogDirFile     = Join-Path $ConfigDir 'log_dir.txt'
        Cache          = Join-Path $ConfigDir 'cache'
        Ssh            = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe'
    }
    foreach ($Name in @('Restic', 'ResticSha', 'PasswordFile', 'Key', 'KnownHosts', 'RepositoryFile', 'SourceFile', 'LogDirFile', 'Ssh')) {
        if (-not (Test-Path -LiteralPath $S[$Name])) { throw "missing $($S[$Name])" }
    }
    # RESTIC IS THE ONE RECORDED AT INSTALLATION
    $Recorded = (Get-Content -LiteralPath $S.ResticSha -Raw).Trim().ToUpper()
    $Actual = (Get-FileHash -LiteralPath $S.Restic -Algorithm SHA256).Hash
    if ($Actual -ne $Recorded) { throw "restic.exe SHA-256 differs from the one recorded at installation" }
    # THE REPOSITORY: sftp:<user>@<host>:<absolute path>
    $S.Repository = (Get-Content -LiteralPath $S.RepositoryFile -Raw).Trim()
    if ($S.Repository -notmatch '^sftp:([^@/\s]+)@([^:\s]+):(/\S+)$') { throw "repository.txt is not sftp:<user>@<host>:<absolute path>" }
    $UserHost = "$($Matches[1])@$($Matches[2])"
    # RESTIC SPLITS sftp.command ITSELF AND TREATS "\" AS AN ESCAPE, SO EVERY PATH IN IT USES "/"
    $SshF = $S.Ssh -replace '\\', '/'; $KeyF = $S.Key -replace '\\', '/'; $KhF = $S.KnownHosts -replace '\\', '/'
    $S.SftpCommand = "$SshF -i $KeyF -o UserKnownHostsFile=$KhF -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -o BatchMode=yes -o ServerAliveInterval=60 $UserHost -s sftp"
    # THE SOURCES AND THE LOG FOLDER
    $S.Sources = @(Get-Content -LiteralPath $S.SourceFile | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') })
    if ($S.Sources.Count -eq 0) { throw "no source folder in sources.txt" }
    $S.LogDir = (Get-Content -LiteralPath $S.LogDirFile -Raw).Trim()
    if (-not $S.LogDir) { throw "log_dir.txt is empty" }
    return $S
}

# FUNCTION: SET AND CLEAR RESTIC'S ENVIRONMENT (THE PASSWORD IS READ FROM ITS FILE BY RESTIC; IT NEVER ENTERS A COMMAND LINE)
function Set-SoBackupEnv($S) {
    $env:RESTIC_REPOSITORY = $S.Repository
    $env:RESTIC_PASSWORD_FILE = $S.PasswordFile
    $env:RESTIC_CACHE_DIR = $S.Cache
}
function Clear-SoBackupEnv {
    Remove-Item Env:\RESTIC_REPOSITORY, Env:\RESTIC_PASSWORD_FILE, Env:\RESTIC_CACHE_DIR -ErrorAction SilentlyContinue
}

# FUNCTION: RUN RESTIC; RETURNS ITS EXIT CODE AND EVERY OUTPUT LINE AS TEXT
function Invoke-SoRestic($S, [string[]]$ArgumentList) {
    $Out = & $S.Restic -o "sftp.command=$($S.SftpCommand)" @ArgumentList 2>&1 | ForEach-Object { "$_" }
    return [pscustomobject]@{ Code = $LASTEXITCODE; Out = @($Out) }
}
