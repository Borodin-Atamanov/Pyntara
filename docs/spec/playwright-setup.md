# playwright-cli setup

There is a dedicated task: playwright_setup. The task belongs to the desktop install mode and installs the playwright-cli browser control tool for the desktop user. The tool controls the Google Chrome that chrome_setup installs and starts through its CDP desktop entry, so the task depends on chrome_setup and on add_extra_repos: the CDP browser entry comes from chrome_setup and nodejs lives in the universe component that add_extra_repos enables.

## Why npm in the user prefix

playwright-cli is distributed as the npm package @playwright/cli. Chrome itself is already installed by chrome_setup with a Chrome DevTools Protocol listener on the loopback address (docs/spec/chrome-setup.md), and playwright-cli is the token-efficient way for an agent to drive that listener over CDP. The npm default prefix /usr/local is root-owned, and a root-owned npm prefix fails later when the plain user updates it, so the task installs into the user prefix home_dir/.local of the desktop user. nodejs and npm come from the Ubuntu archive: nodejs is in universe and the archive version runs playwright-cli, so no third-party runtime is introduced.

## Install

The task installs the configured apt packages (nodejs and npm) through the shared install helper, then runs npm install -g with the configured cli_package and the prefix home_dir/.local as the desktop user through runuser with HOME set, so the binary lands at home_dir/.local/bin/playwright-cli and the npm cache stays in the user home. The version is not chased: npm installs the latest release and no version list is tracked anywhere.

## Idempotency record

The target state is reached when every configured apt package is installed and the playwright-cli binary exists, is executable and answers --version as the desktop user; the task then changes nothing. Force mode re-runs the npm install regardless of the current binary. The version read-back runs through runuser, because the binary and the npm cache live in the user home.

## Parameters

username - the desktop user whose prefix receives playwright-cli
home_dir - the home of that user; the prefix and the binary path are derived under it
packages - the apt packages that provide the npm runtime, nodejs and npm
package_status_timeout_seconds - seconds the dpkg status query may take
package_install_retries - retry attempts after a failed package install
cli_package - the npm package that provides the playwright-cli binary
user_prefix_relative_path - the install prefix under home_dir, .local in the shipped config
cli_bin_relative_path - the binary path inside that prefix, bin/playwright-cli in the shipped config
runuser_command - the command that runs another command as the desktop user, with {username} and {home_dir} as its placeholders
cli_version_command - the command that prints the version of the installed binary, with {cli_bin} as its placeholder
npm_install_command - the install command, with {cli_package} and {prefix} as its placeholders
npm_install_timeout_seconds - seconds a single npm install command may run
