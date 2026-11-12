# Security and confidentiality

This repository is public. Two kinds of material must never enter it: credentials, and anything that weakens the research's
holdout. The research's AI agents can read the web, so a public copy of recent market data is a leak.

## Never committed

| What | Where it lives instead |
|---|---|
| Market data of any date: CSVs, notebook outputs, logs, screenshots of tables | the quarantine data tree only |
| IBKR credentials and account numbers | nowhere: you sign in to IB Gateway yourself, and the code never requests account data |
| API keys (FRED from D2), SMTP passwords, tokens | Windows Credential Manager (through `keyring`) and your password manager |
| Machine addresses, share names, folder paths, account names, access rights | `config/local.toml` (git-ignored) and the PowerShell blocks you run by hand |

## Enforced by

- `.gitignore`: data, logs, run records, `config/local.toml`, key files.
- `tests/test_repo_guard.py`, also in CI, fails on any of these in a tracked file:
  - data files, or notebooks with outputs;
  - IPv4 addresses (other than 127.0.0.1), network share paths, Windows user-profile paths;
  - private keys, `password=` / `api_key=` values, IBKR account ids;
  - an import of the research package.
- CI's `secrets` job: gitleaks over the whole history (`.gitleaks.toml`). Only the exact fake values of the tests are allowed,
  never a whole test file.
- A `pre-push` hook (`scripts/machine/install_git_hooks.ps1`, once per clone) runs the repository guard, and gitleaks when it is
  installed, before anything leaves the machine.
- GitHub secret scanning with push protection, turned on in the repository settings.
- The code itself:
  - `ingest.ibkr_client` drops the account list, masks account ids in IBKR's messages and keeps ibapi's logging at WARNING;
  - every printed line (stdout and stderr, once `ingest` is imported) masks dates on or after `print_dates_before` and account ids;
  - `ingest.records.redact_str` masks secret values before anything is printed or written into a manifest;
  - a test checks that the code calls no account, position or order request.

## If something leaks

1. **Rotate the secret first.** Deleting the commit does not undo the leak: forks, caches and archives keep copies.
2. Remove the material from the history, or recreate the repository, which is what was done on 2026-11-08 with the old
   `stock_overflow_data_ingest`.
3. Note it in the project's decisions log.
