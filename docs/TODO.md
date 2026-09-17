# TODO

Planned future work. После реализации - удаляем из этого файла.

## Values migration: the TOML config becomes Python values

Goal: every value of the machine lives in one Python module per task under
src/pyntara/values/, so adding or changing a value is one edit in one file and
no value is written in two places. The TOML config/ directory, the loader, the
shared test document, the parity guards and the deployed copy of the config go
away. Development stays strict: mypy on the annotations, the rules of the
values, the full gate. The run stays soft: a value that is not declared is
reported in plain words by the task that needed it, the remaining steps and the
remaining tasks still run, and only the values of the engine itself, the task
catalog and the install modes, are fatal.

The pattern, set by the hostname pilot and now the only one in the tree:

1. src/pyntara/values/<task>.py holds the typed constants of that task with the
   comments carried over from its TOML section, plus READ_VALUE_NAMES, the
   tuple of names the task reads.
2. src/pyntara/values/__init__.py holds missing_value_names(module, names),
   which answers which names a module does not declare and never raises.
3. The task reads its values at the point of use and, before that, reports the
   absent names once as TaskResult(success=True, message=..., warnings=(...))
   and changes nothing. No per-value branch, no substituted default.
4. tests/value_checks.py holds the shared rules a type cannot express:
   check_nonempty_text, check_absolute_path, check_positive_int,
   check_nonnegative_int, check_file_mode and check_text_tuple, each taking the
   value and the dotted name it reports, so a section adds no copy of a rule.
5. tests/test_values.py applies those rules to the shipped values, proves
   READ_VALUE_NAMES names exactly the declared values and proves by an AST scan
   that every declared value is read somewhere. A new values module is added to
   VALUES_MODULE_NAMES and gets its shipped-values test there.
6. A task test points its values at the fixture tree with
   monkeypatch.setattr(module, "NAME", value) inside a per-section helper that
   takes monkeypatch and the fixture paths; monkeypatch restores the shipped
   values whether the test passed or failed.
7. While a section is being migrated its TOML section and its config tests stay
   in place; only the task, its tests and the new rules move. Stage C removes
   the old sources in one go.
8. A section is one commit: the gate of scripts/check_gates.sh green, then a
   fast-forward merge into main and a push.

Sections, one commit each. Renumber this list while it shrinks:

1. hostname, done, commit 4b9076b
2. imagemagick_setup, done, commit 6cbeacc
3. ffmpeg_setup, done, commit 736bcab
4. playwright_setup
5. rustdesk_setup
6. vocalinux_setup
7. cli_tools
8. add_extra_repos
9. telegram_setup
10. chrome_setup
11. kde_keyboard_setup
12. zram_service
13. zswap_service
14. swapfile_service_install
15. nextdns_setup_system_wide
16. tor_setup
17. i2pd_service_setup
18. yggdrasil_service_setup
19. ssh_client_setup
20. ssh_daemon_setup
21. port_forwarding_setup
22. upnp_forwarding_setup
23. vault_structure, local_vault_setup and the vault entry titles the sections cross-check
24. system_metrics_setup, the largest section: the collector, the ingest, the sender, the deployment and the two telemetry pdf values
25. three_x_ui_xray_setup
26. sotavpn_setup
27. kde_settings
28. dnsproxy_setup
29. engine, the most connected section, and the task catalog of tasks

Stages after the sections:

30. Stage C, removal: delete config/, src/pyntara/config/loader.py,
    tests/config_helpers.py, tests/test_config_reader.py, the make_config
    factory, the type checks of tests/config_checks.py, the config copy in
    system_metrics_setup (system_config_path and the {config_path} placeholders
    of the unit commands), and drop the config layer from VALUE_DIRECTORIES in
    tests/test_config_value_guard.py so the values package is the single place a
    value may be declared.
31. Stage D, documents: docs/contracts/architecture.md (Configuration),
    docs/spec/config-content.md (where a value is declared and checked),
    docs/guides/project-structure.md (adding a value, config section map),
    docs/guides/developer-guide.md and docs/spec/system-metrics.md (the config
    path of the deployed services).
32. Stage E, live proof on a target machine: a full provisioning run, then the
    deployed metrics service reading its values without
    /etc/pyntara/config.toml, and the installer path from inst.sh.

Decisions already taken, not to be reopened silently:

33. The values package lives inside the package, not at the repository root, so
    the installed wheel carries the values to the deployed services.
34. The aggregate Config and Context.config go away; a task reads the values of
    its own module at the point of use.
35. A type is declared once, by the annotation of the constant; mypy replaces
    the shape checks of the old suite, and only the rules a type cannot express
    stay in tests/value_checks.py.
36. A mode of a file is a value and its literal belongs in the values package;
    tests/test_config_coverage.py leaves that package out of its scan and keeps
    refusing a mode literal everywhere else.



