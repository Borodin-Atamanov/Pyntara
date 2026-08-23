# Secrets model

## Vault files

The repository contains two KeePass vault files: default.vault and production.vault. Both are in git.

Two password files: default.password (in git, well-known test value) and production.password (in .gitignore, must never be committed).

KeePass database handling is done via a Python library.

## Vault structure

The structure of both vault files is described in the [vault_structure] table of the config/ directory, the single source of truth. The structure is flat: every entry lives directly in the root group and is identified by its unique title; the notes field of each entry explains what it carries and who consumes it. The mapping between the config and the database is one-to-one: the keys of a [[vault_structure.entries]] table are the KeePass entry field names (title, username, password, url, notes), and a key that is not a field name is a config error. Tooling that creates or inspects the vaults reads this table.

The google_script_key entry carries the System Metrics Google Drive web app credentials: the username field holds the Apps Script project script ID, the url field holds the web app deployment endpoint, from which the deployment ID is extracted with the system_metrics_setup.google_script_deployment_url_regex pattern, the password field holds the shared auth key. The deploy helper reads all three and substitutes the auth key into the deployed script template: google_drive_script.js ships as a template whose __GOOGLE_SCRIPT_KEY__ placeholder the deploy step replaces; the System Metrics client sends files to the url. Both consumers take the entry title from system_metrics_setup.google_script_key_entry_title and the URL pattern from system_metrics_setup.google_script_deployment_url_regex in the config/ directory, the single source of truth.

## Vault regeneration

The script secrets/regenerate_vault_by_config.py creates or updates a vault file from the [vault_structure] table of the config/ directory. Run it with the project interpreter, for example .venv/bin/python secrets/regenerate_vault_by_config.py secrets/default.vault; invoked directly, the script re-executes itself with the project virtualenv interpreter. The vault password comes from the first available source in this order:
The PYNTARA_VAULT_PASSWORD environment variable.  
The file next to the vault with the same name and the .password extension, its content trimmed of surrounding whitespace.  
An interactive prompt, only when stdin is a terminal.

The script never writes password files: default.password and production.password are maintained by hand.

The script works in one of three modes:
The vault file is absent or empty: the vault is created from the config, with every configured entry in the root group.  
--overwrite is given: the vault is recreated from the config; existing entries outside the config are lost.  
Otherwise the vault is opened with the password and the entries missing from the root group are added; every existing entry is kept.

Newly created entries carry exactly the configured fields; a missing password field leaves the entry password empty for manual filling. The script exits with code 0 on success and no-op, 1 on any error, 2 on invalid usage; with no arguments it prints its usage help.

## Password prompt

Deprecated: the interactive password prompt is not used and its development is stopped. The installer runs non-interactively. The production vault password is passed through the PYNTARA_VAULT_PASSWORD environment variable, entered by the user via read -s before sudo. Without a password, or with a password that matches no vault, the installer shows a countdown notice and falls back to default.vault. PYNTARA_VAULT_SOURCE (production or default) is optional: when omitted, the source is auto-detected from the password.

Historical summary of the removed prompt (for reference):
The user gets 3 attempts to enter the production vault password via bash read -s (VAULT_PASSWORD_TIMEOUT 333s timeout per attempt).
After 3 failed attempts, the system falls back to default.vault using default.password.
If user does not press any key within VAULT_PASSWORD_TIMEOUT seconds, fallback to default.vault immediately.

## Decrypted values

With a correct password:
the database is decrypted  
some values become environment variables  
some values are saved into internal machine configuration  
some values are one-time-use and must only live in memory during execution

## Runtime storage on the target machine

The runtime secret database and its password live on the target machine in fixed locations, so services that start after install can decrypt the database without user input.

The runtime secret database lives at /var/lib/pyntara/secrets/pyntara.vault. The directory /var/lib/pyntara/secrets/ has mode 0700, the file has mode 0640.  
The vault password lives in a plain file /etc/pyntara/pass with mode 0400 and owner root:root.

The file modes are configurable in the [local_vault_setup] table of the config/ directory as octal strings: secrets_dir_mode, local_vault_file_mode, pass_dir_mode and pass_file_mode.

Passwords are written to files strictly without a trailing newline: the file holds only the password itself, with surrounding whitespace trimmed.

## Runtime vault creation

The local_vault_setup task creates the runtime vault from a source vault on the provisioning machine.

The source vault is not fixed: the task tries the production vault first, then the default vault, both with the vault password from the run (PYNTARA_VAULT_PASSWORD). When neither opens, the task journals a serious error at syslog level 3 and fails without stopping the run.

The future local vault password comes from the pyntara_local_vault_password entry of the source vault, defined in the [vault_structure] table of the config/ directory. The task copies the source vault and re-encrypts the copy with that password, so the source vault password never opens the runtime vault. The copy is written to /var/lib/pyntara/secrets/pyntara.vault (mode 0640, directory 0700) and the password to /etc/pyntara/pass (mode 0400), both owned by root:root.

The task is idempotent: without force it skips when the runtime vault already exists; force mode (PYNTARA_FORCE_TASKS) rewrites the vault and the password file.

The default vault carries a well-known test value for this entry, mirroring its well-known vault password.
