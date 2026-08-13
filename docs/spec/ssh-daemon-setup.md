# SSH daemon setup

There is a dedicated SSH server task: ssh_daemon_setup.

The task installs the SSH server package, runs its systemd service and patches the daemon configuration through a drop-in file, so passwordless login with the pre-generated key pair works out of the box. The task belongs to all install modes and depends on users_setup, because the keys are deployed into the home directories of the configured users.

## Key pair

The key pair lives in the repository under task_data/ssh_daemon_setup/: the private key id_ed25519 and the public key id_ed25519.pub. Both files are committed to the repository. The deployed file names equal the repository names and match the OpenSSH default identity name id_ed25519, so the client offers the key automatically on every connection, without -i or ssh-add. The private key is an OpenSSH private key encrypted with a strong pass phrase, so committing it is safe: the pass phrase is never stored in the repository, in the config or on the target machine. The task copies the private key as is, still encrypted, and never needs the pass phrase. The pass phrase stays on the target: the first connection prompts for it, or the user runs ssh-add once to load the key into the agent. A key deployed under another name by an earlier task version is a harmless leftover and is removed by hand; the task does not clean it up.

## Configuration ownership

The task never rewrites sshd_config itself. The main configuration is patched through the drop-in at the configured sshd_config_dropin_path:

1. The task checks that sshd_config has an Include directive that pulls the drop-in directory in. The check matches every Include pattern against the drop-in path with glob semantics, resolving relative patterns against the directory of sshd_config. A missing Include means the drop-in would be silently ignored, so the task fails with an explicit error instead of pretending the configuration is in place.
2. The directives are written through augeas (augtool, installed by the augeas-tools package of the cli_tools task). augeas parses the real syntax and updates only what differs: a directive that is already present with the same value is left untouched, a directive with a different value is updated, a directive that is no longer configured is removed, and the ownership comment is guaranteed. The drop-in is owned by the task: a manual edit is reverted on the next run.
3. An empty directives list removes the drop-in, so the task can revoke its own settings.
4. After a change the effective configuration is verified with sshd -T, which prints the result of the whole Include chain. A directive that a later file overrides, or a keyword the daemon does not know, is reported as an error instead of being silently accepted: the verification is independent of the OpenSSH version and of other files in the drop-in directory.

## Listen port and the systemd socket

Ubuntu activates the SSH daemon through the systemd socket unit socket_unit_name, and the socket then owns the listen port: sshd_config Port is ignored while the socket is enabled. The task disables the socket with systemctl disable --now, so the daemon listens on the port from the configuration. After a start or restart the task verifies with ss -tlnp that something listens on the configured Port, so a port that never came up is a task error, not a silent success.

## Key deployment

The keys are deployed into the .ssh directory of root (root_ssh_dir) and of every configured user. For every target:

1. The .ssh directory is created with the configured ssh_dir_mode and owned by the target user.
2. The private and public key files are written with their configured modes and owned by the target user.
3. The public key line is guaranteed in authorized_keys: the file is appended to, never rewritten, so keys the user added by hand survive; an already present key line is a no-op, so repeated runs do not accumulate duplicates.

The task owns the key files: a file whose content differs from the repository copy is overwritten, so a manual edit cannot wedge the deployed keys. A configured user that does not exist yet is skipped with a log line, so the task stays idempotent while users_setup runs later.

## Service lifecycle

The service unit comes from the package; the task never renders or writes it. The task enables the unit when it is not enabled and starts it when it is inactive, waiting up to start_check_attempts times with a pause of start_check_retry_delay_seconds between the checks for the unit to report active. On an already active service, a change that affects the port (a Port change or a socket disable) is applied with a restart, because reload does not rebind the listen socket; any other change is applied with a reload, which never drops existing connections. A unit that stays inactive after the readiness loop or a failed reload or restart is a task error.

## Idempotency

The target state is reached when the package is installed, sshd_config pulls the drop-in directory in, the drop-in matches the configured directives through augeas, the socket is disabled, the keys are in place for root and every existing configured user and the service is enabled and active; the task then skips with changed=False. Force mode rewrites the drop-in and restarts the active service, but never reinstalls the package and never changes the deployed keys beyond the content comparison.

## Parameters

All parameters live in config.toml under [ssh_daemon_setup]:

package_name is the package that provides the SSH server daemon
package_status_timeout_seconds bounds the dpkg status query
install_retries is the retry count of the package install; total attempts are retries plus one
service_unit_name is the systemd unit of the SSH daemon
socket_unit_name is the systemd socket unit of the SSH daemon, disabled so the configured port takes effect
start_check_attempts and start_check_retry_delay_seconds bound the readiness loop after a start
sshd_config_path is the main daemon configuration, checked for the Include directive but never rewritten
sshd_config_dropin_path is the drop-in file owned by the task, written through augeas
dropin_file_mode is the file mode of the drop-in
private_key_file_name and public_key_file_name are the repository key file names under task_data/ssh_daemon_setup/
private_key_file_mode and public_key_file_mode are the file modes of the deployed keys
authorized_keys_file_mode is the file mode of the authorized_keys file
ssh_dir_mode is the file mode of the created .ssh directories
root_ssh_dir is the .ssh directory of the root user
users is the list of additional users whose .ssh directories receive the keys
directives is the list of sshd_config keywords guaranteed by the task, each a name and a value
