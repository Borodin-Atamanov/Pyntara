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

Hostname is generated randomly as a proquint word pair ([Hostname](users-and-host.md#hostname)).

Unencrypted PDF versions must never be saved to disk (in-memory generation only).

After send, System Metrics files are saved in a dedicated folder.

## Schedule and retry

System Metrics attempts to send immediately after computer boot.

Retry mode:
The service runs in the normal mode while it can send: every cycle drains all uploadable entries of the Google Drive channel queue. Each entry travels in one call built from google_script_upload_command of [system_metrics_setup], whose {timeout_seconds}, {file_name} and {key} the sender fills with the configured curl timeout, the original name of the entry and the shared auth key; the content arrives on stdin, because a payload argument would hit the argv length limit. When a cycle made at least one send attempt and none succeeded (a curl failure, a timeout or a non-OK answer), the service switches to the retry mode. In the retry mode every cycle sends one randomly chosen uploadable entry, so one permanently rejected entry never blocks the drain of the rest; after n consecutive failed cycles the pause is delay(n) = min(backoff_base_seconds x backoff_multiplier^(n-1), backoff_max_seconds): the first failure waits the base, every further failure multiplies the pause by the integer multiplier until the ceiling. The parameters live in the config/ directory under [system_metrics_setup] (defaults: 2 seconds, a multiplier of 2, a ceiling of 4 hours). All three values are whole seconds, so every delay is a positive whole number of seconds by construction and never drops below the base. A cycle with a successful send, or with no send attempt at all (an empty queue, missing credentials or only non-uploadable entries), returns the service to the normal mode and resets k to zero. The counter lives in memory only, so a service restart starts from the normal mode, which matches the immediate send after boot.

## Collected data

System Metrics additionally includes:
clipboard text (inside encrypted PDF)  
startup network information: attempts to detect addresses/channels (Cloudflare, Yggdrasil, IPv6, etc.), machine's own addresses, and connection availability status

The default collector configuration adds two address modules to the network section, ipv4 and ipv6, which run the network_addresses command of the shared package with their family flag as the last argument. Each reports every address of its family that the machine carries, as a record with the address, the family, the interface, the scope and the ssh command that connects to it: the addresses come from the configured iproute2 query in JSON form (interface_addresses_command of the [engine] table, ip -j addr show by default), so the loopback, link scope, global, bridge and overlay addresses are all present instead of only the ones a scope filter keeps. The [engine] table also carries the vocabulary of that read: address_family_by_flag maps the flag of the command line to the family the report uses, iproute2_address_family_names maps that family to the name iproute2 prints, and link_scope_name is the scope value that counts as a link scope. An IPv6 link scope address is reachable only through its own interface, so its ssh command carries the zone index (fe80::1%eth0) while the address field stays plain. A family the machine does not carry reports empty, because a machine without IPv6 is not a failure. Every address therefore reaches the report with the command that reaches it, so the operator connects from any network the machine joins, the local network included.

The same command builds the ssh command of every address, and the form lives in one place: the client is always verbose (ssh -v), the port is always written even when it equals the default, the user is deliberately absent because the operator chooses it and the ssh agent offers the deployed key, and an anonymity channel carries the local SOCKS proxy of its own router through ProxyCommand (nc -X 5 -x 127.0.0.1:PORT %h %p). The SOCKS proxy host is the loopback address, because i2pd and Tor bind their proxy to the loopback interface, and the proxy port comes from the [i2pd_service_setup] and [tor_setup] tables; the SOCKS port is never reported as a value of its own, it appears inside the command only.

The default collector configuration adds two detection modules to the network section: public_address asks the configured echo services for the address the machine appears under from the internet, in one parallel call over both families, and reports each answer as a record with its ssh command; a family without an answer contributes a reason record instead of an address, and a completely silent detection is an error. The parallel call itself is the shared helper of pyntara.utils, built from curl_parallel_command of the [engine] table, with curl_parallel_write_out printing the configured curl_parallel_source_marker before the effective URL of every transfer, so a merged answer text is split back into one answer per service. country asks the configured country services what country they see and reports the decision together with the answers, so the report says where the machine currently sits; the geographic position lives in the values the services named. Both reuse the service list, the word and the timeouts of the [three_x_ui_xray_setup] table through the single config, so the detection exists once.

The default collector configuration adds three anonymous network modules to the report: i2pd, whose command reads the .b32.i2p tunnel address through the deployed address command, yggdrasil, whose command reads the node self address from the admin socket, and tor_onion, whose command reads the SSH onion address from the hidden service hostname file. Each reports one record with the channel, the address, the port (the sshd port for the overlay and the I2P tunnel, the virtual port of the onion service for Tor), the proxy of the anonymity network and the ssh command; all three read the live source at collection time and fall back to the saved address files written by the provisioning tasks, and the reason of a fallback travels as a note inside the record, so a report keeps the error instead of losing it. When neither source yields an address, the command exits nonzero with an explanation on stderr. The onion address is derived from the key in the hidden service directory, which the provisioning task never recreates, so the address is stable between reports.

The default collector configuration adds a port_forwarding module that reports the assigned remote ports from the state file written by the auto_port_forwarding service: one record per server and forwarded local port, with the server host, the local port, the granted remote port and the ssh command that reaches this machine through that server, where the reverse tunnel delivers the connection to the local SSH daemon. A machine without the state file reports an empty module instead of an error.

The default collector configuration adds a nextdns network module whose command prints the selected NextDNS profile ID from the file the nextdns_setup_system_wide task writes after the selection ([NextDNS profile selection](nextdns-profile.md)). A missing file makes the module report error, so a machine without NextDNS shows the failure in the report instead of silently omitting the profile.

### Address commands

The address commands (network_addresses, i2pd_address, tor_address, yggdrasil_address, port_forwarding_state, public_address_report, country_report) are deployed commands that report live values and fall back to a saved file where one exists. Each prints a JSON document on stdout: the address commands print the records of their channel, and the report keeps the document as structured data. A command that needs a port or a path reads it from the single system config named by its argument, which is the same source the provisioning tasks write, so the report can never name a port the machine does not listen on; this is the one difference from the earlier text convention, which needed no config access. When a live source fails, the reason travels inside the record as a note, so the report keeps the error instead of losing it; when no source yields an address, the command exits nonzero with an explanation on stderr. The per-service details (command line, live source, saved file) are documented in [i2pd](i2pd-service.md#determining-the-address-on-the-target-system), [yggdrasil](yggdrasil-service.md#determining-the-address-on-the-target-system) and [Tor](tor-service.md#determining-the-address-on-the-target-system).

The shape of every record is a value of the config as well, so the commands and the collector agree on it in one place without a copy in each module: report_record_keys maps the meaning of a field to the name it carries (channel, address, port, proxy, ssh, note, server, local_port, remote_port, family, interface, scope), report_json_indent is the indentation of the printed document, and the channel name of a record comes from the section that owns the channel (report_channel_name of i2pd_service_setup, tor_setup, yggdrasil_service_setup and port_forwarding_setup). The ssh command a record carries is built from ssh_report_command_format with the port, the address and, for an anonymity channel, the proxy option of ssh_report_proxy_option_format, which routes the connection through the local SOCKS proxy named by ssh_report_proxy_host and ssh_report_socks_command_format; the shared builder of pyntara.ssh_access is the only place that renders it.

## Installation log

Installation log (full install + messages) is sent to System Metrics as a separate file.

## Queue architecture

The System Metrics queue is the single hand-off point between producers and senders. Any producer (installation log, system snapshot, clipboard) commits a finished artifact through the commit_system_metrics system command; the deployed service drains the queue into the delivery channels and archives sent files.

The spool at the configured system_metrics_setup.spool_dir is the intake pre-queue: the thin commit command, which runs without privileges, publishes files here; the root ingest service moves them into the queue. The spool mode comes from spool_dir_mode: sticky, write and search for everyone, no listing, so spool entry names stay private. Spool entries are created by the commit command with queue_file_mode; a file placed into the spool by hand with looser modes is visible to other users until the ingest moves it, so producers must always use the command.

Directory layout under system_metrics_dir, configured as system_metrics_setup.system_metrics_dir (default /var/lib/pyntara/metrics):

main_outbox — the intake directory of the queue. The ingest service publishes committed files here; the directory name comes from system_metrics_setup.main_outbox_dir.  
temp — temporary files of the ingest service. Copies are written here before publication into main_outbox. Handlers never scan this directory; leftovers of a crash between the hard link and the unlink are never swept (explicit decision). The directory name comes from system_metrics_setup.temp_dir.  
google_script — the Google Drive channel queue. The dispatcher creates one hard link per main_outbox entry here.  
telegram — the Telegram channel queue, reserved for the future channel. The directory appears when the channel is implemented.  
main_sent — the sent archive. Senders move successfully sent entries here.

### Entry lifecycle

The producer creates an artifact (encrypted PDF in memory, install log, anything) and runs commit_system_metrics FILE. The thin command checks that the file is regular and non-empty and publishes it into the spool atomically under the original name with mode 0600 and the commit time; a name that is already pending in the spool is an explicit error, never an overwrite.  
The path unit system_metrics-ingest.path watches the spool with inotify and starts the ingest service system_metrics-ingest.service on every file appearance; there is no polling. The service runs venv_dir/bin/python -m pyntara.metrics_ingest system_config_path, copies each spool file into temp with the queue file mode and the spool modification time (the commit time), publishes it into main_outbox under the original name plus a random alphanumeric suffix through a hard link and removes the spool entry. The source file is never modified.  
The dispatcher creates one hard link per main_outbox entry in every channel queue, and only after every link succeeds removes the name from main_outbox. A channel enabled later receives only entries committed after its enablement.  
Every channel drains its queue independently: entries are ordered by modification time according to send_order, the suffix is stripped and the original name is uploaded; on success the entry name is moved to main_sent, on failure it stays for retry. When a cycle made at least one send attempt and none succeeded, the loop switches to the retry mode described in the Schedule and retry section.

### Queue rules

Entry names preserve the original file name; hidden files are not filtered. A random alphanumeric suffix of queue_file_suffix_length characters is appended after a dot: <original>.<suffix>. The suffix lets entries with identical original names coexist; the sender strips exactly suffix_length + 1 trailing characters, so the remote server receives the original name.  
Empty files are rejected at ingest; the sender additionally skips empty entries as a second line of defense.  
Files larger than max_queue_file_size_bytes are rejected at ingest; the sender duplicates the check.  
Symlinks and hard links are treated as the files they point to: the content of the target is committed, the name of the passed path is used.  
Queue directories and entries carry the strictest permissions: system_metrics_dir_mode for every queue directory, queue_file_mode for every entry, root only.  
Send order comes from send_order: oldest_first (the default) sends the earliest committed entry first, newest_first the latest. Entries are ordered by modification time, which the commit command sets to the commit time; ties are broken by name.  
The ingest service creates only system_metrics_dir, main_outbox and temp. The channel queues and main_sent are created by the deployed service.  
Rejected spool entries (not regular, empty, oversized) are removed from the spool and reported in the journal; a failed publication leaves the spool entry in place so the next ingest run retries it. Spool entries with the spool_temp_prefix (the commit command temporaries) are never ingested.  
main_sent grows without a rotation policy for now; the archive retention is a future decision.

## Commit command

The commit_system_metrics command is a thin generated bash script installed by the system_metrics_setup task. The task renders it from a template at the configured command_path with the spool path, the journal identifier and the temporary prefix embedded from the system config, and sets the mode from command_file_mode. The command needs no config access and no root privileges, so any user can commit. It takes exactly one file argument, verifies that the file is regular and non-empty, copies it into the spool with queue_file_mode and the commit time and publishes it atomically under the original name; every action and every error is mirrored into the system journal under the configured identifier (best effort, like the installer logging). The callers run it through the commit_command argv of [system_metrics_setup], which names command_path and the file argument, so the report collector and the runtime vault backup task call the command the same way. A name collision is an explicit error. The command file is idempotent: the task is done when its content and mode match, rewrites it on change or in force mode, replaces a foreign file on command_path and fails on a directory there.

Current stage: the spool, the thin commit command, the ingest service with its inotify path unit, the queue config, the directory structure, the dispatcher and the Google Drive channel sender are implemented. The service loop dispatches main_outbox entries into the google_script channel and drains it into the web app; sent entries accumulate in main_sent without a rotation policy for now. The Telegram channel and the encrypted PDF generation are the next stages. The retry mode of the Schedule and retry section is implemented: a cycle with send attempts and no successes switches the loop to the single-random-entry retry with the geometric backoff from the config/ directory. The daily send is scheduled through the systemd timer (daily_send_time in the config); the once-a-day gate (skip if less than a day since last send) is not yet implemented.

## Report collector

The report collector is a producer of the System Metrics queue. The systemd timer system_metrics_collector.timer starts the oneshot service system_metrics_collector.service after boot and at the configured daily time; the service reads the single system config, runs the configured console commands, keeps their full output, waits up to the retry window for enough network modules to answer, writes the report as network.json into the system temp directory and commits it through the commit_system_metrics command. All waiting happens inside the service, never in systemd: boot_delay_seconds only sets the OnBootSec of the timer, and the daily time comes from daily_send_time of [system_metrics_setup.collector] in the config/ directory. The collector and the other deployed services run from the dedicated venv system_metrics_setup.venv_dir; the system_metrics_setup task refreshes the venv whenever its installed pyntara version differs from the repository version, so the deployed code follows the repository after every installer run, and a refresh restarts the long-running service.

The collector configuration lives in [system_metrics_setup.collector] of the config/ directory.

Every module is a name and a command as an argv array, never a shell line. A module reports ok when the command exited 0 with non-empty output, empty when it exited 0 with empty output, error otherwise (a nonzero exit, a missing executable or a timeout). The output of every module is trimmed of leading and trailing whitespace before it enters the report, so the trailing newline of every console command, and any stray whitespace from config files or user data, never reaches the telemetry; internal newlines of multi-line output are preserved. A whitespace-only output is empty, because it carries no information. A module whose command printed a JSON array or object contributes it as structured data instead of a string, so the addresses of the machine and the ssh commands that reach them stay records with their fields and the report never has to be parsed again to be used; a bare scalar stays text, because an identifier that happens to be a number is not a document. Unconfigured sources are simply absent from the module lists: sources are added or removed in the config without code changes.

Collection flow:

The service collects every module of both lists and computes ready_percent = the share of ok modules among the network modules; an empty network module list is trivially ready at 100 percent. A module is one source of the network picture, so the share counts sources and never the records inside them: a source that reports thirty addresses weighs exactly as a source that reports one, and the readiness of a machine therefore never depends on how many addresses it carries.  
When ready_percent is at least threshold_percent, the report is committed immediately.  
Otherwise the collection is repeated after the geometric backoff until retry_max_seconds have passed since the first collection; when the window is exhausted, the report is committed as is, whatever the readiness. A threshold of 0 commits after the first collection, a threshold of 100 waits for every network module.

The report is a JSON document: generated_at in the project datetime format YYYY-MM-DD-HH-MM-SS, ready_percent, and the network and system module results, each with name, status and the module output, which is text or the structured document the command printed. The report is written under report_file_name into the system temp directory with the configured report_file_mode, committed through the configured commit_command and the temporary file is removed; a failed commit is journaled at the System Metrics error priority and exits nonzero, so the systemd restart policy retries the collector. The queue keeps the name and the random suffix of the ingest, so daily reports with the same name coexist in the queue.

The first collection runs right after provisioning, without waiting for the first boot. The system_metrics_initial_collect task starts the already deployed collector service once through the configured start_command of the collector table (systemctl start --no-block with the configured service unit name substituted) and reads both the unit name and the command from the config through Context; the non-blocking flag is required because the collector may wait up to its retry window inside the service. The task depends on system_metrics_setup, and its catalog position puts it after i2pd and yggdrasil provisioning in the default task sets, so the first report carries the live anonymous addresses. When the collector unit file is missing, the deployment did not happen and the task is done without starting the collector; a failed start is an error and shows in the install log. The task runs before the final task of the catalog, commit_final_system_metrics (section Runtime vault backup below).

## Runtime vault backup

The last task of the catalog, commit_final_system_metrics, backs the runtime secret vault up off-machine through the System Metrics queue. The task reads the runtime vault path from the local_vault_setup config (docs/spec/secrets-model.md, Runtime storage on the target machine), copies the vault to a temporary file named from the system_metrics_setup.vault_backup_file_name template with {hostname} replaced by the machine hostname, and commits the copy through the configured commit_command, so the queue receives the encrypted vault under the name <hostname>.kdbx and the deployed service sends it to the delivery channels. The task is a producer of the queue: the commit is the hand-off point, the installer never waits for the upload. The temporary copy is created with mode 0600 and always removed. A missing or empty runtime vault and a failed commit are reported as an error, so the install log shows them (no silent failures); a run whose local_vault_setup could not create a vault therefore ends with a visible warning.
