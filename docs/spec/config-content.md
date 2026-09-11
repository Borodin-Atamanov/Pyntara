# Config content

What the config/ directory holds, value type by value type, and what never goes
there. The list of types is closed: a value of a type in the Types section is a
config value, a value of a type in the Exceptions section stays in code, and a
value that fits no type is a config value by the first rule below
(architecture contract, Configuration).

The types describe the shipped config/ directory as it is today and, in the
same words, the values the code still carries outside it. The counts are of the
shipped config at the time of writing and are examples, not limits.

## Rules

Every value the run uses lives in config/, in the section of the task or of the
engine that owns it, unless its type is listed in the Exceptions section. The
rule holds whether the value ever changes: the config is the one place where a
reader sees the machine the installer builds and changes it, so a value hidden
in a module is a defect even when it is stable. A module level constant, a
literal inside a function, a literal in a command list and a default fallback
are all subject to the rule in the same way.

A value of an exception type stays in code and is not repeated anywhere else. A
shared value is written once and imported, never copied (architecture contract,
Configuration).

The types below are the whole list. A new value is placed by its type, without a
new decision: a path is a path whether it names a unit file or a spool
directory, and a number of seconds is a number of seconds whether it is a
command timeout or a pause between attempts.

## Types that go into the config

Paths and names:

Path of a file the run reads, writes or verifies: apt_source_path,
hostname_file, binary_path, swapfile_path, lock_file_path. About 45 values.  
Path of a directory the run reads, writes or verifies: task_data_root,
systemd_unit_dir, download_dir, spool_dir, resolved_conf_dir. About 31 values.  
Name of a systemd unit the run deploys or starts: service_unit_name,
ingest_service_unit_name, ingest_path_unit_name, timer_unit_name. About 18
values.  
Name of a file or artifact the run generates, its prefix and the length of a
generated part: resolved_dropin_file_name, private_key_file_name,
spool_temp_prefix, queue_file_suffix_length. About 11 values.  
Journal identifier of a service or of a command the run deploys:
journal_identifier, service_journal_identifier, commit_journal_identifier.  
Path of a file or directory of a foreign program that the run edits:
/etc/sddm.conf, /etc/sddm.conf.d/20-kubuntu.conf,
/usr/share/plasma/look-and-feel, /usr/bin/python3, /etc/os-release, the paths of
the apt sources.  
Path of an interface of an external API that the run calls: /panel/api/xray/...,
/login, /csrf-token, /VirtualDesktopManager, /files, the path of a release query
of a source repository.

Numbers and modes:

Duration in seconds of a command, a wait, a pause or a retry window:
command_timeout_seconds, notice_timeout, service_restart_seconds,
start_check_retry_delay_seconds. About 52 values.  
Count of retries, attempts, loops or items: curl_retries,
package_install_retries, start_check_attempts, peer_target_count. About 24
values.  
Size in bytes of a cache, an alignment or a queue entry: cache_size_bytes,
alignment_bytes, max_queue_file_size_bytes.  
Percent or fraction of a whole: memory_fraction_percent, disk_fraction,
package_success_threshold_percent, accept_threshold_percent.  
Network port of a listener, a client or a proxy: panel_port, inbound_port,
socks_port, cdp_port.  
Syslog priority of a journal line: error_priority, progress_priority.  
File mode written as four octal digits: file_mode, private_key_file_mode,
swapfile_mode, spool_dir_mode. About 33 values; the reader turns the text into
the number chmod expects.  
Parameter of an external tool whose value is ours to choose: bandwidth, share,
if_mtu, num_introduction_points, cursor size, log rate limit burst.  
Number that describes the machine, the run or the output: fallback_cpu_count,
ram_extra_mb, queue_file_suffix_length, the length of an excerpt of a command
output.

Texts and vocabularies:

Command argv of every command the run invokes, with the arguments that are ours
to choose: nmcli_modify_command, resolvectl_status_command,
kwin_reload_command, panel_restart_command, the update command of the apt
tasks. About 21 values today; the commands that are assembled inside the task
modules belong to this type as well.
URL and URL template of an external service: settings_repo_url,
install_script_url, github_latest_release_url, doh_url_format.  
Host name, domain, IP address and address:port: ubuntu_hosts,
verification_domain, listen_addresses, tor_proxy_address.  
Owner and name pair of a source repository: github_repo of every task that
installs from a release.  
Value of a vocabulary of an external tool where the choice is ours: upstream
mode, send order, touchpad click method, color scheme, log level, address
strategy, share scheme, virtual keyboard input method.  
Text of at most five lines that becomes part of a file the run writes: a section
name, a directive, a header, an option line, the body of the apt keep-debs
drop-in.  
Text that is stored on the machine and read back: the title of a vault entry,
the title of a vault group, a description written into a foreign file.  
Keyboard shortcut handed to a desktop: layout_switch_shortcuts.  
Account name of the machine or of a service: username, sddm_autologin_user,
tor_user, remote_ssh_user.  
Address of an external service used as an identity or as a source of data:
freedesktop.org, www.kde.org, github.com.

Collections:

List of package names, of apt components, of host names, of URLs, of simple
names, of tagged names such as geosite:category-ads-all and
ext-ip:geoip_RU.dat:ru-blocked.  
List of records, each with its own fields: the vault structure, kconfig records,
ssh directives, collector modules, the task catalog.  
Mapping of a name to a value: kde_settings.user_dirs.

## Exceptions

A value of one of these types never goes into the config.

Paths of the kernel and of the devices it exposes: paths under /proc, under /sys
and under /dev, for example /proc/meminfo, /proc/cpuinfo, /sys/block,
/sys/module/zswap/parameters and /dev/zram.  
Regular expressions.  
The wording of a message the run prints or journals, including its prefix, its
suffix and the text of a warning.  
The body of a file longer than five lines: it lives as a template file under
task_data/, and the run fills its placeholders. A body of five lines or fewer is
a value of the type Text of at most five lines that becomes part of a file the
run writes.  
Numbers that encode a protocol or an encoding instead of describing the machine,
the tool or the run: the HTTP response code 200, the size and the flag masks of
a DNS header, the bit shifts of the proquint encoding, the factor 1024 of a unit
conversion.

## Where a value is declared and checked

A config value is declared in four places, and a missing one of them fails the
test suite: the key in config/<section>.toml, the field in
src/pyntara/config/<section>.py, the check in tests/config_checks.py and the key
in tests/config_helpers.py. The Parameters section of the task spec documents
the value for the reader, and the section map is in
[project structure](../guides/project-structure.md#config-section-map).

The runtime reader never checks a value and never invents one: a key that is not
in the document leaves its field without a value, and the task that needed it
reports what it could not do (architecture contract, Configuration). Every rule
of the config lives in tests/config_checks.py, applied to the shipped config by
tests/test_config_coverage.py.

## What is left to move

The migration of the values the code still carries is tracked in
[docs/TODO.md](../TODO.md): the constants of the task modules, the commands
assembled inside functions, the paths of the foreign programs, the file names,
the site addresses, the file modes and the numbers that describe the run.
