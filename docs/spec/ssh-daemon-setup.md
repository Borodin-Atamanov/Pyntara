# SSH daemon setup

There is a dedicated SSH server task: ssh_daemon_setup.

The task installs the SSH server package, runs its systemd service and patches the daemon configuration through a drop-in file, so passwordless login with the pre-generated key pair works out of the box. The task belongs to all install modes and deploys the keys into the home directories of the configured users.

## Key pair

The key pair lives in the repository under task_data/ssh_daemon_setup/: the private key id_ed25519 and the public key id_ed25519.pub. Both files are committed to the repository. The deployed file names equal the repository names and match the OpenSSH default identity name id_ed25519, so the client offers the key automatically on every connection, without -i or ssh-add. The private key is an OpenSSH private key encrypted with a strong pass phrase, so committing it is safe: the pass phrase is never stored in the repository, in the config or on the target machine. The task copies the private key as is, still encrypted, and never needs the pass phrase. The pass phrase stays on the target: the first connection prompts for it, or the user runs ssh-add once to load the key into the agent. A key deployed under another name by an earlier task version is a harmless leftover and is removed by hand; the task does not clean it up.

## Configuration ownership

The task never rewrites sshd_config itself. The main configuration is patched through the drop-in at the configured sshd_config_dropin_path:

The task checks that sshd_config has an Include directive that pulls the drop-in directory in. The check matches every Include pattern against the drop-in path with glob semantics, resolving relative patterns against the directory of sshd_config. A missing Include is a warning of a completed task and the drop-in is written anyway: the configured directives are the part of the machine the task owns, and they start to work the moment the directive appears, so the work is not thrown away by a line missing from a file the task does not own.  
The directives are written through augeas (augtool from the augeas-tools package, which the task installs itself when augtool is missing, so it never waits for another task to provide the tool). augeas parses the real syntax and updates only what differs: a directive that is already present with the same value is left untouched, a directive with a different value is updated, a directive that is no longer configured is removed, and the ownership comment is guaranteed. The drop-in is owned by the task: a manual edit is reverted on the next run.  
An empty directives list removes the drop-in, so the task can revoke its own settings.  
The keepalive pair inside the list serves another feature: ClientAliveInterval and ClientAliveCountMax decide how long the daemon keeps a session whose peer went silent, and with it how long the port of a reverse tunnel stays bound on this machine when the tunnel died without its FIN ever arriving, which is what a broken path or a power loss leaves behind. The configured pair, 60 seconds and 3 probes, ends such a session after about three minutes: a port is reusable soon after a drop, and a link whose answers take tens of seconds still answers a probe within the interval ([Port forwarding](port-forwarding-setup.md)).  
After a change the effective configuration is verified with sshd -T, which prints the result of the whole Include chain. A directive that a later file overrides, or a keyword the daemon does not know, is a warning of a completed task instead of being silently accepted: the verification is independent of the OpenSSH version and of other files in the drop-in directory.

## Listen port and the systemd socket

Ubuntu activates the SSH daemon through the systemd socket unit socket_unit_name, and the socket then owns the listen port: sshd_config Port is ignored while the socket is enabled. The task disables the socket with systemctl disable --now, so the daemon listens on the port from the configuration. After a start or restart the task verifies with ss -tlnp that something listens on the configured Port, so a port that never came up is a warning of a completed task, never a silent success.

## Key deployment

The keys are deployed into the .ssh directory of root (root_ssh_dir) and of every configured user. For every target:

The .ssh directory is created with the configured ssh_dir_mode and owned by the target user.  
The private and public key files are written with their configured modes and owned by the target user.  
The public key line is guaranteed in authorized_keys: the file is appended to, never rewritten, so keys the user added by hand survive; an already present key line is a no-op, so repeated runs do not accumulate duplicates.

The task owns the key files: a file whose content differs from the repository copy is overwritten, so a manual edit cannot wedge the deployed keys. A configured user that does not exist yet is skipped with a log line, so the task stays idempotent.

## Service lifecycle

The service unit comes from the package; the task never renders or writes it. The task enables the unit when it is not enabled and starts it when it is inactive, waiting up to start_check_attempts times with a pause of start_check_retry_delay_seconds between the checks for the unit to report active. On an already active service, a change that affects the port (a Port change or a socket disable) is applied with a restart, because reload does not rebind the listen socket; any other change is applied with a reload, which never drops existing connections. A unit that stays inactive after the readiness loop or a failed reload or restart is a warning of a completed task: the reason is named and every other step of the run keeps its result.

The task follows the recoverable failure policy of the task contract: a step that cannot run is a warning of a completed task and the missing mechanism skips that step alone. A missing key file skips the key deployment, a missing augeas tool skips the drop-in, a failed package install skips both while the keys and the service state are still handled, and a failed socket disable, enable, start, reload, restart or readiness wait leaves every earlier step in place.

## Idempotency

The target state is reached when the package is installed, sshd_config pulls the drop-in directory in, the drop-in matches the configured directives through augeas, the socket is disabled, the keys are in place for root and every existing configured user and the service is enabled and active; the task then returns done with changed=False. Force mode rewrites the drop-in and restarts the active service, but never reinstalls the package and never changes the deployed keys beyond the content comparison.

## Parameters

All parameters live in src/pyntara/values/ssh_daemon_setup.py, and the system config holds no copy of them. The port of the daemon is the Port entry of DIRECTIVES, read through pyntara.ssh by every task and command that forwards to the daemon, so the forward target exists once.

PACKAGE_NAME - the package that provides the SSH server daemon
AUGEAS_TOOLS_PACKAGE_NAME - the package that provides augtool, installed by the task when missing
package_status_timeout_seconds - seconds the dpkg status query may take, the shared value of pyntara.values.common
package_install_retries - retry attempts after a failed package install, the shared value of pyntara.values.common
SERVICE_UNIT_NAME - the systemd service unit of the daemon
SOCKET_UNIT_NAME - the systemd socket unit that owns the listen port and is disabled by the task
START_CHECK_ATTEMPTS - attempts of the readiness loop after a start
START_CHECK_RETRY_DELAY_SECONDS - pause between two readiness checks
SSHD_CONFIG_PATH - the daemon configuration the task only checks for the Include directive
SSHD_CONFIG_DROPIN_PATH - the drop-in the task owns and writes
DROPIN_FILE_MODE - the file mode of the drop-in, as an octal string
DROPIN_HEADER - the ownership comment written at the top of the drop-in, without the leading hash
DROPIN_COMMENT_SIGN - the sign a comment node carries in the augtool listing, by which the task reads the ownership comment of the drop-in and skips a commented line of the main configuration
INCLUDE_DIRECTIVE - the keyword of the main configuration that pulls the drop-in in, matched without case
AUGEAS_LENS - the augeas lens of the sshd_config syntax
PORT_DIRECTIVE - the directive whose change needs a restart instead of a reload
EFFECTIVE_CONFIG_COMMAND - the daemon query that prints the effective configuration (sshd -T)
LISTENING_SOCKETS_COMMAND - the listener query the task reads the port from (ss -tlnp)
SOCKET_DISABLE_COMMAND - the disable and stop of the socket unit, with {socket_unit_name}
SERVICE_ENABLE_COMMAND - the enable of the service unit, with {service_unit_name}
SERVICE_START_COMMAND - the start of the service unit, with {service_unit_name}
SERVICE_RESTART_COMMAND - the restart of the service unit, with {service_unit_name}
SERVICE_RELOAD_COMMAND - the reload of the service unit, with {service_unit_name}
PRIVATE_KEY_FILE_NAME - the repository name of the server private key
PUBLIC_KEY_FILE_NAME - the repository name of the server public key
PRIVATE_KEY_FILE_MODE - the file mode of the deployed private key, as an octal string
PUBLIC_KEY_FILE_MODE - the file mode of the deployed public key, as an octal string
AUTHORIZED_KEYS_FILE_MODE - the file mode of authorized_keys, as an octal string
SSH_DIR_MODE - the mode of the deployed .ssh directories, as an octal string
ROOT_SSH_DIR - the root account .ssh directory
USERS - the accounts that receive the key pair
DIRECTIVES - the sshd_config keywords the task guarantees, each with its value; the keepalive pair among them is the window described under Configuration ownership
PORT_FORWARDING_PRIVATE_KEY_FILE_NAME - the repository name of the port-forwarding private key
PORT_FORWARDING_PUBLIC_KEY_FILE_NAME - the repository name of the port-forwarding public key
PORT_FORWARDING_AUTHORIZED_KEYS_OPTIONS - the restriction prefix of the port-forwarding key line in authorized_keys
