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
4. The rule of a value follows from its own annotation: tests/test_values.py
   walks every values module, reads the annotation of each declared value and
   applies the generic rule, which is that a text is not empty, a whole number
   is not negative, a path is absolute, and a tuple holds something whose
   elements are non-empty texts. A new value needs no line anywhere. This pass
   is also the only thing that looks at the shipped values, because a task test
   points the values at its own fixture tree and never exercises them.
5. A rule an annotation cannot express, and where a wrong value would break the
   machine silently, is one line in EXTRA_VALUE_RULES of tests/test_values.py:
   the module, the name and the rule. A guard refuses a name no module
   declares. Today the list holds one entry, the file mode of ffmpeg_setup.
6. tests/test_values.py also proves that READ_VALUE_NAMES names exactly the
   declared values, and by an AST scan that every declared value is read
   somewhere. A new values module is added to VALUES_MODULE_NAMES.
7. A task test points its values at its fixture with
   monkeypatch.setattr(module, "NAME", value); monkeypatch restores the shipped
   values whether the test passed or failed. The patch lives inside a
   per-section helper that takes monkeypatch and the fixture paths when the
   helper already takes them (ffmpeg_setup), and inside a small autouse fixture
   of the test module when the helper takes no arguments and many call sites
   use it (cli_tools): the fixture keeps those call sites unchanged, and a test
   that needs another set or another threshold patches the same names itself.
8. While a section is being migrated its TOML section and its config tests stay
   in place; only the task, its tests and the new rules move. Stage C removes
   the old sources in one go.
9. A section is one commit: the gate of scripts/check_gates.sh green, then a
   fast-forward merge into main and a push.

Sections, one commit each. Order rule set 2026-09-17: the smallest section
first, so every turn lands one whole section and main stays green; the work is
proportional to the number of value reads. Sizes were measured on 2026-09-17 as
values in the TOML section, lines of the task module, value reads in it. The
numbers below are the current order, not stable names: a section is named by its
module, and git log holds the commit of each finished one.

A section is COUPLED when a deployed runtime module reads the same values.
Those modules still load the config from a path argument, so such a section
cannot be migrated alone: its values would live twice, in the values package for
the task and in the TOML for the running service. A coupled section is migrated
together with its runtime module, which then imports the values package and
loses the config path argument, and the unit command loses {config_path}; the
values package lives inside the wheel for exactly this (point 33). The list was
probed again on 2026-09-17 with grep -rln load_config src/pyntara/*.py, which
answers fourteen modules, and the coupled sections are these: port_forwarding_
setup (port_forwarding.py, port_forwarding_state.py, network_addresses.py),
system_metrics_setup (metrics.py, metrics_collect.py, metrics_ingest.py,
public_address_report.py, country_report.py, telemetry_pdf.py),
i2pd_service_setup (i2pd_address.py), tor_setup (tor_address.py),
upnp_forwarding_setup (upnp_forwarding.py, upnp_forwarding_state.py),
yggdrasil_service_setup (yggdrasil_address.py) and the engine itself
(pyntara.py). Each is a turn of its own, and the order rule skips them until the
plain sections are done. The earlier text of this paragraph was wrong for three
sections (tor_setup, upnp_forwarding_setup, yggdrasil_service_setup): it was
read off a truncated command output, the defect is reported, and the probe above
is the correction.

1. cli_tools, done, 4 / 120 / 3
2. imagemagick_setup, done, 6 / 142 / 1
3. playwright_setup, done, 12 / 216 / 1
4. ffmpeg_setup, done, 11 / 224 / 1
5. hostname, done, 3 / 189 / 1
6. add_extra_repos, done, 13 / 411 / 17
7. nextdns_setup_system_wide, done, 4 / 152 / 10
8. port_forwarding_setup, 48 / 292 / 11, COUPLED, waits for its own turn
9. system_metrics_setup, 117 / 759 / 18, COUPLED, the largest by values: the
   collector, the ingest, the sender, the deployment and the two telemetry pdf
   values
10. zswap_service, done, 12 / 253 / 19
11. ssh_client_setup, done, 37 / 185 / 26
12. three_x_ui_xray_setup, 162 / 535 / 32
13. local_vault_setup and vault_structure, done, 11 / 467 / 33, with a
    consequence worth naming: secrets/regenerate_vault_by_config.py read the
    [vault_structure] table through the loader and now reads
    pyntara.values.vault_structure, so a structure field is added by changing
    the record type, the values and the script's field mapping instead of a
    TOML key. The loader cross-check that the local vault password title names
    a structure entry moved to tests/value_checks.py as
    check_vault_entry_title, and the read guard of tests/test_values.py now also
    scans secrets/, because a value read only by a maintenance script is not
    dead. The script tests that could not outlive the TOML (an unknown field, a
    non-string field, a rejected url, a field set for the future, a missing
    config file, the seed shapes that are not records) are gone with this
    reason: the record type of the values module carries the field set and the
    text types, and mypy refuses the rest at the module.
14. upnp_forwarding_setup, 22 / 293 / 35, COUPLED, waits for its own turn
15. telegram_setup, done, 20 / 449 / 37, and it landed the shared pair: the
    desktop user name and his home directory stood in eight TOML sections
    (chrome_setup, kde_keyboard_setup, kde_settings, playwright_setup,
    scrcpy_setup, sotavpn_setup, telegram_setup, vocalinux_setup), so both
    moved to values/common.py as DESKTOP_USERNAME and DESKTOP_HOME_DIR
    (point 57) and the telegram task reads them there; playwright_setup was
    switched in the same commit, and each of the remaining six sections drops
    its own copy when its turn comes
16. scrcpy_setup, 29 / 725 / 48, added to this list 2026-09-17: the section and
    the module were added to the repository after the list was written
17. swapfile_service_install, 17 / 380 / 51
18. kde_keyboard_setup, 41 / 631 / 52
19. rustdesk_setup, 42 / 733 / 61
20. zram_service, 27 / 679 / 66
21. sotavpn_setup, 24 / 659 / 67
22. tor_setup, 27 / 503 / 68
23. vocalinux_setup, 38 / 680 / 71
24. chrome_setup, 49 / 973 / 87
25. ssh_daemon_setup, 64 / 659 / 90
26. i2pd_service_setup, 35 / 728 / 93, COUPLED
27. dnsproxy_setup, 78 / 1051 / 117
28. yggdrasil_service_setup, 74 / 1249 / 147
29. kde_settings, 1607 / 2244 / 177, and last the engine with the task
    catalog, the most connected section of all, COUPLED

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
42. The guards of the values package: the shipped values pass the rule of their
    annotation; READ_VALUE_NAMES names exactly the declared values; every
    declared value is read somewhere; a value literal outside the values
    package fails the suite; and every extra rule names a declared value. No
    guard restricts which values module a module may read (decision 59).
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
    the task name; only its config goes away. How an internal helper of a large
    task obtains the values it needs is still open, question 64.
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

Decisions taken while the migration runs:

55. The rules of the values are generic (user decision of 2026-09-17). The two
    rejected alternatives were a full table of rules per value, and no rules at
    all. The annotation of a value carries its shape rule, tests/test_values.py
    applies it to every shipped value, and only a rule an annotation cannot
    express, whose absence would be silent, stays in EXTRA_VALUE_RULES. Reason:
    a task test points the values at its own fixture tree, so nothing else looks
    at the shipped values, while a table of two hundred rules is an entity
    nobody keeps in step. What no rules would have cost: the class of wrong
    values that leave the machine looking configured, such as a relative path or
    a backup suffix that names the file it should preserve.
56. Consequence of the generic pass to keep in view: a rule cannot be overridden
    for one value. A value whose shape the pass would refuse and which is
    legitimate, an empty list among them, needs a documented exemption list, and
    adding an entry is a decision, never a convenience. That list is empty
    today; the empty depends list of a task is the case expected to reach it.
57. Shared values live in one place for every task (user decision of
    2026-09-17): a value two or more tasks need is written once, the copies a
    section used to carry move to the shared place, and the sections read it
    there. A value one task needs stays in its own module. If a section ever
    needs another number, it declares its own value, and that is a decision.
58. A value that is a list of records keeps a named record type in code and the
    value is the tuple of those records (user decision of 2026-09-17). The
    rejected alternatives were a tuple of plain pairs, which asks the reader to
    remember which element is the key, and a dictionary, which loses the order.
59. No guard restricts which values module a module may read (user decision of
    2026-09-17): a module reads what it needs. The blast radius of a broken
    values file is therefore a convention of this migration, not a checked rule.
60. A values module of the engine that does not import is fatal, and it is
    reported as one plain sentence with a nonzero exit code, never as a Python
    traceback (user decision of 2026-09-17): the run cannot continue without
    those values, and the target machine has no developer to read a traceback.
61. The TOML sections of migrated sections stay in place as dead data until
    stage C removes everything at once (user decision of 2026-09-17). They harm
    nothing, and their list is the list of done sections above.
62. Stage C removes the old sources with git, so the content stays in the
    repository history and is recoverable (user decision of 2026-09-17). The
    project rule to delete only through the trash applies to resources outside
    the repository; the user decided this case, it is not an assumption.

63. Where the shared values live (user decision of 2026-09-17): in a separate
    module of the values package, src/pyntara/values/common.py, which any task
    that needs it reads. The values module of the engine keeps the values of
    the run itself. Reason: a module named after the run that also carries the
    values of package installation stops telling the truth, while a module
    named common states the rule in its own name. The shared pair of package
    install values moved there when this decision was recorded, and the three
    migrated sections read it from there.
64. How an internal helper of a large task obtains the values it needs (user
    decision of 2026-09-17): it reads the values module of its own task. The
    rejected alternatives were passing each value as a parameter, which
    lengthens every call and lets the order of the numbers be mistaken, and
    passing a small object built at the top of the task, which writes every
    value twice. A helper shared by two tasks still receives the values as
    parameters, because it must not depend on one task's module.

Remainder plan, set 2026-09-17 with the planning procedure. Numbered after the
decisions so no earlier number moves.

65. Stage A, done: the parameter table of zswap_service became a tuple of the
    named record type ZswapParameter, as decision 58 requires; a tuple of plain
    pairs was my mistake in the first version of that section.
66. Stage B, done: tests/test_values_softness.py proves for every migrated
    section that a value which is not declared costs the task and never the run,
    and that a values module which cannot be imported costs only its task. It
    found two real defects. The guard of add_extra_repos stood below a read of
    COMPONENTS, so a missing value crashed that task instead of warning about
    it; the guard now stands above every read. And load_task treated any import
    failure as a task nobody wrote, so a values module that could not be
    imported was reported as a missing task module; load_task now separates a
    ModuleNotFoundError that names the task module itself (not written yet, a
    normal state during development) from a failure inside the module or its
    values (raised, so the reason reaches the user).
67. Resolved assumption, measured 2026-09-17: the built wheel carries the values
    package, and the deployment already syncs the venv with --no-editable
    (venv_sync_command of the [system_metrics_setup] table), so a deployed
    service gets the values with no deployment change. What the coupled stage
    removes is only the config path mechanism.
68. Stage C, documents: done. The minimal truthful edit landed in the
    architecture contract (the Configuration paragraph, the behavioral-values
    paragraph, the resilience paragraph, the NextDNS exception line), in
    docs/spec/config-content.md (the header and the Rules paragraph), in
    README.md and in the adding-a-task step of docs/guides/developer-guide.md.
    A search for the old claims answers nothing. The full rewrite of the
    documents, including the Config section map of docs/guides/
    project-structure.md and the type lists of docs/spec/config-content.md,
    stays in Stage D (point 31); 17 mentions of config/ remain across five
    documents, and they describe the sections that are still in TOML.
69. Stage D, the plain sections, smallest first, one commit each: the section
    list above minus the COUPLED ones.
70. Stage E, the engine and the task catalog, before the coupled sections: a
    runtime module that stops reading the config takes the engine values from
    values/engine.py, so those values must exist first.
71. Stage F, the coupled sections, one service per commit: port forwarding
    (port_forwarding.py, port_forwarding_state.py, network_addresses.py), then
    i2pd (i2pd_address.py), then metrics (metrics.py, metrics_collect.py,
    metrics_ingest.py, public_address_report.py, country_report.py). In each, the
    runtime module imports the values package, the config path argument and
    {config_path} disappear, and the proof is a run of that module without the
    argument.
72. Stage G, removal: the old point 30. Stage H, documents: the old point 31.
    Stage I, live proof on a target machine: the old point 32.




