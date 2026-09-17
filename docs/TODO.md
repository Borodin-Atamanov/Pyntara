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
4. playwright_setup, done
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

What every section must keep true: the criteria and the nuances.

37. Softness at run time is the mission and mypy does not replace it. A value
    that is not declared must cost a step or a task and never the run: the task
    names the absent value in plain words, changes nothing, and the runner
    carries on with the remaining tasks. The values of the engine itself (its
    module, the task catalog and the install modes) stay the only fatal read,
    as an empty task catalog is fatal today.
38. A values module is all or nothing: Python imports it whole or not at all, so
    a module that lost one value cannot exist; it is the module that fails to
    import. A task module imports the values module of its own task and never a
    shared one, so an unimportable module costs the tasks that import it and
    never the run.
39. No defensive code in a task body: no test for None at a read, no default at
    a read, no try around a read. The absence is caught once, by the guard at
    the top of the task, and a step that cannot run without a value adds its
    name to the warnings the task already collects. One condition per step,
    never one per value.
40. The deployed long-running services (the metrics collector, the ingest and
    the sender) have no task runner, so each keeps one protected read at the top
    of its entry function, one line in plain words naming what it could not
    find, and a clean exit instead of a crash loop under systemd. The places
    that name absent config keys today (COLLECTOR_SECTION_KEYS,
    COLLECTOR_TABLE_KEYS, INGEST_CONFIG_KEYS, SERVICE_CONFIG_KEYS,
    COUNTRY_REPORT_CONFIG_KEYS and the address ones) lose their purpose and go
    away with them.
41. The tests that prove the softness belong to a migrated section and are not a
    later nice to have: one that removes a name from the values module and
    requires a warning naming it, no change and a successful task; one for a
    values module that does not import, requiring the remaining tasks to run;
    the entry point test of a deployed service; and the existing proof that an
    empty task catalog stays fatal.
42. Four guards hold the values package: the shipped values pass every rule of
    tests/value_checks.py; READ_VALUE_NAMES names exactly the declared values;
    every declared value is read somewhere; and a value literal outside the
    values package fails the suite. A fifth is worth having: a task module
    imports only the values module of its own task.
43. Every rule of the old suite is either replaced or deleted with its reason: a
    shape rule dies with mypy on the annotation, and a rule a type cannot
    express moves to tests/value_checks.py together with its negative test. A
    rule that disappears without one of those two is a defect, which is why a
    section is migrated with the diff read, not by a script.
44. Values of special kinds. MODES and SEND_ORDERS are read by production and
    become values. The vocabularies only the checks read (I2PD_LOG_LEVELS,
    TOR_LOG_LEVELS, the listen and peer schemes, the numlock states, the click
    methods, the share address strategies, the domain strategies) stay in the
    tests. A record type (TaskConfig, VaultEntry, VaultGroupSeed, SshDirective,
    CollectorModuleConfig, TelemetryPdfConfig, KConfigRecord,
    RustdeskOptionConfig) stays in code as a type, and the value is the tuple of
    its records.
45. Context keeps the clone root, the install mode, the forced task names and
    the task name; only its config goes away. A helper shared by two tasks takes
    the value as a parameter; anything else reads the module of its own task.
46. The deployment stops carrying a config. The deployed services import the
    values of the installed wheel, which is built with uv sync --no-editable, so
    the values package must be inside the wheel. The whole config path mechanism
    goes with it: system_config_path, the write and the compare of
    /etc/pyntara/config.toml, the three service commands and the two
    module_run_command values that pass {config_path}.
47. Consequence to state plainly about a machine: a changed value reaches a
    deployed service only through a provisioning run. That is what happens today
    as well, because the task writes that file from the clone.
48. Out of scope, not to be touched by this migration: inst.sh, the templates of
    task_data/, secrets/, hooks/, and the logic of a task other than the place
    where it reads a value.
49. Two removals in stage C that are easy to forget: absent_config_keys and
    describe_absent_config_keys go away with their call sites, and
    OPTIONAL_SECTION_KEYS goes away because the four keys it names are real
    values now. DERIVED_SECTION_FIELDS stays, because the four certificate path
    fields of three_x_ui_xray_setup are still derived from other values.
50. Stage E proves more than one task run: that the built wheel carries the
    values package, that a deployed service completes a cycle reading its
    values, and that the installer path of inst.sh produces the same machine.
51. The engine is migrated last on purpose: every module reads it, so it is the
    one section whose migration touches the whole package at once.
52. Where a value goes after the migration, which is the whole point: one line in
    src/pyntara/values/<task>.py with its annotation and its comment, and the
    read at the place in the task that needs it. Nothing else. A rule in
    tests/value_checks.py only for what the annotation cannot say, its entry in
    the shipped-values test of tests/test_values.py, and the module added to
    VALUES_MODULE_NAMES. No second copy exists anywhere.
53. Scale, measured on 2026-09-17 before the migration started, for judging the
    remaining work: 31 sections and about 1313 values in 6377 lines of TOML; 66
    modules of the package use pyntara.config; 238 places in 19 task modules read
    a value through the aggregate; tests/config_checks.py is 7262 lines with 33
    section functions and 342 refusals; the tests hold 610 replace sites in 56
    files, and 62 files use the make_config factory. One migrated section costs a
    values module of 45 to 90 lines, 43 to 65 lines of task edits and 76 to 105
    lines of test edits, plus about 90 lines in the shared guards.
54. The one fallback worth keeping in view: if removing the aggregate Config
    proves too expensive section by section, the alternative is a settings object
    per task, built inside its values module. That keeps two places per value
    instead of one, so it is a decision to take with the user, never silently.




