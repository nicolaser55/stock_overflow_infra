<#
.SYNOPSIS
    Installs a pre-push hook in this clone: the repository guard (and gitleaks, when it is installed) runs before every push.

.DESCRIPTION
    This repository is public, and CI scans only after a push, when the content is already public. The hook stops the push instead:
      - tests/test_repo_guard.py (no data, notebook outputs, machine paths or addresses, credentials, account ids) with the venv's
        Python; no venv means no push (fail closed);
      - "gitleaks git" over the history, only when gitleaks is on the PATH.
    Run it once per clone, from the repository root. Safe to run twice (the hook is rewritten). Remove .git\hooks\pre-push to undo.

.EXAMPLE
    .\scripts\machine\install_git_hooks.ps1
#>
$ErrorActionPreference = "Continue"

# THE CLONE'S HOOK FOLDER
$Top = (git rev-parse --show-toplevel 2>&1 | ForEach-Object { "$_" }) -join ""
if ($LASTEXITCODE -ne 0) { Write-Host "STOP: not inside a git clone"; exit 1 }
$HookDir = (git rev-parse --git-path hooks 2>&1 | ForEach-Object { "$_" }) -join ""
if ($LASTEXITCODE -ne 0) { Write-Host "STOP: cannot find the hooks folder"; exit 1 }
if (-not [System.IO.Path]::IsPathRooted($HookDir)) { $HookDir = Join-Path $Top $HookDir }
New-Item -ItemType Directory -Force -Path $HookDir | Out-Null

# THE HOOK (A SH SCRIPT: GIT FOR WINDOWS RUNS HOOKS WITH ITS OWN BASH), WRITTEN WITH LF ENDINGS AND NO BYTE ORDER MARK
$Hook = @(
    '#!/bin/sh',
    '# stock_overflow_infra pre-push guard (installed by scripts/machine/install_git_hooks.ps1)',
    'top="$(git rev-parse --show-toplevel)" || exit 1',
    'py="$top/venv-infra/Scripts/python.exe"',
    '[ -x "$py" ] || py="$top/venv-infra/bin/python"',
    'if [ ! -x "$py" ]; then echo "pre-push: no venv-infra Python, so the repository guard cannot run: push stopped"; exit 1; fi',
    '"$py" "$top/tests/test_repo_guard.py" || { echo "pre-push: repository guard failed: push stopped"; exit 1; }',
    'if command -v gitleaks >/dev/null 2>&1; then',
    '    gitleaks git --no-banner --redact "$top" || { echo "pre-push: gitleaks found something: push stopped"; exit 1; }',
    'else',
    '    echo "pre-push: gitleaks is not installed; CI runs it after the push"',
    'fi',
    'exit 0'
) -join "`n"
$HookPath = Join-Path $HookDir "pre-push"
[System.IO.File]::WriteAllText($HookPath, $Hook + "`n", (New-Object System.Text.UTF8Encoding $false))
Write-Host "OK pre-push hook written to $HookPath"
