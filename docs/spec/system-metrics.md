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

Directory layout under system_metrics_dir, configured as system_metrics_setup.system_metrics_dir (default /var/lib/pyntara/metrics):

1. main_outbox — the intake directory. The only write path of the queue; commit_system_metrics publishes committed files here.
2. temp — temporary files of the commit utility. Copies are written here before publication into main_outbox. Handlers never scan this directory; leftovers of a crash between the hard link and the unlink are never swept (explicit decision).
3. google_script — the Google Drive channel queue. The dispatcher creates one hard link per main_outbox entry here.
4. telegram — the Telegram channel queue, reserved for the future channel. The directory appears when the channel is implemented.
5. main_sent — the sent archive. Senders move successfully sent entries here.

Entry lifecycle:

1. The producer creates an artifact (encrypted PDF in memory, install log, anything) and runs commit_system_metrics FILE.
2. The utility copies the file into temp with the queue file mode and a modification time equal to the commit time, publishes it into main_outbox under the original name plus a random alphanumeric suffix through a hard link, and removes the temp name. The source is never modified.
3. The dispatcher creates one hard link per main_outbox entry in every channel queue, and only after every link succeeds removes the name from main_outbox. A channel enabled later receives only entries committed after its enablement.
4. Every channel drains its queue independently: entries are ordered by modification time according to send_order, the suffix is stripped and the original name is uploaded; on success the entry name is moved to main_sent, on failure it stays for retry with the sqrt(2) interval growth.

Queue rules:

1. Entry names preserve the original file name; hidden files are not filtered. A random alphanumeric suffix of queue_file_suffix_length characters is appended after a dot: <original>.<suffix>. The suffix lets entries with identical original names coexist; the sender strips exactly suffix_length + 1 trailing characters, so the remote server receives the original name.
2. Empty files are rejected at commit; the sender additionally skips empty entries as a second line of defense.
3. Files larger than max_queue_file_size_bytes are rejected at commit; the sender duplicates the check.
4. Symlinks and hard links are treated as the files they point to: the content of the target is committed, the name of the passed path is used.
5. Queue directories and entries carry the strictest permissions: system_metrics_dir_mode for every queue directory, queue_file_mode for every entry, root only.
6. Send order comes from send_order: oldest_first (the default) sends the earliest committed entry first, newest_first the latest. Entries are ordered by modification time, which the commit utility sets to the commit time; ties are broken by name.
7. The utility creates only system_metrics_dir, main_outbox and temp. The channel queues and main_sent are created by the deployed service.
8. main_sent grows without a rotation policy for now; the archive retention is a future decision.

Current stage: commit_system_metrics, the queue config and the directory structure are implemented. The dispatcher and the channel senders are the next stages; main_outbox accumulates entries in the meantime.
