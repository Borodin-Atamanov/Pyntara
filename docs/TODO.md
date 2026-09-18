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
public_address_report.py, country_report.py),
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
12. three_x_ui_xray_setup, 162 / 535 / 32, COUPLED, corrected 2026-09-17: it
    was planned as the first plain section of stage D, and the probe that
    opened its turn showed five live readers of the table instead of one, so it
    moved to stage F (point 85). Its shape is one flat table, so the shape
    question is only whether parts of it are lists of records
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
16. scrcpy_setup, done, 29 / 725 / 48, added to this list 2026-09-17: the
    section and the module were added to the repository after the list was
    written. Its commit moved two more values to the shared module: the mode of
    a menu entry (0644) and the mode of a delivered binary (0755) stood in the
    telegram_setup section as well, so both now live in values/common.py as
    LAUNCHER_FILE_MODE and EXECUTABLE_FILE_MODE and the telegram task reads
    them there; the icon mode stays with telegram_setup, where nothing shares
    it. The section also drops its own copies of the desktop user pair and of
    the two package budgets, which the shared module has carried since the
    first sections
17. swapfile_service_install, done, 17 / 380 / 51, and it moved the name of the
    /proc/meminfo RAM line to the shared module earlier than point 74 says: the
    value belongs to no section (it is a kernel interface name), the zram
    section reads the same line, so MEMINFO_TOTAL_KEY moved to
    values/common.py in this commit and zram_service reads it there when its
    turn comes, instead of touching this section twice. The commit also found a
    silent disagreement: the test harness of the section took a RAM multiplier
    of 2 from its own defaults while the shipped value is 1.6, so the expected
    swap size of the tests was wrong and nobody saw it while the harness faked
    the value; the tests now compute the expected size from the values
18. kde_keyboard_setup, done, 41 / 631 / 52, and it settled three points. First,
    its own copies of the desktop user pair are gone: the home of that user
    comes from the shared module, and the directory of the KDE configuration is
    declared as the home plus ".config" in the values module, so the home is
    written once and no section repeats the literal. Second, the name of the
    KDE shortcuts file and the boolean spelling of the KConfig files moved to
    values/common.py, because two sections write each of them (kde_keyboard_setup
    and kde_settings for the spelling, this section and vocalinux_setup for the
    file), and neither value belongs to a section by meaning. Third, the two
    switches of the section (reset_old_options, use_layout_switching) are 1 and 0
    and not True and False, per the user rule that a switch setting answers 1 or
    0; a values module had no switch before this section, so this is the pattern
    the remaining sections follow, and kde_settings will carry the same answer
    for its own flags
19. rustdesk_setup, done, 42 / 733 / 61, and it settled two things. The option
    list of the section is a list of records, so it became the tuple of the named
    record type RustdeskOption, which the values module declares (point 58). And
    the section produced the first entry of the exemption list of point 56: the
    separator between the proquint words of the permanent password is one space,
    and the generic rule that refuses a text with nothing in it is right to
    refuse it. tests/test_values.py now carries EXEMPT_VALUES with that one
    entry, a guard proves the entry names a declared value that the generic pass
    really refuses, so an exemption that became unnecessary fails the suite.
    The commit also removed 60 seconds of real waiting from the tests of the
    section: each test paid the settle pause of the service check, and the
    project rule says a test never waits out real time to learn an outcome. The
    autouse fixture zeroes that pause and the test of the pause sets its own two
    values, so the file went from 60.7 s to 0.6 s. The task also stopped reading
    the local vault path through the config: it reads the values module of
    local_vault_setup, which is already migrated
20. zram_service, done, 27 / 679 / 66, and it deviates from the shared-pair list
    of point 74 in one place, on purpose: compressor is NOT moved to
    values/common.py. The probe in the migrated zswap_service module shows why
    the pair the TOML probe reported is not one value: there the word sits inside
    the record ZswapParameter("compressor", "zstd"), that is, it is the value of
    one sysfs parameter of the zswap mechanism, while here it is the algorithm of
    a zram device. They are two settings that happen to carry the same word, and
    a machine that compresses zram devices with lzo while its zswap cache keeps
    zstd is a legitimate machine; sharing one constant would have to be undone.
    The section reads the meminfo line name from the shared module, which point
    17 moved there, and the values module keeps 26 values including the mode bit
    of the hot_add interface, which joins the file-mode rule list
21. sotavpn_setup, done, 24 / 663 / 67, and it refined the switch rule of point 88
    with the test that a real value produced: the four subscription flags
    (enabled, allow_private, allow_insecure, prepend) are booleans and not the 1
    and 0 of a switch setting, because they are fields of the JSON body the task
    posts to the panel API. json.dumps writes a Python True as the JSON word
    true and 1 as the number 1, and the panel field takes a boolean, so a 1 on
    the wire would be a different value. The 1 and 0 rule therefore covers a
    switch that a program of this repository reads, while a field of a foreign
    protocol keeps the type that protocol has; the test that compares the payload
    against True is what caught it. The section also keeps one live read of
    another section: the panel vocabulary of three_x_ui_xray_setup stays in the
    config document until that section lands in stage F, so the task still
    receives that object while its own values come from the module
22. tor_setup, 27 / 503 / 68
23. vocalinux_setup, done, 38 / 680 / 66, and it settled three things. Seven of
    its values were copies of a shared one: the desktop user name and home, the
    two deployed file modes (0644 and 0755), the name of the KConfig shortcut
    file and the package status timeout with the install retries, so the task
    reads values/common.py and every number stays as it was on the machine. The
    download cache and the home of the desktop user come from the values now, so
    the section contributes no make_config parameter at all, and the two the
    harness carried (vocalinux_home_dir, vocalinux_download_dir) are gone. The
    eleven call sites of the template writer keep an unused monkeypatch argument,
    the same shape the ssh_client_setup section chose, because the edit tool
    cannot aim a call whose text is identical at every site.
24. chrome_setup, done, 49 / 973 / 83, and it settled three things. Three of its
    values came from the shared module: the desktop user name and home and the
    mode of every deployed file. One read still belongs to another section: the
    address and the port of the local proxy come from three_x_ui_xray_setup and
    stay in the config document until that section lands in stage F, which the
    task marks with a comment. The twelve harness parameters of the section are
    gone, because every writable path is a value now and the test file points
    them at its temporary tree through one autouse fixture; the engine value it
    still needs (systemd_unit_dir) is the only config parameter left in that
    harness. The name of a local variable had to move: two helpers built a
    placeholder map called values, which is now the name of the module the task
    reads, so both maps are named placeholders.
25. ssh_daemon_setup, 64 / 659 / 90, COUPLED since 2026-09-17 (see point 113):
    nine live modules read its directives, so it cannot leave the config alone
26. i2pd_service_setup, 35 / 728 / 93, COUPLED
27. dnsproxy_setup, done, 78 / 1051 / 108, and it settled four things. The
    NextDNS profile id path and mode moved to values/common.py and the copy in
    the migrated nextdns_setup_system_wide module is gone, so the file has one
    home; the task reads it through the shared module. The section carried an
    install_retries value that no code ever read: the value guard refused the
    declaration, the value is dropped rather than frozen, and its TOML line goes
    with the config layer in stage G. Three values disagreed with the test
    document (bootstrap_resolvers held two addresses against the shipped
    eighteen, service_restart_seconds 2.0 against 7.0 and
    start_check_retry_delay_seconds 1.0 against 3.0), and the test that counted
    the generated arguments now computes the count from the values module. One
    annotation is a deliberate choice: service_template_path is a relative path
    and is declared as text, not as Path like the old loader typed it, which is
    the shape the other sections use for a relative path and joins to the clone
    root the same way.
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

Remainder plan refined on 2026-09-17 with the planning procedure, after the
telegram_setup section landed. The stage letters of points 69 to 72 keep their
meaning; the points below say what each stage contains and what was measured for
it. Every figure was measured on this machine today, and the probe that produced
it is named with the figure.

73. Measured facts the remainder rests on. config/ holds 32 TOML files with 6470
    lines, and the sections already migrated are dead data in it (point 61).
    src/pyntara/config/ holds 33 modules with 3250 lines: the loader (224), the
    field types (160), the engine dataclass (191), one module per section, and
    the large ones are system_metrics_setup (323), three_x_ui_xray_setup (307),
    kde_settings (207) and engine (191). Fourteen modules take the config path as
    a command line argument, measured with grep -rl "load_config(" src/pyntara:
    country_report, i2pd_address, metrics, metrics_collect, metrics_ingest,
    network_addresses, port_forwarding, port_forwarding_state,
    public_address_report, tor_address, upnp_forwarding, upnp_forwarding_state,
    yggdrasil_address and pyntara.py itself, which holds CONFIG_PATH =
    Path("config") relative to the clone root because inst.sh runs the engine
    there. A fifteenth owner is the task, not a module: the metrics task renders
    the repository config into system_config_path through render_config_source.
    src/pyntara/telemetry_pdf.py does NOT read the config, so the coupled
    paragraph above was corrected in that one name. Twelve engine value names are
    read by task modules today, counted with grep over src/pyntara/tasks:
    command_timeout_seconds (21 reads), root_owner_uid (10), root_owner_gid (10),
    systemd_unit_dir (6), system_python (2), bytes_per_mib (2), bytes_per_kib
    (2), and one read each for release_asset_architectures, progress_priority,
    percent_scale, os_release_family_keys and error_priority. engine.toml holds
    97 values, so the rest are read by the engine or by the runtime modules and
    are counted at the start of stage E. Deployment: inst.sh clones the
    repository into $CACHE_DIR/repo and runs uv sync there, so the values travel
    inside the wheel with no deployment change (point 67); the deployed metrics
    service runs /usr/local/lib/pyntara/venv/bin/python, a venv the task creates
    with uv venv and syncs with uv sync --project {repo_root} --active --locked
    --no-dev --no-editable, refreshed with the flag --reinstall-package pyntara.
    Tests: 28 test_config_*.py files, tests/config_checks.py 7388 lines imported
    by 9 files, tests/config_helpers.py 1243 lines, tests/test_config_coverage.py
    346 lines, the make_config factory used by 142 test files in 344 calls, and
    the whole tests tree 55996 lines. Documents: docs/guides/project-structure.md
    names config/ 37 times, docs/contracts/architecture.md 6,
    docs/simplified-architecture.md 5, docs/spec/config-content.md 5,
    docs/spec/system-metrics.md 3, README.md once, and fourteen section specs
    once each.
74. Shared values still standing in more than one section, found with a probe
    over config/*.toml today. Each pair moves to values/common.py in the commit
    of the section that arrives second, and that commit drops the copy of the
    section that is already migrated; a copy is never left behind silently. The
    pairs: desktop user name and home directory in chrome_setup,
    kde_keyboard_setup, kde_settings, scrcpy_setup, sotavpn_setup and
    vocalinux_setup; compressor "zstd" in zram_service and zswap_service (the
    second is migrated and holds its copy); dropin_file_mode 0644 in
    ssh_client_setup (migrated), ssh_daemon_setup and tor_setup;
    address_file_mode 0644 in i2pd_service_setup, tor_setup and
    yggdrasil_service_setup; executable_file_mode 0755 in telegram_setup
    (migrated), scrcpy_setup and vocalinux_setup; private_key_file_mode 0600 in
    ssh_daemon_setup and yggdrasil_service_setup; profile_id_file_path and
    profile_id_file_mode in nextdns_setup_system_wide (migrated) and
    dnsproxy_setup; meminfo_total_key in swapfile_service_install (migrated, it
    moved the name to the shared module as point 17 records) and zram_service;
    augeas_tools_package_name in ssh_client_setup (migrated) and
    ssh_daemon_setup; shortcuts_file_name in kde_keyboard_setup and
    vocalinux_setup; kconfig_true_value and kconfig_false_value in
    kde_keyboard_setup and kde_settings.
75. Stage D remainder, the plain sections in ascending read count, one commit
    each, with the telegram_setup pattern: the values module, the guard above
    every read, the tests moved to the values, the section registered in
    tests/test_values.py and tests/test_values_softness.py, the plan updated, the
    full gate, the merge into main and the branch deleted. telegram_setup and
    scrcpy_setup are done; the order that remains is vocalinux_setup
    (38 / 71 / 680), chrome_setup (49 / 87 / 973),
    ssh_daemon_setup (64 / 90 / 659), dnsproxy_setup (78 / 117 / 1051, its commit
    moves the nextdns profile id pair to common) and kde_settings (1607 / 177 /
    2244, a list of kconfig records, so a named record type and the tuple of
    those records per point 58). kde_keyboard_setup is next.
76. Stage E, the engine and the task catalog, one commit. values/engine.py
    carries the values of the run itself and stays the only fatal read (point
    37); values/tasks.py carries the catalog as a tuple of the named record type
    TaskSpec with name, description, depends and modes, because the catalog is a
    list of records (point 58); MODES leaves config/_fields.py for the engine
    values; src/pyntara/task_catalog.py keeps its public functions and reads the
    value instead of the loader. The stage comes before the coupled ones (point
    70) because a runtime module that stops reading the config takes the engine
    values from the package, so those values must exist first. The stage opens
    with a probe that lists every engine.toml name with its readers, because 97
    names against the twelve counted here need the truth rather than a guess.
    Tests: tests/test_config_engine.py, tests/test_config_tasks.py and
    tests/test_config_coverage.py are rewritten for the values, a check proves
    that a catalog which is not declared stops the run with one plain sentence
    and a nonzero exit code, and the values guards cover both new modules.
77. Stage F, the coupled services, one commit per service, in this order:
    port_forwarding_setup (port_forwarding.py, port_forwarding_state.py and
    network_addresses.py, plus the two check commands of the metrics table that
    hand the config path to network_addresses), i2pd_service_setup
    (i2pd_address.py), tor_setup (tor_address.py), upnp_forwarding_setup
    (upnp_forwarding.py, upnp_forwarding_state.py), yggdrasil_service_setup
    (yggdrasil_address.py) and system_metrics_setup last (metrics.py,
    metrics_collect.py, metrics_ingest.py, public_address_report.py,
    country_report.py and the task). In each the module imports the values
    package, the argv path parameter and the usage line that names CONFIG_PATH
    go, and the proof is a run of that module with no argument. The trap: the
    config_path parameter of src/pyntara/augeas.py is a file path for augeas and
    has nothing to do with the engine config, so it stays.
78. Stage F, the metrics service in detail, because it is the only deployed
    reader. The task stops rendering the repository config into
    system_config_path, so render_config_source, the config copy and the
    directory argument of the check go; {config_path} leaves the three commands
    send_service_command, ingest_service_command and collector_service_command;
    the module check table loses the path argument of its ten commands, which
    leaves the family argument of network_addresses intact; and the deployed
    service then reads exactly the values of the wheel the venv carries. The
    order inside the task is checked, not assumed: the venv is synced and the
    package reinstalled before the units are restarted, otherwise the service
    runs the values of the previous installation. system_config_path and the
    config copy belong to the same commit as the five modules, and stage G then
    finds no config copy left.
79. Stage G, removal, one commit: delete config/ (32 files, 6470 lines),
    src/pyntara/config/ (33 modules, 3250 lines), tests/config_checks.py (7388
    lines), tests/config_helpers.py (1243 lines), tests/test_config_coverage.py
    (346 lines) and the test files that existed only for the config layer;
    replace the make_config factory in the 142 test files that still use it with
    the values of the section under test; and drop the config layer from
    VALUE_DIRECTORIES of tests/test_config_value_guard.py so the values package
    becomes the single place a value may be declared (point 30). A test whose
    intent outlives the TOML (the catalog, the engine, the ban on a mode literal)
    moves to a values test with a written reason, and the rest go, as the vault
    section already did. The deletion goes through git and keeps the content in
    the history (point 62).
80. Stage H, documents, one commit: docs/guides/project-structure.md loses the
    config section map and names the values package with the one value one place
    rule, docs/spec/config-content.md becomes the rule for a values file with the
    type lists rewritten, docs/contracts/architecture.md and
    docs/simplified-architecture.md lose the Configuration paragraph,
    docs/spec/system-metrics.md loses the config path of the deployed services,
    README.md and docs/guides/developer-guide.md follow, and the fourteen section
    specs get their one line corrected.
81. Stage I, live proof on a target machine: a full provisioning run from
    inst.sh, then the deployed metrics service, its ingest and collector units
    and the check commands running with no /etc/pyntara/config.toml on the
    machine. The user has not named the machine, so this stage carries one
    assumption and waits for that word. It is the implied goal of the whole
    migration: a target machine that configures itself from the package with no
    TOML anywhere.
82. Test coverage of the remainder. Every stage keeps the existing guards: the
    read guard, the READ_VALUE_NAMES equality, every-value-read, the literal ban
    and the softness proof, each of which runs over the package, so a new section
    is covered the moment it joins them. Beyond that, stage E adds the fatal
    catalog check, stage F adds a check per module that it answers with no argv
    argument and a check that the task syncs the venv before it restarts the
    units, and stage G must show the gate green with the values guards unchanged
    after 8977 lines of config checks leave. Stage I is the only stage a machine
    can prove and no test replaces it.
83. Risks and weak points, with a mitigation each. The order rule assumes the
    read count measures the work, while three_x_ui_xray_setup has 34 reads and a
    4770 line test file (tests/test_3x_ui_xray_setup.py), so its commit may be
    larger than the number suggests: the test file is measured at the start and
    the commit stays one section, not one file. kde_settings is by far the
    largest section with 1607 values and a 2420 line test file, and its record
    shape decides the commit size: the shape is one named record type and the
    landing order puts it last among the plain sections. Stage G deletes
    18697 lines of sources and tests (9720 of config sources, 8977 of config
    checks) in one commit: no section is deleted before its values are green and
    its tests have moved. Another agent session shares this clone: every change
    takes a fresh branch from main, the tree is clean before and after every
    commit, and its files are never staged. The metrics venv can carry stale
    values on a machine where the task does not run again: the values travel with
    the package and the task reinstalls it, which is the same exposure as the
    copied config today. Stage I needs a machine the user must name, so it waits
    while every earlier stage is provable in the clone. The softness rule is the
    one mypy cannot check: every new section joins
    tests/test_values_softness.py in the same commit as its values module.
84. First stage: point 75 begins with swapfile_service_install, and that is the
    work to start with.
85. Correction of 2026-09-17, found by the probe that opened the turn of
    three_x_ui_xray_setup: its table has five live readers and not one, so it is
    a coupled section and leaves stage D for stage F. The readers, measured with
    grep -rn on the source: tasks/three_x_ui_xray_setup.py (the owner),
    tasks/sotavpn_setup.py (the panel vocabulary it reuses),
    tasks/chrome_setup.py (the local proxy address and port of the section),
    public_address_report.py (server_ip_services and server_ip_timeout_seconds)
    and country_report.py (country_services, country_word, the two country
    timeouts). The two report modules belong to the metrics cluster of stage F,
    so the section lands in that cluster, after chrome_setup and sotavpn_setup
    have taken their values from the package in stage D. The reason for waiting
    is the one the plan already gives for a coupled section: the six values the
    reports read live in the deployed copy of the config, so a values module
    next to a live TOML copy would be two truths, and each module changes its
    source once when the section lands with both reports in the same commit.
    The refinement of point 57 that this turn settled: a value that belongs to a
    section by meaning (the address and the port of its local proxy) is read by
    the second consumer straight from the values module of that section, and
    only a value that belongs to no section moves to common. The plan of stage D
    therefore continues with swapfile_service_install, and point 83 keeps its
    risk about the size of that section's test file.

Remainder plan refreshed on 2026-09-17 with the planning procedure, after the
telegram_setup, scrcpy_setup, swapfile_service_install and kde_keyboard_setup
sections landed. Every figure below was measured today, and the probe is named
with the figure.

86. The sections that remain in stage D, with their values, task lines and config
    reads, counted with grep -cE "^[a-z_]+ *= " over config/<section>.toml,
    wc -l over the task and grep -cE "cfg\." over the task: rustdesk_setup
    42 / 733 / 50, zram_service 27 / 679 / 59, sotavpn_setup 24 / 663 / 63,
    vocalinux_setup 38 / 680 / 66, chrome_setup 49 / 973 / 83, ssh_daemon_setup
    64 / 659 / 82, dnsproxy_setup 78 / 1051 / 108 and kde_settings
    1607 / 2244 / 171. The order of point 75 stands unchanged; the small
differences
    from the earlier figures come from the counting pattern, which counted the
    ctx.config reads as well. The coupled sections keep their own figures:
    three_x_ui_xray_setup 168 values, port_forwarding_setup 48,
    i2pd_service_setup 35, tor_setup 27, upnp_forwarding_setup 22,
    yggdrasil_service_setup 60 (measured today on the section table) and
    system_metrics_setup 117.
87. The shared module as it stands today:
    PACKAGE_STATUS_TIMEOUT_SECONDS, PACKAGE_INSTALL_RETRIES,
    SOURCE_VAULT_PRODUCTION, SOURCE_VAULT_DEFAULT, DESKTOP_USERNAME,
    DESKTOP_HOME_DIR, LAUNCHER_FILE_MODE, EXECUTABLE_FILE_MODE,
    MEMINFO_TOTAL_KEY, SHORTCUTS_FILE_NAME, KCONFIG_TRUE_VALUE and
    KCONFIG_FALSE_VALUE. The pairs still waiting to move, each on the turn of the
    section that arrives second: compressor "zstd" (zram_service with the
    migrated zswap_service), dropin_file_mode (ssh_daemon_setup with the migrated
    ssh_client_setup and with tor_setup), address_file_mode
    (i2pd_service_setup, tor_setup, yggdrasil_service_setup), private_key_file_mode
    (ssh_daemon_setup, yggdrasil_service_setup), the NextDNS profile id path and
    mode (dnsproxy_setup with the migrated nextdns_setup_system_wide),
    augeas_tools_package_name (ssh_daemon_setup with the migrated
    ssh_client_setup), and the desktop pair still standing in chrome_setup,
    kde_settings, sotavpn_setup and vocalinux_setup. A pair that belongs to no
    section moves on the first turn instead, as the meminfo line name, the KDE
    shortcuts file name and the boolean spelling did.
88. The switch rule the kde_keyboard_setup section set: a switch value answers 1
    or 0, never True or False. Sections with switches still to come are
    zram_service, sotavpn_setup, vocalinux_setup, chrome_setup, dnsproxy_setup
    and the coupled ones. The booleans inside the KConfig records of kde_settings
    are record fields, so they stay the words "true" and "false".
89. New risk found today, measured with a comparison of the make_config
    parameters of tests/support.py with the shipped sections: a test harness can
    carry its own default for a value the section ships, and while the harness
    fakes that value no test fails. The swapfile_service_install commit proved it
    with a RAM multiplier of 2 against the shipped 1.6. The same comparison
    finds sixteen such disagreements today, all of which will surface when their
    section migrates: kde_settings five (automatic_look_and_feel False against
    True, an empty kconfig record list against 114 records, a package list
    without python3-pyqt6, empty hidden places against six entries, and the
    touchpad method clickfinger against clickareas), yggdrasil_service_setup six
    (two key maps named nowhere else, a connection wait of 1 against 30, a peer
    probe of 0.0 against 60, a peer target count of 6 against 11 and an address
    save budget of 1 against 67), i2pd_service_setup two (two retry delays of 0.0
    against 2 and 1), tor_setup one (a start check retry delay of 0.0 against 1),
    port_forwarding_setup one and upnp_forwarding_setup one (the journal
    identifier). A section whose harness has no parameter for a value reads the
    test document instead, so its commit changes fewer expectations.
90. The test document is the second source of test values: base_config() in
    tests/config_helpers.py (1243 lines) builds it and make_config overrides
    parts of it. A migrated section leaves its part of the document as dead data,
    and 140 test files still call make_config in 339 places; both numbers fall
    with every section and stage G removes what is left.
91. Stage D remainder, one commit per section, in the order of point 75 and with
    the pattern of points 69 and 75. Each commit: the values module verified
    value by value against the TOML, the guard above every read, the helpers
    reading the module while an engine value stays a parameter, the tests moved
    off the config with the harness parameters of the section deleted in the same
    commit (point 89), the section registered in tests/test_values.py and
    tests/test_values_softness.py, its shared pairs moved with the copy of the
    already-migrated owner dropped, the plan updated, the full gate, the merge
    into main and the branch deleted. Expected extra work per section:
    rustdesk_setup moves no pair, zram_service moves compressor and reads the
    meminfo key from the shared module, sotavpn_setup and vocalinux_setup drop
    the desktop pair (vocalinux also drops the shortcuts file name, which the
    shared module now carries), chrome_setup drops the desktop pair, ssh_daemon
    _setup moves the dropin mode, the private key mode and the augeas package
    name, dnsproxy_setup moves the NextDNS profile id pair, and kde_settings is
    the large one: 1607 values, a list of KConfig records with a named record
    type, and the boolean spelling read from the shared module.
92. Stage E, the engine and the task catalog, unchanged except for its opening
    probe: it lists every engine.toml name with its readers (97 names today, 12
    of them counted among the task modules), values/tasks.py carries the catalog
    as a tuple of TaskSpec records (124 keys today, one record per task with
    name, description, depends and modes), MODES moves out of config/_fields.py,
    and a check proves that an undeclared catalog stops the run with one plain
    sentence and a nonzero exit code.
93. Stage F, the coupled services, with the correction of point 85 in the order:
    three_x_ui_xray_setup lands first in the cluster, together with
    public_address_report.py and country_report.py, because its table has five
    live readers; then port_forwarding_setup (port_forwarding.py,
    port_forwarding_state.py, network_addresses.py), i2pd_service_setup,
    tor_setup, upnp_forwarding_setup, yggdrasil_service_setup, and
    system_metrics_setup last with the five metrics modules and the task, where
    the config copy, render_config_source, {config_path} and the ten check
    commands lose the path.
94. Stage G, removal, with today's sizes: config/ 32 files and 6470 lines,
    src/pyntara/config/ 33 modules and 3250 lines, tests/config_checks.py 7388
    lines, tests/config_helpers.py 1243 lines (the test document),
    tests/test_config_coverage.py 346 lines, the 28 test_config_*.py files, the
    make_config factory in 140 test files and 339 calls, and the config layer of
    VALUE_DIRECTORIES. The order inside the stage: the config layer of the
    document, then the loader and the factory, then the files that existed only
    for it, with the gate green after each step.
95. Stage H, the documents, unchanged from point 80. The counts to drive to zero
    are measured today: docs/guides/project-structure.md names config/ 37 times,
    docs/spec/config-content.md 5, docs/contracts/architecture.md 6,
    docs/simplified-architecture.md 5, docs/spec/system-metrics.md 3, README.md
    once, and fourteen section specs once each.
96. Stage I, the live proof, unchanged from point 81 and still waiting for the
    name of the target machine.
97. Test coverage of the remainder, unchanged from point 82, plus the check this
    refresh adds to the working method: a section commit deletes the make_config
    parameters of that section, so a harness value that disagreed with the
    shipped one cannot survive its migration.
98. Risks, refreshed. The harness disagreements of point 89 surface as failing
    tests in the section that migrates, which is their point: they are fixed in
    the direction of the shipped value. kde_settings carries 1607 values and the
    largest test file, so it lands last among the plain sections and its record
    type is decided at its start. yggdrasil_service_setup is the largest coupled
    section and its two key maps must become named record types.
    three_x_ui_xray_setup keeps the risk of point 83: 4770 lines of tests. The
    metrics venv must be refreshed before the units restart, which the metrics
    commit checks rather than assumes. Stage G deletes about 18700 lines in one
    stage. Another agent session shares the clone, so every change takes a fresh
    branch from main and a clean tree before every commit. Stage I needs a
    machine the user names.
99. First stage: rustdesk_setup, with 42 values, no shared pair to move and six
    harness parameters to delete. The plan waits for the user's approval before
    the first section starts (stage 12 of the planning procedure).

Remainder plan refreshed again on 2026-09-17 with the planning procedure, after
the rustdesk_setup, zram_service and sotavpn_setup sections landed, and after a
compliance audit of the same day. Every figure below was measured on this
machine in this turn, and the probe is named with the figure.

100. Where the remainder stands. Seventeen sections are migrated and merged into
    main: cli_tools, imagemagick_setup, playwright_setup, ffmpeg_setup,
    hostname, add_extra_repos, nextdns_setup_system_wide, zswap_service,
    ssh_client_setup, local_vault_setup with vault_structure, telegram_setup,
    scrcpy_setup, swapfile_service_install, kde_keyboard_setup, rustdesk_setup,
    zram_service and sotavpn_setup. Stage D still holds five plain sections,
    stage E then migrates the engine and the task catalog, and stage F takes the
    seven coupled sections in the order of point 93.
101. The plain sections of stage D that remain, with their values, task lines,
    config reads and test file lines, counted with grep -cE "^[a-z_]+ *= " over
    config/<section>.toml, wc -l over the task, grep -cE "cfg\." over the task
    and wc -l over the test: kde_settings 112 settings and 327 KConfig records
    (see point 114; the figure 1607 of the earlier points counted the fields of
    the records as well) / 2244 / 171 / 2420. vocalinux
    _setup, chrome_setup and dnsproxy_setup stood before it and landed, as points
    23, 24 and 27 record, and ssh_daemon_setup left the stage for the coupled
    cluster (point 113). kde_settings is therefore the last plain section, and
    the read count keeps the order of point 75.
102. What each remaining plain section carries besides its own values.
    vocalinux_setup drops its own desktop user name and home directory and reads
    SHORTCUTS_FILE_NAME from the shared module, because both already arrived
    there; chrome_setup drops the desktop pair; ssh_daemon_setup moves
    dropin_file_mode, private_key_file_mode and augeas_tools_package_name;
    dnsproxy_setup moves the NextDNS profile id path and mode, of which the
    migrated nextdns_setup_system_wide holds the other copy; kde_settings is the
    large one, with 1607 values, a list of KConfig records that needs a named
    record type (point 58) and the boolean spelling read from the shared module.
    The desktop pair still stands in chrome_setup, kde_settings and
    vocalinux_setup, so the correction of point 87 is that sotavpn_setup dropped
    its copy when it landed.
103. The harness-disagreement count of point 89 is corrected from sixteen to
    eighteen by a fresh comparison of the make_config parameters of
    tests/support.py with the shipped TOML values. The two the earlier probe
    missed: ssh_daemon_setup carries a start check retry delay of 0.0 against the
    shipped 1, and dnsproxy_setup carries a download directory of /tmp/dnsproxy
    against the shipped /var/lib/pyntara/dnsproxy-download. The rest stand as
    point 89 lists them: five in kde_settings, six in yggdrasil_service_setup,
    two in i2pd_service_setup and one each in tor_setup, port_forwarding_setup
    and upnp_forwarding_setup. chrome_setup and vocalinux_setup carry none:
    every harness default of those two equals the shipped value, checked name by
    name. A disagreement is fixed in the direction of the shipped value in the
    commit of its section, which is the same commit that deletes the harness
    parameters of that section.
104. New fact from the compliance audit of this turn, and it belongs in the plan.
    Two values of the migrated local_vault_setup section still have a second home
    in the config document, because four modules of coupled sections read them
    there: src/pyntara/metrics.py reads local_vault_setup.local_vault_path at
    line 66 and local_vault_setup.pass_file_path at lines 78 and 82,
    src/pyntara/tasks/commit_final_system_metrics.py reads the vault path at line
    218, src/pyntara/xray_inbound.py at line 378 and src/pyntara/xray_panel.py at
    line 376. No stage D section touches those readers, and the machine works
    because the TOML section is still alive, but one value lives in two places
    until the stage F commit of each reader replaces the config read with the
    values module. The metrics task and metrics.py belong to the
    system_metrics_setup commit, the two xray modules to the
    three_x_ui_xray_setup commit.
105. Stage E, the engine and the task catalog, unchanged from point 92: the values
    of the run itself in values/engine.py (97 names in engine.toml today), the
    catalog as a tuple of TaskSpec records in values/tasks.py (124 keys today),
    the MODES table out of config/_fields.py, the opening probe that lists every
    engine name with its readers, and the check that an undeclared catalog stops
    the run with one plain sentence and a nonzero exit code.
106. Stage F, the coupled sections in the order of point 93, with the readers of
    point 104 added to the three_x_ui_xray_setup and system_metrics_setup
    commits. The sizes measured today on the section tables:
    three_x_ui_xray_setup 162 values, port_forwarding_setup 48,
    i2pd_service_setup 35, tor_setup 27, upnp_forwarding_setup 22,
    yggdrasil_service_setup 74 and system_metrics_setup 117. ssh_daemon_setup
    joined the cluster on 2026-09-17 with nine live readers (point 113); it has
    64 values and lands after the three_x_ui cluster, together with the readers
    of its directives, because those readers compare the port they forward to
    with the directive the section carries.
107. Stage G, removal, with the sizes measured today: config/ 32 TOML files and
    6470 lines, src/pyntara/config/ 33 modules and 3250 lines,
    tests/config_checks.py 7388 lines, tests/config_helpers.py 1243 lines,
    tests/test_config_coverage.py 346 lines, 27 test_config_*.py files, the
    make_config factory used by 138 test files in 333 calls, and the config layer
    of VALUE_DIRECTORIES. The order inside the stage is the one of point 94.
108. Stage H, documents, with the mentions of config/ measured today:
    docs/guides/project-structure.md 37, docs/contracts/architecture.md 6,
    docs/spec/config-content.md 5, docs/simplified-architecture.md 5,
    docs/spec/system-metrics.md 3, docs/guides/developer-guide.md 2, README.md 1
    and fourteen section specs once each.
109. Stage I, the live proof on a target machine, unchanged from point 96 and
    still waiting for the name of that machine.
110. Test coverage of the remainder, unchanged from point 97. The values guards
    run over the package, so a section is covered the moment it joins them, and
    the check this refresh repeats: a section commit deletes the make_config
    parameters of that section, so a harness default that disagrees with the
    shipped value (point 103) cannot survive its migration. The two homes of
    point 104 get one check in the commit that removes them: the module answers
    with no config read at all, which is the proof point 77 already asks of every
    coupled module.
111. Risks of the remainder, refreshed. kde_settings carries 1607 values and the
    largest test file, so it lands last among the plain sections and its record
    type is decided at its start. dnsproxy_setup has 78 values with 108 reads and
    a 757 line test, so its commit is smaller than its value count suggests,
    while chrome_setup has 49 values against a 1012 line test. The two homes of
    point 104 are the one place where the rule of the migration is broken today,
    so stage G must not delete the TOML section of local_vault_setup before those
    four readers have moved. Another agent session shares this clone, so every
    change takes a fresh branch from main with a clean tree before and after the
    commit. Stage I needs a machine the user names.
112. Next stage: kde_settings, the last plain section and the largest one. It is
    one commit and it is a job of its own, because the section carries 112
    settings and 327 KConfig records (point 114), 52 helpers with 171 config
    reads in a 2244 line task and 77 tests in a 2420 line test file. The commit
    moves the record type and the records into the values module (point 58),
    reads the boolean spelling and the shortcut file name from the shared module,
    and decides the five harness disagreements of point 89 in the direction of
    the shipped value.
113. Correction of 2026-09-17, found by the probe that opened the turn of
    ssh_daemon_setup: nine live modules read its directives through
    ssh_port_from_directives, so it is a coupled section and leaves stage D for
    stage F, exactly as three_x_ui_xray_setup did in point 85. The readers,
    measured with grep over src/pyntara: network_addresses.py,
    public_address_report.py, yggdrasil_address.py, port_forwarding.py,
    i2pd_address.py, upnp_forwarding.py, ssh.py (the shared reader of the port),
    and the two coupled tasks tor_setup and i2pd_service_setup. One reader goes
    further: port_forwarding.py reads root_ssh_dir and
    port_forwarding_private_key_file_name from the same section. The reason to
    wait is the one the plan gives for every coupled section: a values module
    next to a live TOML copy would be two truths about the port the machine
    forwards to. The order rule of point 75 is unaffected, and the next plain
    section is kde_settings.
114. Reconnaissance of kde_settings, the probe that opens its turn, measured on
    2026-09-17 before any edit. The section is not 1607 values: that figure came
    from counting every line that looks like a setting, and the file declares 112
    top-level settings besides 327 KConfig records, each with a file, a group, a
    key, a value and usually a type. The records are the reason the section is
    the largest: config/kde_settings.toml is 2244 lines. The task is 2244 lines
    with 52 helpers and 171 config reads, and tests/test_kde_settings.py is 2420
    lines with 77 tests. The record type already exists as KConfigRecord in
    src/pyntara/config/kde_settings.py, so the commit moves it and the 327
    records into the values module of the section (point 58). Four names the
    section holds are copies of a shared one and go: username and home_dir
    (values/common.py), global_shortcuts_file_name (SHORTCUTS_FILE_NAME) and the
    two boolean words kconfig_true_value and kconfig_false_value
    (KCONFIG_TRUE_VALUE, KCONFIG_FALSE_VALUE). Two modes need a decision at the
    start of the turn: script_file_mode is 0644 and default_file_mode is 0600,
    and neither is the shared pair of the desktop launcher and binary modes
    (0644 and 0755), because 0600 names a private file of the desktop user and a
    shared value would have to be undone, the way the zram compressor word is not
    shared. The five harness disagreements of point 89 belong to this section
    (automatic_look_and_feel False against true, an empty record list against the
    shipped records, a package list without python3-pyqt6, empty hidden places
    against six entries and the touchpad method clickfinger against clickareas),
    and the commit decides each in the direction of the shipped value.
115. The record list of kde_settings, prepared on 2026-09-17 by a read-only script
    over the TOML, which printed each record as one Python line. Measured facts
    the conversion rests on: 327 records; the only field shapes are file, group,
    key and value, with type added when the value is a flag and delete added when
    the key is removed instead of written; type is always the word "bool" and
    never anything else; three records are delete records and carry no value at
    all, so the record type needs a default for value and for delete; and one
    value is built from angle, round and square brackets plus both quote
    characters, so its Python literal must be escaped. That escaping is the
    reason the records are written by hand
    from the printed lines and then read back: an unescaped quote produces a
    syntax error that the gate catches, but a wrongly escaped one would produce a
    wrong desktop setting that only the desktop would notice. The same script
    proves the record count and the field shapes against the TOML after the
    conversion, so the move of 327 records is checked, not trusted.
116. Why the two homes of point 104 cannot be closed early, measured on
    2026-09-17 while trying exactly that as a small step. The vault pair is read
    through one shared helper, pyntara.metrics.open_runtime_vault, and seven
    modules call it: pyntara/telemetry_pdf.py, pyntara/metrics_send.py,
    pyntara/port_forwarding.py, pyntara/xray_inbound.py, pyntara/xray_panel.py,
    tasks/rustdesk_setup.py and tasks/nextdns_setup_system_wide.py, apart from
    the metrics modules of the system_metrics_setup section itself. Several test
    files create a real vault in a temporary directory and configure its path
    through make_config (test_metrics_send.py and
    test_commit_final_system_metrics.py at least), so a helper that stopped
    reading the config would send those tests to the shipped path under /var,
    which is a machine-safety failure, not a test failure. The pair therefore
    moves with the system_metrics_setup commit, in the same change as the module
    that reads it and every test that configures it, which is what point 93
    already asks. The shortcut was abandoned before any edit.
117. The opening probe of stage E, run on 2026-09-17 before its turn, measured
    with an AST walk over every source file and a tomllib read of both files.
    engine.toml holds 98 values and not 97, and every one of them is read as an
    attribute somewhere, so the stage declares all of them and none is dead. The
    names the task modules read are 22 and not the 12 counted before, with
    command_timeout_seconds first (29 task modules), then root_owner_uid and
    root_owner_gid (11 each), systemd_unit_dir (8), and three each for
    session_environment_command, session_environment_keys, system_python,
    release_asset_architectures and partial_download_file_suffix. The blast radius
    is the real risk of the stage: 47 modules outside the config package touch the
    engine config, 31 of them task modules and 16 runtime modules (upnp, logger,
    public_address, network_addresses, public_address_report, location, augeas,
    xray_certificate, xray_client, ssh_access, xray_facts, github_release,
    pyntara, task_runner, utils and xray_panel). The catalog is smaller than its
    figure suggests: config/tasks.toml holds 31 task records with the four fields
    name, description, depends and modes, which is the 124 of the earlier point
    counted as key-value pairs and never as tasks; the modes are desktop, minimal
    and server, and 22 records name dependencies. 59 test files use make_config or
    a real vault, so the stage's test work is broad rather than deep.
118. The harness and the test document measured against the shipped values on
    2026-09-17, with a script that loads the shipped config, builds make_config()
    and parses the text of tests/config_helpers.py. The harness disagrees with
    the shipped values in 50 fields spread over 20 sections, and the test document
    disagrees on 430 of the 1109 keys it declares, spread over all 31 sections.
    Point 89 counted eighteen because it compared the make_config parameters
    alone; the document is the larger source, and a section commit must move its
    tests off the document and not only off the harness parameters. For the
    sections that still have to land the numbers are, as document disagreements
    with the harness ones in brackets: kde_settings 46 (6),
    three_x_ui_xray_setup 41 (4), yggdrasil_service_setup 31 (4),
    system_metrics_setup 29 (6), ssh_daemon_setup 17 (2), tor_setup 12 (2),
    port_forwarding_setup 12, i2pd_service_setup 11 (2) and
    upnp_forwarding_setup 5. The sections already migrated keep their part of the
    document as dead data, which stage G removes together with the document.



