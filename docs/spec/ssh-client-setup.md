# SSH client setup

There is a dedicated SSH client task: ssh_client_setup.

The task patches the system-wide SSH client configuration through a drop-in file, so the client defaults apply to every user and every connection. It belongs to all install modes.

## Configuration ownership

The task never rewrites ssh_config itself. The client configuration is patched through the drop-in at the configured ssh_config_dropin_path:

The task checks that ssh_config has an Include directive that pulls the drop-in directory in. The check matches every Include pattern against the drop-in path with glob semantics, resolving relative patterns against the directory of ssh_config. A missing Include means the drop-in would be silently ignored, so the task fails with an explicit error instead of pretending the configuration is in place.  
The task installs the augeas-tools package itself when augtool is missing, so it never waits for another task to provide the tool. The directives are written through augeas under the Host * block, so they apply to every connection. augeas parses the real syntax and updates only what differs: a directive that is already present with the same value is left untouched, a directive with a different value is updated, a directive that is no longer configured is removed, and the ownership comment is guaranteed. The drop-in is owned by the task: a manual edit is reverted on the next run.  
An empty directives list removes the drop-in, so the task can revoke its own settings.  
After a change the effective configuration is verified with ssh -G, which prints the result of the whole Include chain, so a directive that a later file overrides, or a keyword the client does not know, is reported as an error instead of being silently accepted.

## Effective order

ssh_config parses command-line options first, then the user-specific file, then the system-wide file, and the first value set wins. The drop-in extends ssh_config, so the system-wide defaults win over the values in /etc/ssh/ssh_config; a per-user ~/.ssh/config still overrides them for that user. Durations are configured in whole seconds, so the ssh -G comparison stays exact.

## Idempotency

The target state is reached when ssh_config pulls the drop-in directory in and the drop-in matches the configured directives through augeas; the task then returns done with changed=False. Force mode rewrites the drop-in and verifies it again. There is no daemon to restart: the client reads its configuration on every invocation.

## Parameters

All parameters live in the [ssh_client_setup] table of the config/ directory.

ssh_config_path - the client configuration the task only checks for the Include directive
ssh_config_dropin_path - the drop-in the task owns and writes
dropin_file_mode - the file mode of the drop-in, as an octal string
dropin_header - the ownership comment written at the top of the drop-in, without the leading hash
augeas_lens - the augeas lens of the ssh_config syntax
augeas_container - the container node the directives are placed under
augeas_container_value - the pattern that container node carries, so the directives apply to every connection
augeas_tools_package_name - the package that provides augtool, installed by the task when missing
package_status_timeout_seconds - seconds the dpkg status query may take
install_retries - retry attempts after a failed package install
directives - the ssh_config keywords the task guarantees, each with its value
