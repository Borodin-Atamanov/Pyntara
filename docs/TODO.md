# TODO

Planned future work, one idea per item. Items are not commitments: an item moves to a spec (docs/spec/) when implementation starts.

New task that configures fstab to reduce SSD writes. Mount options are not chosen yet and must be researched on the internet first.  
Move error_priority and command_timeout_seconds out of the per-task config tables (nextdns_setup_system_wide, system_metrics_setup, local_vault_setup) into the [engine] table. i2pd_service_setup, tor_setup and yggdrasil_service_setup no longer have their own copies.  
Set an i2pd traffic limit in i2pd_service_setup: total bandwidth 100 Mbit/s with a 1 percent transit share, which maps to bandwidth = 12500 and share = 1 in i2pd.conf.
