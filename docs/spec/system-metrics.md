# System Metrics

There is a dedicated System Metrics installation task.

## Network detection

At system start the service checks network availability. When network is unavailable, it enters the retry mode of [Schedule and retry](#schedule-and-retry); when network appears, it attempts to send.

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

Hostname is generated randomly as a proquint word pair ([Hostname](docs/spec/users-and-host.md#hostname)).

Unencrypted PDF versions must never be saved to disk (in-memory generation only).

After send, System Metrics files are saved in a dedicated folder.

## Schedule and retry

System Metrics attempts to send immediately after computer boot.

Retry mode:
The service runs in the normal mode while it can send: every cycle drains all uploadable entries of the Google Drive channel queue. When a cycle made at least one send attempt and none succeeded (a curl failure, a timeout or a non-OK answer), the service switches to the retry mode. In the retry mode every cycle sends one randomly chosen uploadable entry, so one permanently rejected entry never blocks the drain of the rest; after n consecutive failed cycles the pause is delay(n) = min(backoff_base_seconds x backoff_multiplier^(n-1), backoff_max_seconds): the first failure waits the base, every further failure multiplies the pause by the integer multiplier until the ceiling. The parameters live in the config/ directory under [system_metrics_setup] (defaults: 2 seconds, a multiplier of 2, a ceiling of 4 hours). All three values are whole seconds, so every delay is a positive whole number of seconds by construction and never drops below the base. A cycle with a successful send, or with no send attempt at all (an empty queue, missing credentials or only non-uploadable entries), returns the service to the normal mode and resets k to zero. The counter lives in memory only, so a service restart starts from the normal mode, which matches the immediate send after boot.

## Collected data

System Metrics additionally includes:
clipboard text (inside encrypted PDF)
startup network information: attempts to detect addresses/channels (Cloudflare, Yggdrasil, IPv6, etc.), machine's own addresses, and connection availability status

The default collector configuration adds four address modules to the network section: ipv4 and ipv6 print the global scope of the address tables, which covers the private subnets of local networks (192.168.0.0/16, 10.0.0.0/8, 172.16.0.0/12) and the public IPv6 addresses; ipv4_link and ipv6_link print the link scope (IPv4 169.254.0.0/16 and IPv6 fe80::/10), which every interface of a connected network carries. Together the four modules put every address of the machine in its local and global networks into the report, so the operator sees the current network state of the target machine from any network it joins.

The default collector configuration adds three anonymous network modules to the report: i2pd, whose command prints the .b32.i2p tunnel address through the deployed address command, yggdrasil, whose command prints the node self address from the admin socket, and tor_onion, whose command prints the SSH onion address from the hidden service hostname file. All three read the live source at collection time and fall back to the saved address files written by the provisioning tasks; when the fallback is used, the reason appears in the module output, and when the live source fails completely the raw utility output lands in the module output, so errors are reported as they are and never silently dropped. The onion address is derived from the key in the hidden service directory, which the provisioning task never recreates, so the address is stable between reports.

The default collector configuration adds a nextdns network module whose command prints the selected NextDNS profile ID from the file the nextdns_setup_system_wide task writes after the selection ([NextDNS profile selection](docs/spec/nextdns-profile.md)). A missing file makes the module report error, so a machine without NextDNS shows the failure in the report instead of silently omitting the profile.

### Address commands

The anonymous address modules (i2pd, yggdrasil, tor_onion) run deployed commands that report the live address first and fall back to a saved file: the command reads the live source (the service keys or hostname file, or the admin socket) and prints the address on stdout, and when the live source fails reads the saved address file written by the provisioning task. When the fallback is used, the reason goes to the following stdout line, so the module output keeps the error instead of losing it; when neither source yields an address, the command exits nonzero with an explanation on stderr. The commands need no config access. The per-service details (command line, live source, saved file) are documented in [i2pd](docs/spec/i2pd-service.md#determining-the-address-on-the-target-system), [yggdrasil](docs/spec/yggdrasil-service.md#determining-the-address-on-the-target-system) and [Tor](docs/spec/tor-service.md#determining-the-address-on-the-target-system).

## Installation log

Installation log (full install + messages) is sent to System Metrics as a separate file.

## Queue architecture

The System Metrics queue is the single hand-off point between producers and senders. Any producer (installation log, system snapshot, clipboard) commits a finished artifact through the commit_system_metrics system command; the deployed service drains the queue into the delivery channels and archives sent files.

The spool at the configured system_metrics_setup.spool_dir is the intake pre-queue: the thin commit command, which runs without privileges, publishes files here; the root ingest service moves them into the queue. The spool mode comes from spool_dir_mode: sticky, write and search for everyone, no listing, so spool entry names stay private. Spool entries are created by the commit command with queue_file_mode; a file placed into the spool by hand with looser modes is visible to other users until the ingest moves it, so producers must always use the command.

Directory layout under system_metrics_dir, configured as system_metrics_setup.system_metrics_dir (default /var/lib/pyntara/metrics):

1. main_outbox — the intake directory of the queue. The ingest service publishes committed files here; the directory name comes from system_metrics_setup.main_outbox_dir.
2. temp — temporary files of the ingest service. Copies are written here before publication into main_outbox. Handlers never scan this directory; leftovers of a crash between the hard link and the unlink are never swept (explicit decision). The directory name comes from system_metrics_setup.temp_dir.
3. google_script — the Google Drive channel queue. The dispatcher creates one hard link per main_outbox entry here.
4. telegram — the Telegram channel queue, reserved for the future channel. The directory appears when the channel is implemented.
5. main_sent — the sent archive. Senders move successfully sent entries here.

### Entry lifecycle

1. The producer creates an artifact (encrypted PDF in memory, install log, anything) and runs commit_system_metrics FILE. The thin command checks that the file is regular and non-empty and publishes it into the spool atomically under the original name with mode 0600 and the commit time; a name that is already pending in the spool is an explicit error, never an overwrite.
2. The path unit system_metrics-ingest.path watches the spool with inotify and starts the ingest service system_metrics-ingest.service on every file appearance; there is no polling. The service runs venv_dir/bin/python -m pyntara.metrics_ingest system_config_path, copies each spool file into temp with the queue file mode and the spool modification time (the commit time), publishes it into main_outbox under the original name plus a random alphanumeric suffix through a hard link and removes the spool entry. The source file is never modified.
3. The dispatcher creates one hard link per main_outbox entry in every channel queue, and only after every link succeeds removes the name from main_outbox. A channel enabled later receives only entries committed after its enablement.
4. Every channel drains its queue independently: entries are ordered by modification time according to send_order, the suffix is stripped and the original name is uploaded; on success the entry name is moved to main_sent, on failure it stays for retry. When a cycle made at least one send attempt and none succeeded, the loop switches to the retry mode described in the Schedule and retry section.

### Queue rules

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

The commit_system_metrics command is a thin generated bash script installed by the system_metrics_setup task. The task renders it from a template at the configured command_path with the spool path, the journal identifier and the temporary prefix embedded from the system config, and sets the mode from command_file_mode. The command needs no config access and no root privileges, so any user can commit. It takes exactly one file argument, verifies that the file is regular and non-empty, copies it into the spool with queue_file_mode and the commit time and publishes it atomically under the original name; every action and every error is mirrored into the system journal under the configured identifier (best effort, like the installer logging). A name collision is an explicit error. The command file is idempotent: the task skips when its content and mode match, rewrites it on change or in force mode, replaces a foreign file on command_path and fails on a directory there.

Current stage: the spool, the thin commit command, the ingest service with its inotify path unit, the queue config, the directory structure, the dispatcher and the Google Drive channel sender are implemented. The service loop dispatches main_outbox entries into the google_script channel and drains it into the web app; sent entries accumulate in main_sent without a rotation policy for now. The Telegram channel and the encrypted PDF generation are the next stages. The retry mode of the Schedule and retry section is implemented: a cycle with send attempts and no successes switches the loop to the single-random-entry retry with the geometric backoff from the config/ directory. The daily send is scheduled through the systemd timer (daily_send_time in the config); the once-a-day gate (skip if less than a day since last send) is not yet implemented.

## Report collector

The report collector is a producer of the System Metrics queue. The systemd timer system_metrics_collector.timer starts the oneshot service system_metrics_collector.service after boot and at the configured daily time; the service reads the single system config, runs the configured console commands, keeps their full output, waits up to the retry window for enough network modules to answer, writes the report as network.json into the system temp directory and commits it through the commit_system_metrics command. All waiting happens inside the service, never in systemd: boot_delay_seconds only sets the OnBootSec of the timer, and the daily time comes from daily_send_time of [system_metrics_setup.collector] in the config/ directory. The collector and the other deployed services run from the dedicated venv system_metrics_setup.venv_dir; the system_metrics_setup task refreshes the venv whenever its installed pyntara version differs from the repository version, so the deployed code follows the repository after every installer run, and a refresh restarts the long-running service.

The collector configuration lives in [system_metrics_setup.collector] of the config/ directory.

Every module is a name and a command as an argv array, never a shell line. A module reports ok when the command exited 0 with non-empty output, empty when it exited 0 with empty output, error otherwise (a nonzero exit, a missing executable or a timeout). The output of every module is trimmed of leading and trailing whitespace before it enters the report, so the trailing newline of every console command, and any stray whitespace from config files or user data, never reaches the telemetry; internal newlines of multi-line output are preserved. A whitespace-only output is empty, because it carries no information. Unconfigured sources are simply absent from the module lists: sources are added or removed in the config without code changes.

Collection flow:

1. The service collects every module of both lists and computes ready_percent = the share of ok modules among the network modules; an empty network module list is trivially ready at 100 percent.
2. When ready_percent is at least threshold_percent, the report is committed immediately.
3. Otherwise the collection is repeated after the geometric backoff until retry_max_seconds have passed since the first collection; when the window is exhausted, the report is committed as is, whatever the readiness. A threshold of 0 commits after the first collection, a threshold of 100 waits for every network module.

The report is a JSON document: generated_at in the project datetime format YYYY-MM-DD-HH-MM-SS, ready_percent, and the network and system module results, each with name, status and the trimmed output. The report is written under report_file_name into the system temp directory with mode 0600, committed through the commit_system_metrics command and the temporary file is removed; a failed commit is journaled at the System Metrics error priority and exits nonzero, so the systemd restart policy retries the collector. The queue keeps the name and the random suffix of the ingest, so daily reports with the same name coexist in the queue.

The first collection runs right after provisioning, without waiting for the first boot. The last task of the catalog, system_metrics_initial_collect, starts the already deployed collector service once with systemctl start --no-block and reads the service unit name from the config through Context; the non-blocking flag is required because the collector may wait up to its retry window inside the service. The task depends on system_metrics_setup, and its catalog position puts it after i2pd and yggdrasil provisioning in the default task sets, so the first report carries the live anonymous addresses. When the collector unit file is missing, the deployment did not happen and the task skips; a failed start is an error and shows in the install log.
