# Google Chrome setup

There is a dedicated Chrome setup task: chrome_setup. The task belongs to the desktop install mode, installs Google Chrome from the official Google apt repository, applies the browser settings of the chromium-default-settings repository to the standard Chrome profile of the desktop user and writes a desktop entry override that starts Chrome with a Chrome DevTools Protocol listener bound to the loopback address, then pins that entry to the Plasma taskbar of the desktop user. The settings repository is the single source of the browser defaults: the machine policy, the external extension files and the profile preferences are applied from it, so adding an extension to the repository is enough to roll it out on the next run.

## Why the official Google apt repository

Google Chrome is not in the Ubuntu archive: Ubuntu ships only Chromium, and as a snap wrapper since 19.10. The only fresh deb source of Chrome is the official Google repository, whose stable channel is updated continuously and installs through apt, so the browser keeps itself current with the ordinary apt upgrade.

## Repository registration

The task registers the source as a deb822 file at the configured apt_source_path with the Signed-By keyring at keyring_path. The keyring is downloaded from Google when missing and dearmored into place, so the download happens once and the files stay root-owned. The source file is written only when its content differs. This mirrors the file Ubuntu itself generates from the Chrome deb, so a later Chrome update that re-registers the source converges to the same content.

## Install

google-chrome-stable is installed with apt-get from the registered repository when the package is missing; an already installed package is left alone and apt keeps it current on later runs. Force mode runs the install again regardless, which brings the package to the newest available version.

## Settings repository

The browser settings live in the git repository named by settings_repo_url on the branch settings_repo_ref. The task clones it into the root cache settings_dir and updates it on every run, so the applied settings follow the repository main without a version chase.

## System tree deployment

The system/ subtree of the settings repository is deployed under system_root, which is "/" in production, with the relative paths preserved. This places the machine policy into /etc/opt/chrome/policies/managed and the external extension files into /opt/google/chrome/extensions, both root-owned mode 0644, copied only when the bytes differ. The policy disables the default browser check and the external extension files make Chrome install the listed extensions into every profile.

## Profile merge

The Default/Preferences file of the settings repository is merged over the live profile preferences of the desktop user at home_dir/.config/google-chrome/Default/Preferences. The merge is a deep dictionary merge in which the repository values win on conflict and current keys that the repository does not carry are kept, so local settings are never deleted and the repository settings land on top. The merge is identical in normal and force mode. A merge that reproduces the current content writes nothing. A running Chrome makes the merge wait with a warning and apply on the next Chrome start, because a live Chrome would rewrite the file from its own memory, and an unreadable profile file is left untouched.

## Desktop entry override

The KDE menu launches Chrome through the packaged desktop entry at desktop_source_path, the visible google-chrome.desktop; the packaged com.google.Chrome.desktop is hidden with NoDisplay. The task writes the override to desktop_override_path, derived from the packaged entry on every run with the CDP flags appended to every Exec line: --remote-debugging-port on the configured cdp_port and --remote-debugging-address on the configured cdp_address, which binds the listener to the loopback only. The override directory precedes the packaged one in the XDG search order, so the flags apply to the menu launch and survive Chrome package updates; when an update replaces the packaged entry, the next run re-derives the override from the new content.

## Menu refresh

After the override changes, the task rebuilds the KDE menu cache for the desktop user with kbuildsycoca6 as a best-effort step. The command carries XDG_MENU_PREFIX=plasma- so the rebuild looks up the plasma-applications.menu of the Plasma session and does not warn about a missing default applications.menu. A failure is a warning: the entry is picked up on the next login or cache rebuild.

## Taskbar pinning

The task pins the CDP desktop entry to the Plasma taskbar of the desktop user, so the button is one click away in the panel. Plasma keeps the pinned launchers in the appletsrc of the user, under the Configuration/General group of every task manager applet: the icons-only task manager (org.kde.plasma.icontasks) and the classic task manager (org.kde.plasma.taskmanager). The task finds every applet that declares one of the two plugins and appends the launcher id applications:google-chrome.desktop to its launchers list when missing, so a desktop with either widget type, or with several panels, pins the button without detecting which variant is present. The launcher id resolves through the XDG applications dirs to the CDP desktop override. A missing appletsrc (the user has not logged into a Plasma session yet) is a note: the button pins on the first login. After a change the task restarts the Plasma panel, so the button appears immediately; when the restart fails the button still appears at the next login.

## Idempotency record

The target state is reached when the apt source and keyring are present, google-chrome-stable is installed, the settings repository is up to date, the system/ tree bytes match, the merged profile equals the current profile, the desktop override matches and the Chrome launcher sits in the taskbar launchers; the task then changes nothing. Force mode reinstalls Chrome and rewrites the deployed files regardless of the current bytes, and the profile merge behaves exactly as in normal mode.

## Parameters

username - the desktop user whose Chrome profile receives the settings  
home_dir - the home directory of that user; the profile preferences path is derived under it  
settings_repo_url - the git repository of browser settings  
settings_repo_ref - the branch of that repository applied on every run  
settings_dir - the root cache that holds the clone of the settings repository  
system_root - the filesystem root that receives the system/ tree, "/" in production  
apt_source_path - the deb822 apt source of the Google Chrome repository  
keyring_path - the Google signing keyring the source signs with  
google_key_url - the url of the armored Google signing key  
desktop_source_path - the packaged Chrome desktop entry  
desktop_override_path - the override entry with the CDP flags  
cdp_port - the Chrome DevTools Protocol port  
cdp_address - the address the CDP listener binds to, the loopback  
file_mode - the mode of every deployed configuration and desktop file  
