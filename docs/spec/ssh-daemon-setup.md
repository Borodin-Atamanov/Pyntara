# SSH daemon setup

There is a dedicated SSH server task: ssh_daemon_setup.

The task installs the SSH server package, runs its systemd service and patches the daemon configuration through a drop-in file, so passwordless login with the pre-generated key pair works out of the box. The task belongs to all install modes and depends on users_setup, because the keys are deployed into the home directories of the configured users.

## Key pair

The key pair lives in the repository under task_data/ssh_daemon_setup/: the private key pyntara_mesh and the public key pyntara_mesh.pub. Both files are committed to the repository. The private key is an OpenSSH private key encrypted with a strong pass phrase, so committing it is safe: the pass phrase is never stored in the repository, in the config or on the target machine. The task copies the private key as is, still encrypted, and never needs the pass phrase.

## Configuration ownership

The task never rewrites sshd_config itself. The main configuration is patched through the drop-in at the configured sshd_config_dropin_path, rendered from the configured directives in order:

1. The task checks that sshd_config has an Include directive that pulls the drop-in directory in. The check matches every Include pattern against the drop-in path with glob semantics, resolving relative patterns against the directory of sshd_config. A missing Include means the rendered drop-in would be silently ignored, so the task fails with an explicit error instead of pretending the configuration is in place.
2. The drop-in is written whenever the rendered content differs, so manual edits are reverted on the next run: the task owns the file.
3. An empty directives list renders no content, and the task removes the drop-in, so the task can revoke its own settings.

## Key deployment

The keys are deployed into the .ssh directory of root (root_ssh_dir) and of every configured user. For every target:

1. The .ssh directory is created with the configured ssh_dir_mode and owned by the target user.
2. The private and public key files are written with their configured modes and owned by the target user.
3. The public key line is guaranteed in authorized_keys: the file is appended to, never rewritten, so keys the user added by hand survive; an already present key line is a no-op, so repeated runs do not accumulate duplicates.

The task owns the key files: a file whose content differs from the repository copy is overwritten, so a manual edit cannot wedge the deployed keys. A configured user that does not exist yet is skipped with a log line, so the task stays idempotent while users_setup runs later.

## Service lifecycle

The service unit comes from the package; the task never renders or writes it. The task enables the unit when it is not enabled and starts it when it is inactive, waiting up to start_check_attempts times with a pause of start_check_retry_delay_seconds between the checks for the unit to report active. When the configuration changed while the service was already active, the task reloads the unit instead of restarting it: a reload never drops existing connections. A unit that stays inactive after the readiness loop or a failed reload is a task error.

## Idempotency

The target state is reached when the package is installed, sshd_config pulls the drop-in directory in, the drop-in matches the render, the keys are in place for root and every existing configured user and the service is enabled and active; the task then skips with changed=False. Force mode rewrites the drop-in and reloads the active service, but never reinstalls the package and never changes the deployed keys beyond the content comparison.

## Parameters

All parameters live in config.toml under [ssh_daemon_setup]:

package_name is the package that provides the SSH server daemon
package_status_timeout_seconds bounds the dpkg status query
install_retries is the retry count of the package install; total attempts are retries plus one
service_unit_name is the systemd unit of the SSH daemon
start_check_attempts and start_check_retry_delay_seconds bound the readiness loop after a start
sshd_config_path is the main daemon configuration, checked for the Include directive but never rewritten
sshd_config_dropin_path is the drop-in file owned by the task
dropin_file_mode is the file mode of the rendered drop-in
private_key_file_name and public_key_file_name are the repository key file names under task_data/ssh_daemon_setup/
private_key_file_mode and public_key_file_mode are the file modes of the deployed keys
authorized_keys_file_mode is the file mode of the authorized_keys file
ssh_dir_mode is the file mode of the created .ssh directories
root_ssh_dir is the .ssh directory of the root user
users is the list of additional users whose .ssh directories receive the keys
directives is the list of sshd_config keywords guaranteed by the task, each a name and a value
