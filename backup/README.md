# Backup (NAS, restic)

Decided 2026-11-12 (`claude/PROPOSAL_2026-11-12_nas_backup_and_ibapi.md`, option A): restic over SFTP to the TrueNAS, plus ZFS
snapshots on the NAS. Every night the desktop backs up, as SYSTEM and from a Windows volume snapshot: the quarantine, the experiment
records and run folders, the backup records, the holdout results, the archive, the move logs, both pilot ledgers and the runner
clone's `.git`. The exact folders are machine settings (`sources.txt`), not in this repository.

| File | Role |
|---|---|
| `backup_common.ps1` | settings, the restic environment, the SFTP command (shared) |
| `run_backup.ps1` | the scheduled run: backup, check; Sundays forget and prune (14 daily, 8 weekly, 24 monthly, 10 yearly); the 1st of each month a check reading 1/12 of the data |
| `restore_test.ps1` | by hand: restores a random sample of unchanged files into `<quarantine>\_restore_test` and compares them with the live files (counts only) |
| `install_backup.ps1` | copies the three scripts into the configuration folder, writes the settings, registers the task |

**Why the task runs a copy.** The task runs `C:\ProgramData\so_backup\run_backup.ps1`, readable and writable only by SYSTEM and
Administrators, never the script in a clone: no account that can edit a clone can have code run as SYSTEM. After every change to
these scripts, run `install_backup.ps1 ... -Apply` again.

**Holdout.** The repository is encrypted (restic). The NAS account has key-only SSH, no SMB or NFS share exists on its dataset, and no
agent or runner account holds the key or the password. Restores go only into `<quarantine>\_restore_test` (the script refuses any
other place); `_restore_test` and `_backup_logs` are never backed up. Logs and status files stay in the quarantine; status files
hold counts only.

**Set up once (done 2026-11-12, by hand, administrator PowerShell):** in `C:\ProgramData\so_backup` (SYSTEM and Administrators only,
owner SYSTEM): `restic.exe` checked against the release's `SHA256SUMS` with its SHA-256 recorded in `restic.exe.sha256`; a random
`restic_password.txt` (copy in the password manager); the SSH key `so_backup_ed25519` (public key on the NAS account); `known_hosts`
with the NAS host key, its fingerprint compared on the NAS; `restic init`.

**Status.** `<log dir>\last_status.json` (`ok`, `step`, `error`, counts, sizes, durations, exit codes), and Task Scheduler's last run
result (0 done, 1 failed, 2 another backup was running).

**Restore (real).** As administrator, with the settings of the configuration folder: `restic snapshots`, then
`restic restore <id> --target <quarantine>\_restore_test --include <path>`; move what is needed back by hand. Never restore into the
development view.
