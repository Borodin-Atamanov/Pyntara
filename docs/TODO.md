# TODO

Planned future work, one idea per item. Items are not commitments: an item moves to a spec (docs/spec/) when implementation starts.

1. New task that configures fstab to reduce SSD writes. Mount options are not chosen yet and must be researched on the internet first.
2. Move error_priority and command_timeout_seconds out of the per-task config tables (nextdns_setup_system_wide, system_metrics_setup, local_vault_setup) into the [engine] table, the way dnscrypt_setup already reads them from engine.error_priority and engine.command_timeout_seconds. i2pd_service_setup, tor_setup and yggdrasil_service_setup no longer have their own copies.
