# Google Chrome setup

There is a dedicated Chrome setup task: chrome_setup. The task belongs to the desktop install mode, installs Google Chrome from the official Google apt repository, applies the browser settings of the chromium-default-settings repository to the standard Chrome profile of the desktop user and writes a desktop entry override that starts Chrome through the local proxy of the three_x_ui_xray_setup section, on the mirror of the live profile, with a Chrome DevTools Protocol listener bound to the loopback address, then pins that entry to the Plasma taskbar of the desktop user. The task depends on three_x_ui_xray_setup, because the browser is started through the local proxy that task creates. The settings repository is the single source of the browser defaults: the machine policy, the external extension files and the profile preferences are applied from it, so adding an extension to the repository is enough to roll it out on the next run.

## Why the official Google apt repository

Google Chrome is not in the Ubuntu archive: Ubuntu ships only Chromium, and as a snap wrapper since 19.10. The only fresh deb source of Chrome is the official Google repository, whose stable channel is updated continuously and installs through apt, so the browser keeps itself current with the ordinary apt upgrade.

## Repository registration

The task registers the source as a deb822 file at the configured apt_source_path with the Signed-By keyring at keyring_path. The keyring is downloaded from Google when missing and dearmored into place, so the download happens once and the files stay root-owned. The source file is written only when its content differs. This mirrors the file Ubuntu itself generates from the Chrome deb, so a later Chrome update that re-registers the source converges to the same content.

## Install

google-chrome-stable is installed with apt-get from the registered repository
when the package is missing; an already installed package is left alone and apt keeps it current on later runs. Force mode runs the install again regardless, which brings the package to the newest available version.

## Settings repository

The browser settings live in the git repository named by settings_repo_url on the branch settings_repo_ref. The task clones it into the root cache settings_dir and updates it on every run, so the applied settings follow the repository main without a version chase.

## System tree deployment

The system/ subtree of the settings repository is deployed under system_root, which is "/" in production, with the relative paths preserved. This places the machine policy into /etc/opt/chrome/policies/managed and the external extension files into /opt/google/chrome/extensions, both root-owned mode 0644, copied only when the bytes differ. The policy disables the default browser check and the external extension files make Chrome install the listed extensions into every profile.

## Profile merge

The Default/Preferences file of the settings repository is merged over the live profile preferences of the desktop user at home_dir/.config/google-chrome/Default/Preferences. The merge is a deep dictionary merge in which the repository values win on conflict and current keys that the repository does not carry are kept, so local settings are never deleted and the repository settings land on top. The merge is identical in normal and force mode. A merge that reproduces the current content writes nothing. A running Chrome makes the merge wait with a warning and apply on the next Chrome start, because a live Chrome would rewrite the file from its own memory, and an unreadable profile file is left untouched.

## Local proxy

The browser is started through the local proxy that three_x_ui_xray_setup creates: a mixed inbound of the panel core that serves SOCKS5 and HTTP on the loopback address, listed in that section as local_proxy_listen_address and local_proxy_port. Those two values are the single description of the local proxy of the machine, so chrome_setup reads them instead of carrying a second copy: a port changed in one place cannot leave the browser pointing at a proxy that no longer exists. Chrome resolves names on the proxy side with SOCKSv5, so the browser never asks the local resolver for a proxied name, and its implicit bypass rules keep loopback addresses, the panel among them, out of the proxy. The proxy server carries no direct fallback, because a silent direct exit would defeat the routing policy the local proxy applies.

The flag enters the desktop entry only when a listener answers on the configured port: a Chrome started with a proxy flag and no proxy behind it opens every request with a connection error, while the browser without the flag keeps working. A machine that is the remote server itself never raises that inbound, and a panel that is down has no listener either; both are reported as a warning naming the address and the port, and the browser starts without the proxy until the listener is there. The UDP traffic of the browser is not touched by any of this: Chrome relays only TCP through a SOCKSv5 proxy.

## Profile mirror

Branded Google Chrome refuses to open the DevTools listener on the default data directory and asks for a non-default one, so the task bind mounts the live profile to a second path and passes that path to Chrome as --user-data-dir: the same files, cookies and logins included, under another directory name. The mirror lives at profile_mirror_path and is mounted with mount --bind from home_dir/.config/google-chrome; the task creates the profile directory when the machine has never started Chrome, before mounting.

The mount does not survive a reboot on its own, so the task writes the oneshot unit named by mount_service_unit_name from the template at task_data/chrome_setup/mount_chrome_user_dir.service, reloads systemd and enables the unit: the unit creates the mirror directory for the desktop user and mounts the profile on it at every boot, before the graphical session opens. The unit is written and enabled on every run, so a mirror mounted by hand is restored after a reboot as well.

The task confirms the mount with findmnt and asks for the mount point and the filesystem root of the mirror path: the mirror is in place when the mount point is the mirror path itself and its filesystem root is the profile directory. The --user-data-dir flag enters the desktop entry only when the mount is confirmed: a Chrome started on an empty mirror directory would hide the live profile, so a mirror that could not be mounted leaves the flag out and is reported as a warning.

## Desktop entry override

The KDE menu launches Chrome through the packaged desktop entry at desktop_source_path, the visible google-chrome.desktop; the packaged com.google.Chrome.desktop is hidden with NoDisplay. The task writes the override to desktop_override_path, derived from the packaged entry on every run with the launch flags appended to every Exec line, in this order:  
--proxy-server on the local proxy of the three_x_ui_xray_setup section, left out while no listener answers on its port  
--user-data-dir on profile_mirror_path, left out while the mirror mount is not confirmed  
--remote-debugging-port on the configured cdp_port and --remote-debugging-address on the configured cdp_address, which binds the listener to the loopback only  

The flags are appended in that order to every Exec line, so the main entry, the new window action and the incognito action all start the same browser. The override directory precedes the packaged one in the XDG search order, so the flags apply to the menu launch and survive Chrome package updates; when an update replaces the packaged entry, the next run re-derives the override from the new content. A missing packaged entry leaves the override unwritten and is reported as a warning.

## Menu refresh

After the override changes, the task rebuilds the KDE menu cache for the desktop user with kbuildsycoca6 as a best-effort step. The command carries XDG_MENU_PREFIX=plasma- so the rebuild looks up the plasma-applications.menu of the Plasma session and does not warn about a missing default applications.menu. A failure is a warning: the entry is picked up on the next login or cache rebuild.

## Taskbar pinning

The task pins the CDP desktop entry to the Plasma taskbar of the desktop user, so the button is one click away in the panel. Plasma keeps the pinned launchers in the appletsrc of the user, under the Configuration/General group of every task manager applet: the plugins named by taskbar_plugin_names (the icons-only task manager and the classic task manager in the shipped config). The task finds every applet that declares one of those plugins and appends the configured panel_launcher_id to the launchers list named by appletsrc_launchers_key when missing, so a desktop with either widget type, or with several panels, pins the button without detecting which variant is present. The launcher id resolves through the XDG applications dirs to the CDP desktop override. A missing appletsrc (the user has not logged into a Plasma session yet) is a note: the button pins on the first login. After a change the task restarts the Plasma panel through panel_restart_command, so the button appears immediately; when the restart fails the button still appears at the next login.

## Idempotency record

The target state is reached when the apt source and keyring are present, google-chrome-stable is installed, the settings repository is up to date, the system/ tree bytes match, the merged profile equals the current profile, the mirror unit file matches, the mirror mount is in place, the desktop override matches and the Chrome launcher sits in the taskbar launchers; the task then changes nothing. Force mode reinstalls Chrome and rewrites the deployed files regardless of the current bytes, and the profile merge behaves exactly as in normal mode.

When the task runs while Chrome is running, the profile merge waits with a warning, and a DevTools listener that does not answer on the configured port is reported as a warning asking for a Chrome restart from the menu, because a running Chrome keeps the flags it was started with. A machine that has never started Chrome gets a progress line instead: the listener opens with the first launch from the menu.

## Parameters

username - the desktop user whose Chrome profile receives the settings
home_dir - the home directory of that user; the profile and the deployed files are derived under it
package_name - the apt package of the browser
process_name - the process name pgrep sees for a running browser
appletsrc_file_name - the Plasma appletsrc name as the KConfig tools take it
appletsrc_relative_path - the same file under home_dir
appletsrc_launchers_key - the appletsrc key that carries the pinned launchers
taskbar_plugin_names - the task manager plugin names whose launcher list receives the button
panel_launcher_id - the launcher id pinned to the panel
panel_restart_command - the command that restarts the Plasma panel, with {username}
settings_repo_url - the git repository of browser settings
settings_repo_ref - the branch of that repository applied on every run
settings_dir - the root cache that holds the clone of the settings repository
settings_system_tree_relative_path - the tree inside the repository deployed under system_root
preferences_relative_path - the settings file inside the repository and inside the profile
profile_dir_relative_path - the live Chrome profile directory under home_dir
keyring_temp_dir_prefix - the prefix of the temporary directory the key is downloaded into
apt_source_template_file_name - the apt source template under task_data/chrome_setup/, rendered with $keyring_path
launch_flags - the flags appended to every Exec line, in order, each with its placeholders; a flag whose value is empty is left out
keyring_dearmor_command - the command that dearmors the key, with {armored} and {output}
settings_clone_command - the clone command, with {url}, {ref} and {dir}
settings_fetch_command - the fetch command, with {dir} and {ref}
settings_revision_command - the revision query, with {dir} and {revision}
settings_reset_command - the reset to a revision, with {dir} and {revision}
process_check_command - the command that asks whether the browser runs, with {process_name}
mount_check_command - the findmnt query that confirms the mirror, with {path}
mount_reload_command - the unit file reload of the mirror unit
mount_enable_command - the enable and start of the mirror unit, with {unit_name}
menu_refresh_command - the menu cache rebuild, with {username} and {home_dir}
mount_unit_template_file_name - the mirror unit template under task_data/chrome_setup/
system_root - the filesystem root that receives the settings tree, "/" in production
apt_source_path - the deb822 apt source of the Google Chrome repository
keyring_path - the Google signing keyring the source signs with
google_key_url - the url of the armored Google signing key
desktop_source_path - the packaged Chrome desktop entry
desktop_override_path - the override entry with the launch flags
profile_mirror_path - the bind mounted mirror of the live profile, passed to Chrome as --user-data-dir
mount_service_unit_name - the oneshot unit that restores the mirror mount at every boot
cdp_port - the Chrome DevTools Protocol port  
cdp_address - the address the CDP listener binds to, the loopback  
file_mode - the mode of every deployed configuration and desktop file  

The local proxy address and port are not chrome_setup values: the task reads local_proxy_listen_address and local_proxy_port of the three_x_ui_xray_setup section, the single place where the local proxy of this machine is described.
