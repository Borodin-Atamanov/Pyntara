# TODO

Planned future work, one idea per item. Items are not commitments: an item moves to a spec (docs/spec/) when implementation starts.

New task that configures fstab to reduce SSD writes. Mount options are not chosen yet and must be researched on the internet first.  
Move error_priority and command_timeout_seconds out of the per-task config tables (nextdns_setup_system_wide, system_metrics_setup, local_vault_setup) into the [engine] table. i2pd_service_setup, tor_setup and yggdrasil_service_setup no longer have their own copies.  
Set an i2pd traffic limit in i2pd_service_setup: total bandwidth 100 Mbit/s with a 1 percent transit share, which maps to bandwidth = 12500 and share = 1 in i2pd.conf.  
3x_ui_xray_setup stage 1: install the 3x-ui panel from GitHub releases as a system service. Resolve the newest release tag via the GitHub API, pick the archive for the architecture, download and unpack it into /usr/local/x-ui, install the systemd unit, enable and start the service, wait for it to report active and report the version; return done when the same version is installed.  
3x_ui_xray_setup stage 2: gain programmatic control of the panel through its REST API. Take the credentials the panel generated on first start, log in, verify the session, and store the credentials in the project vault instead of plain config; the login helper is a shared reusable module with tests against mocked responses.  
3x_ui_xray_setup stage 3: create the universal server inbound through the panel API. Create a VLESS inbound with REALITY on the configured port from the task config table; on a rerun find the existing inbound and return done. Tests cover the request payloads and the idempotent re-run.
