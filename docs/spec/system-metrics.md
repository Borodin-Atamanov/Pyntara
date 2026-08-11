# System Metrics

There is a dedicated System Metrics installation task.

## Network detection

At system start, network availability is checked.
If network is unavailable, retry interval increases by sqrt(2) each attempt (e.g., 1.0 s, 1.4 s, ...).
When network appears, System Metrics attempts to send data.

## Delivery channels

Delivery channels and endpoints come from secrets:
Telegram bot (messages and files)
Google Drive (file uploads)

There are two independent send queues; architecture must allow adding more:
Telegram queue
Google Drive queue

## PDF generation and encryption

System Metrics data is generated as encrypted PDF files.
Encryption: AES-256.

PDF encryption password is generated during Pyntara initialization from:
KeePass salt (decrypted with admin password during installation)
hostname

Hostname is generated randomly: 9 characters (as one of the tasks).

Unencrypted PDF versions must never be saved to disk (in-memory generation only).

After send, System Metrics files are saved in a dedicated folder.

## Schedule and retry

System Metrics attempts to send immediately after computer boot.

Base accumulation/retry behavior:
System Metrics data accumulates for one day
after successful send, next send is scheduled for 12:00 local time
if unsent files exist, retries continue with sqrt(2) interval growth
retries with this scheme run only if more than one day has passed since last send

## Collected data

System Metrics additionally includes:
clipboard text (inside encrypted PDF)
startup network information: attempts to detect addresses/channels (Cloudflare, Yggdrasil, IPv6, etc.), machine's own addresses, and connection availability status

## Installation log

Installation log (full install + messages) is sent to System Metrics as a separate file.

## Queue architecture

The System Metrics queue is the single hand-off point between producers and senders. Any producer (installation log, system snapshot, clipboard) commits a finished artifact through the commit_system_metrics system command; the deployed service drains the queue into the delivery channels and archives sent files.

The spool at system_metrics_setup.spool_dir (default /var/spool/system_metrics) is the intake pre-queue: the thin commit command, which runs without privileges, publishes files here; the root ingest service moves them into the queue. The spool mode is spool_dir_mode (default 1733): sticky, write and search for everyone, no listing, so spool entry names stay private. Spool entries are created by the commit command with mode 0600; a file placed into the spool by hand with looser modes is visible to other users until the ingest moves it, so producers must always use the command.

Directory layout under system_metrics_dir, configured as system_metrics_setup.system_metrics_dir (default /var/lib/pyntara/metrics):

1. main_outbox — the intake directory of the queue. The ingest service publishes committed files here; the directory name comes from system_metrics_setup.main_outbox_dir.
2. temp — temporary files of the ingest service. Copies are written here before publication into main_outbox. Handlers never scan this directory; leftovers of a crash between the hard link and the unlink are never swept (explicit decision). The directory name comes from system_metrics_setup.temp_dir.
3. google_script — the Google Drive channel queue. The dispatcher creates one hard link per main_outbox entry here.
4. telegram — the Telegram channel queue, reserved for the future channel. The directory appears when the channel is implemented.
5. main_sent — the sent archive. Senders move successfully sent entries here.

Entry lifecycle:

1. The producer creates an artifact (encrypted PDF in memory, install log, anything) and runs commit_system_metrics FILE. The thin command checks that the file is regular and non-empty and publishes it into the spool atomically under the original name with mode 0600 and the commit time; a name that is already pending in the spool is an explicit error, never an overwrite.
2. The path unit system_metrics-ingest.path watches the spool with inotify and starts the ingest service system_metrics-ingest.service on every file appearance; there is no polling. The service runs venv_dir/bin/python -m pyntara.metrics_ingest system_config_path, copies each spool file into temp with the queue file mode and the spool modification time (the commit time), publishes it into main_outbox under the original name plus a random alphanumeric suffix through a hard link and removes the spool entry. The source file is never modified.
3. The dispatcher creates one hard link per main_outbox entry in every channel queue, and only after every link succeeds removes the name from main_outbox. A channel enabled later receives only entries committed after its enablement.
4. Every channel drains its queue independently: entries are ordered by modification time according to send_order, the suffix is stripped and the original name is uploaded; on success the entry name is moved to main_sent, on failure it stays for retry with the sqrt(2) interval growth.

Queue rules:

1. Entry names preserve the original file name; hidden files are not filtered. A random alphanumeric suffix of queue_file_suffix_length characters is appended after a dot: <original>.<suffix>. The suffix lets entries with identical original names coexist; the sender strips exactly suffix_length + 1 trailing characters, so the remote server receives the original name.
2. Empty files are rejected at ingest; the sender additionally skips empty entries as a second line of defense.
3. Files larger than max_queue_file_size_bytes are rejected at ingest; the sender duplicates the check.
4. Symlinks and hard links are treated as the files they point to: the content of the target is committed, the name of the passed path is used.
5. Queue directories and entries carry the strictest permissions: system_metrics_dir_mode for every queue directory, queue_file_mode for every entry, root only.
6. Send order comes from send_order: oldest_first (the default) sends the earliest committed entry first, newest_first the latest. Entries are ordered by modification time, which the commit command sets to the commit time; ties are broken by name.
7. The ingest service creates only system_metrics_dir, main_outbox and temp. The channel queues and main_sent are created by the deployed service.
8. Rejected spool entries (not regular, empty, oversized) are removed from the spool and reported in the journal; a failed publication leaves the spool entry in place so the next ingest run retries it. Spool entries with the spool_temp_prefix (the commit command temporaries) are never ingested.
9. main_sent grows without a rotation policy for now; the archive retention is a future decision.

## Commit command

The commit_system_metrics command is a thin generated bash script installed by the system_metrics_setup task. The task renders it from a template at the configured system_metrics_setup.command_path (default /usr/local/bin/commit_system_metrics) with the spool path, the journal identifier and the temporary prefix embedded from the system config, and sets the mode from system_metrics_setup.command_file_mode (default 0755). The command needs no config access and no root privileges, so any user can commit. It takes exactly one file argument, verifies that the file is regular and non-empty, copies it into the spool with mode 0600 and the commit time and publishes it atomically under the original name; every action and every error is mirrored into the system journal under the configured identifier (best effort, like the installer logging). A name collision is an explicit error. The command file is idempotent: the task skips when its content and mode match, rewrites it on change or in force mode, replaces a foreign file on command_path and fails on a directory there.

Current stage: the spool, the thin commit command, the ingest service with its inotify path unit, the queue config and the directory structure are implemented. The dispatcher and the channel senders are the next stages; main_outbox accumulates entries in the meantime.
