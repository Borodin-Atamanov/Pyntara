"""Task sotavpn_setup: a pool of remote exits for the local proxy.

The three_x_ui_xray_setup task makes this machine a client of one remote
server and routes the classes the policy decides through it. This task
adds the server list of a paid Sota Connect account as a second source of
remote exits and lets the fastest member of the enlarged pool carry the
traffic, so the machine is not bound to one server any more
(docs/spec/sotavpn-setup.md).

The access key of the account is a secret: it travels in the subscription
address the panel stores, and no line of this module logs, prints or writes
it. Every message that could carry it passes through _without_the_key.

Order of the work:

1. The access key is read from the source vault entry the declared title names. An
   absent entry or an empty password means the pool is not configured for
   this machine: the task says so and changes nothing.
2. The bridge program of the Sotavpn repository is installed for the
   desktop user. Its branch archive is downloaded into a temporary
   directory and the installer runs as it is, on every run, so the
   machine always runs the code of the fetched branch. The task neither
   looks at the settings of the archive nor decides whether the install
   is needed.
3. The panel subscribes to the subscription address of the bridge: the
   subscription is created or updated, refreshed, and the panel is given
   time to fetch the list, whose nodes join the pool of the local proxy
   because the panel names them with the prefix that pool covers.

The pool itself is not built here: the client half of the panel, the
observatory and the load balancer included, belongs to the
three_x_ui_xray_setup task and exists on every machine, so this task only
feeds it (docs/spec/sotavpn-setup.md).
"""

from __future__ import annotations

import ast
import pwd
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from pyntara import xui as xui_client
from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.tasks.local_vault_setup import open_source_vault
from pyntara.utils import (
    apply_owner,
    download_command,
    port_listener_pid,
    run_command,
    user_session_environment,
)
from pyntara.values import common as common_values
from pyntara.values import engine as engine_values
from pyntara.values import missing_value_names
from pyntara.values import sotavpn_setup as values
from pyntara.values import three_x_ui_xray_setup as panel_values


def _read_access_key(ctx: Context) -> str | None:
    """The access key of the Sota account, or None when the pool is off.

    The source vaults of the fresh clone are the only source, opened the
    way the local_vault_setup task opens them: the production vault first,
    then the default vault, both with the run password. The runtime vault
    is not consulted, because the entry belongs to the account settings of
    the installation and not to the machine state. An unavailable vault, a
    missing entry and an empty password all answer None, and the caller
    reports the pool as not configured instead of failing.
    """

    source = open_source_vault(
        ctx.repo_root,
        common_values.SOURCE_VAULT_PRODUCTION,
        common_values.SOURCE_VAULT_DEFAULT,
        ctx.vault_password,
    )
    if source is None:
        _log("the source vaults are not available: the Sota pool stays off")
        return None
    vault, vault_path = source
    entry = vault.find_entries(
        title=values.KEY_ENTRY_TITLE,
        group=vault.root_group,
        recursive=False,
        first=True,
    )
    if entry is None:
        _log(
            f"the source vault {vault_path} has no {values.KEY_ENTRY_TITLE} "
            "entry: the Sota pool stays off"
        )
        return None
    key: str | None = entry.password
    if not key:
        _log(
            f"the {values.KEY_ENTRY_TITLE} entry of {vault_path} carries no key: "
            "the Sota pool stays off"
        )
        return None
    _log(
        f"the Sota access key is read from the {values.KEY_ENTRY_TITLE} entry of "
        f"{vault_path}"
    )
    return key


def _without_the_key(text: str, key: str) -> str:
    """The text with the access key replaced by a neutral word.

    The panel echoes the subscription address in some of its answers, and
    the address carries the key: a message that could reach the log or the
    terminal passes through here first, so the secret is never published.
    """

    if not key:
        return text
    return text.replace(key, "[secret]")


def _settings_value(settings_path: Path, name: str) -> object | None:
    """The literal value a settings line assigns, or None.

    The bridge settings file is a Python module of plain assignments, so
    the value is read with ast instead of being executed: the assignment
    is found by name and its right side is evaluated only when it is a
    literal. A missing file, a syntax error, a computed value and a name
    that is not assigned all answer None.
    """

    try:
        tree = ast.parse(settings_path.read_text(encoding="utf-8"))
    except OSError, SyntaxError:
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                try:
                    value: object = ast.literal_eval(node.value)
                except ValueError:
                    return None
                return value
    return None


def _installed_settings_path() -> Path:
    """Path of the settings file of the installed bridge.

    The installation lives in the home directory of the desktop user, the same
    layout the installer of the bridge builds, so the HTTP port is read from the
    installed file and never held here.
    """

    return (
        Path(common_values.DESKTOP_HOME_DIR)
        / values.USER_INSTALL_RELATIVE_PATH
        / values.SETTINGS_FILE_NAME
    )


def _service_state_command() -> list[str]:
    """The configured state query with the account and the unit filled in."""

    return [
        part.replace("{username}", common_values.DESKTOP_USERNAME).replace(
            "{unit}", values.SERVICE_UNIT_NAME
        )
        for part in values.USER_SERVICE_IS_ACTIVE_COMMAND
    ]


def _service_is_active(timeout: float) -> bool:
    """Whether the user service of the bridge reports itself active.

    The state is read through the user manager of the account, which works from a
    root run without a live session. A missing manager, a failed query and every
    other state answer False, so the caller installs the bridge or reports it.
    """

    try:
        result = run_command(
            _service_state_command(), check=False, capture=True, timeout=timeout
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        _log(f"cannot read the state of {values.SERVICE_UNIT_NAME}: {exc}")
        return False
    return result.returncode == 0 and result.stdout.strip() == "active"


def _hand_the_work_directory_to_the_user(work_dir: Path) -> None:
    """Make the temporary directory reachable by the account of the bridge.

    The archive is downloaded and extracted by the root run, while the
    installer runs as the desktop user and reads the extracted tree. The
    directory keeps the private mode of the temporary directory and gets
    the account as its owner, which is what lets that account traverse it
    without opening it to anyone else; the extracted files keep the modes
    of the archive and are readable. An unknown account leaves the
    directory as it is and says so, and the shared ownership helper skips
    the change outside a root run.
    """

    try:
        record = pwd.getpwnam(common_values.DESKTOP_USERNAME)
    except KeyError:
        _log(
            f"the account {common_values.DESKTOP_USERNAME} is unknown: the "
            "installer may not read the extracted archive"
        )
        return
    apply_owner(work_dir, record.pw_uid, record.pw_gid)


def _fetch_the_bridge(
    timeout: float,
    warnings: list[str],
) -> tuple[Path, Path] | None:
    """Download and extract the bridge archive; None with a warning.

    Returns the temporary directory the caller removes and the project
    root inside it. Every failure (a dead address, an unusable archive, a
    tree without the installer or the settings) is reported as a warning
    and answers None, because the rest of the task works from the
    installed bridge when one is there.
    """

    work_dir = Path(tempfile.mkdtemp(prefix=values.ARCHIVE_TEMP_PREFIX))
    _hand_the_work_directory_to_the_user(work_dir)
    archive = work_dir / f"{values.ARCHIVE_TEMP_PREFIX}{values.ARCHIVE_TEMP_SUFFIX}"
    try:
        run_command(download_command(archive, values.ARCHIVE_URL), timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        warnings.append(f"the bridge archive was not downloaded: {exc}")
        shutil.rmtree(work_dir, ignore_errors=True)
        return None
    try:
        with tarfile.open(archive, "r:gz") as package:
            package.extractall(work_dir, filter="data")
    except (tarfile.TarError, OSError) as exc:
        warnings.append(f"the bridge archive was not extracted: {exc}")
        shutil.rmtree(work_dir, ignore_errors=True)
        return None
    roots = [
        path.parent
        for path in work_dir.rglob(values.INSTALLER_FILE_NAME)
        if path.is_file()
    ]
    if len(roots) != 1:
        warnings.append(
            f"the bridge archive carries no single {values.INSTALLER_FILE_NAME}"
        )
        shutil.rmtree(work_dir, ignore_errors=True)
        return None
    root = roots[0]
    if not (root / values.SETTINGS_FILE_NAME).is_file():
        warnings.append(
            f"the bridge archive carries no {values.SETTINGS_FILE_NAME} next to "
            f"the {values.INSTALLER_FILE_NAME}"
        )
        shutil.rmtree(work_dir, ignore_errors=True)
        return None
    return work_dir, root


def _run_the_installer(
    installer_path: Path,
    timeout: float,
) -> tuple[bool, str]:
    """Run the bridge installer as the desktop user, or report why not.

    The command is the configured one with the account, the home
    directory, the interpreter of the managed system and the path of the
    extracted installer filled in. The installer is started through the
    configured user wrapper, so the user-mode installation and its user
    service belong to the desktop account; the session environment the run
    reads for that account goes into the environment of the call,
    so the user manager is reachable from a run that has no session of its
    own.
    """

    placeholders = {
        "username": common_values.DESKTOP_USERNAME,
        "home_dir": common_values.DESKTOP_HOME_DIR,
        "python": engine_values.SYSTEM_PYTHON,
        "installer_path": str(installer_path),
    }
    command = [
        part.format_map(placeholders)
        for part in (*values.RUNUSER_COMMAND, *values.INSTALLER_COMMAND)
    ]
    environment = user_session_environment(
        common_values.DESKTOP_USERNAME,
        command_template=engine_values.SESSION_ENVIRONMENT_COMMAND,
        keys=engine_values.SESSION_ENVIRONMENT_KEYS,
        timeout=timeout,
    )
    try:
        run_command(command, timeout=timeout, extra_env=environment or None)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return False, f"the bridge installer failed: {exc}"
    return True, "the bridge installer finished"


def _wait_for_the_bridge(
    *,
    port: int,
    timeout: float,
) -> bool:
    """Wait until the user service is active and the port has a listener.

    The service becomes active as soon as systemd started the program,
    which can be a moment before the listener answers, and the panel fetch
    of the same run needs the listener. The state and the listener are
    therefore checked together, once immediately and then after the
    configured pause, until the configured budget is spent.
    """

    started = time.monotonic()
    while True:
        if _service_is_active(timeout) and (
            port_listener_pid(port, timeout) is not None
        ):
            _log(f"the bridge service is active and port {port} has a listener")
            return True
        elapsed = time.monotonic() - started
        if elapsed >= values.BRIDGE_READY_WAIT_SECONDS:
            return False
        _log(
            f"waiting for the bridge on port {port}, {elapsed:.0f}s of "
            f"{values.BRIDGE_READY_WAIT_SECONDS}s"
        )
        time.sleep(values.READINESS_CHECK_DELAY_SECONDS)


def _subscription_payload(
    *,
    port: int,
    key: str,
) -> dict[str, object]:
    """The outbound subscription the panel stores, with the key inside.

    The field names are the panel vocabulary of the three_x_ui_xray_setup
    section, so a panel version that renames one is answered in its values
    module. allow_private is what lets the panel fetch from the loopback
    address of the bridge, and the update interval is the panel job that
    keeps the node list fresh on its own.
    """

    fields = panel_values.PANEL_FIELD_KEYS
    return {
        fields["subscription_remark"]: values.SUBSCRIPTION_REMARK,
        fields["subscription_url"]: values.SUBSCRIPTION_URL_TEMPLATE.format(
            port=port, key=key
        ),
        fields["subscription_tag_prefix"]: panel_values.POOL_MEMBER_PREFIX,
        fields["subscription_update_interval"]: (
            values.SUBSCRIPTION_UPDATE_INTERVAL_SECONDS
        ),
        fields["subscription_enabled"]: values.SUBSCRIPTION_ENABLED,
        fields["subscription_allow_private"]: values.SUBSCRIPTION_ALLOW_PRIVATE,
        fields["subscription_allow_insecure"]: (values.SUBSCRIPTION_ALLOW_INSECURE),
        fields["subscription_prepend"]: values.SUBSCRIPTION_PREPEND,
    }


def _subscription_matches(
    existing: dict[str, object],
    payload: dict[str, object],
) -> bool:
    """Whether the stored subscription already carries the wanted values.

    Only the fields of the payload are compared: the panel adds its own
    bookkeeping (the identifier, the fetch counters, the last error), and
    a difference there is not a reason to write the subscription again.
    """

    return all(existing.get(name) == value for name, value in payload.items())


def _wait_for_the_nodes(
    env: dict[str, str],
    timeout: float,
) -> tuple[int | None, str | None, bool]:
    """Wait for the panel to fetch the list; (nodes, message, failed).

    The refresh call asks the panel to fetch the subscription address; the
    bridge answers from its own cache and asks the vendor when that cache
    is cold, which takes a few seconds. The panel is therefore asked for
    its own count until a list arrives, until it records an error, or until
    the configured budget is spent. Exactly one of the first two answers is
    set: the count of the nodes, or a sentence naming what stopped the
    fetch. The third value says whether that sentence is a failure the
    panel recorded, in which case the caller warns, or only the budget
    running out while the panel is still fetching, which is a fact about
    the machine and not a defect of the run: the subscription is already
    written and the panel fetches it again on its own schedule.
    """

    fields = panel_values.PANEL_FIELD_KEYS
    started = time.monotonic()
    while True:
        current = xui_client.find_outbound_subscription_by_remark(
            env, values.SUBSCRIPTION_REMARK, timeout
        )
        if current is not None:
            last_error = current.get(fields["subscription_last_error"])
            if last_error:
                return (
                    None,
                    f"the panel could not fetch the node list: {last_error}",
                    True,
                )
            count = current.get(fields["subscription_outbound_count"])
            if isinstance(count, int) and count > 0:
                return count, None, False
        elapsed = time.monotonic() - started
        if elapsed >= values.SUBSCRIPTION_FETCH_WAIT_SECONDS:
            return (
                None,
                (
                    f"the panel listed no node list after "
                    f"{values.SUBSCRIPTION_FETCH_WAIT_SECONDS} s: it fetches "
                    "the subscription again on its own schedule"
                ),
                False,
            )
        _log(
            f"waiting for the node list, {elapsed:.0f}s of "
            f"{values.SUBSCRIPTION_FETCH_WAIT_SECONDS}s"
        )
        time.sleep(values.READINESS_CHECK_DELAY_SECONDS)


def _wait_for_the_pool(
    env: dict[str, str],
    timeout: float,
    warnings: list[str],
) -> None:
    """Wait for the running core to report the pool, then log its pick.

    The panel rebuilds the core wherever a written configuration changes,
    and the outbounds the subscription brought in are such a change, so the
    first question can land in that window. The budget and the pause are
    the values of the three_x_ui_xray_setup section, the same ones its own
    stages wait with. A core that never reports the pool is a warning
    naming it.
    """

    tag_key = panel_values.XRAY_FIELD_KEYS["tag"]
    fields = panel_values.PANEL_FIELD_KEYS
    started = time.monotonic()
    while True:
        entries = xui_client.list_balancer_status(
            env, (panel_values.POOL_BALANCER_TAG,), timeout
        )
        entry = next(
            (
                item
                for item in entries
                if item.get(tag_key) == panel_values.POOL_BALANCER_TAG
            ),
            None,
        )
        if entry is not None:
            _log(
                f"the pool {panel_values.POOL_BALANCER_TAG}: "
                f"running={entry.get(fields['balancer_running'])}, "
                f"selected={entry.get(fields['balancer_selected'])}"
            )
            return
        if time.monotonic() - started >= panel_values.CORE_READY_WAIT_SECONDS:
            warnings.append(
                f"the running core does not report the pool "
                f"{panel_values.POOL_BALANCER_TAG} yet: the pool of the panel "
                "applies with its next start"
            )
            return
        time.sleep(panel_values.READINESS_CHECK_DELAY_SECONDS)


def task(ctx: Context) -> TaskResult:
    """Feed the pool of the local proxy with the nodes of the account.

    The task is the source of remote exits of the panel: it installs the
    bridge that serves the Sota server list and subscribes the panel to
    it. The bridge is installed on every run, so the machine always runs
    the code of the fetched branch. The pool that carries the remote
    classes, with its observatory and its load balancer, was built by
    three_x_ui_xray_setup and is not touched here: the nodes of this
    subscription join it because the panel names them with the prefix that
    pool covers. Every step that could not be reached is a warning of a
    completed task, so one dead step (a bridge that does not answer, a
    panel that cannot fetch) leaves the machine with the rest configured
    and the warning names what to look at. A run whose subscription
    already matches writes no subscription, but it still installs the
    bridge again.
    """

    absent = missing_value_names(values, values.READ_VALUE_NAMES) + missing_value_names(
        common_values, common_values.READ_VALUE_NAMES
    )
    if absent:
        # A value that is not declared costs the task and never the run: the
        # names are reported in plain words and the runner carries on with the
        # remaining tasks. The guard stands above every read.
        return TaskResult(
            success=True,
            message="the sotavpn_setup values are not declared, nothing was changed",
            warnings=(
                "the sotavpn_setup values are not declared: " + ", ".join(absent),
            ),
        )
    # The panel vocabulary belongs to the three_x_ui_xray_setup section, so
    # this task reads it from that section's values module.
    timeout = engine_values.COMMAND_TIMEOUT_SECONDS
    force = ctx.task_name in ctx.force_tasks

    key = _read_access_key(ctx)
    if key is None:
        return TaskResult(
            success=True,
            changed=False,
            message=(
                "the Sota pool is not configured: the source vault carries no "
                f"{values.KEY_ENTRY_TITLE} entry with a key"
            ),
        )

    warnings: list[str] = []
    changed = False
    settings_path = _installed_settings_path()

    fetched = _fetch_the_bridge(timeout, warnings)
    if fetched is not None:
        work_dir, root = fetched
        try:
            # The installer runs as it is, on every run: the task neither
            # looks at the settings of the archive nor decides whether the
            # install is needed, and what the installer does with the
            # settings on the machine is its own business.
            _log(
                "installing the bridge for the account "
                f"{common_values.DESKTOP_USERNAME}"
            )
            installed, message = _run_the_installer(
                root / values.INSTALLER_FILE_NAME, timeout
            )
            _log(message)
            if installed:
                changed = True
            else:
                warnings.append(message)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    port = _settings_value(settings_path, values.SETTINGS_HTTP_PORT_KEY)
    if not isinstance(port, int):
        warnings.append(
            f"the installed bridge settings {settings_path} carry no "
            f"{values.SETTINGS_HTTP_PORT_KEY}: the panel has no address to "
            "subscribe to"
        )
        return TaskResult(success=True, changed=changed, warnings=tuple(warnings))
    if not _wait_for_the_bridge(port=port, timeout=timeout):
        warnings.append(
            f"the bridge did not answer with an active service on port {port} "
            f"within {values.BRIDGE_READY_WAIT_SECONDS} s"
        )

    try:
        env = xui_client.panel_environment(timeout)
    except (FileNotFoundError, RuntimeError) as exc:
        warnings.append(f"the Sota subscription was not configured: {exc}")
        return TaskResult(success=True, changed=changed, warnings=tuple(warnings))

    payload = _subscription_payload(port=port, key=key)
    existing = xui_client.find_outbound_subscription_by_remark(
        env, values.SUBSCRIPTION_REMARK, timeout
    )
    fields = panel_values.PANEL_FIELD_KEYS
    if existing is not None and _subscription_matches(existing, payload) and not force:
        _log(
            f"the panel subscription {values.SUBSCRIPTION_REMARK} is configured already"
        )
    else:
        ok, message = xui_client.upsert_outbound_subscription(
            env, payload, timeout
        )
        if not ok:
            warnings.append(
                "the panel subscription was not written: "
                f"{_without_the_key(message, key)}"
            )
            return TaskResult(success=True, changed=changed, warnings=tuple(warnings))
        changed = True
        _log(
            f"the panel subscription {values.SUBSCRIPTION_REMARK}: "
            f"{_without_the_key(message, key)}"
        )

    current = xui_client.find_outbound_subscription_by_remark(
        env, values.SUBSCRIPTION_REMARK, timeout
    )
    subscription_id = None if current is None else current.get(fields["id"])
    nodes: int | None = None
    if subscription_id is None:
        warnings.append(
            f"the panel does not list the subscription "
            f"{values.SUBSCRIPTION_REMARK} after the write"
        )
    else:
        ok, message = xui_client.refresh_outbound_subscription(
            env, subscription_id, timeout
        )
        _log(f"the panel fetched the node list: {_without_the_key(message, key)}")
        if not ok:
            warnings.append(
                "the panel did not fetch the node list: "
                f"{_without_the_key(message, key)}"
            )
        nodes, note, failed = _wait_for_the_nodes(env, timeout)
        if note is not None:
            if failed:
                warnings.append(_without_the_key(note, key))
            else:
                _log(_without_the_key(note, key))
        elif nodes is not None:
            _log(f"the subscription carries {nodes} nodes")

    _wait_for_the_pool(env, timeout, warnings)

    if nodes is None:
        message = (
            f"the Sota subscription {values.SUBSCRIPTION_REMARK} is in place: "
            "the panel has no node list yet"
        )
    else:
        message = (
            f"the Sota subscription {values.SUBSCRIPTION_REMARK} is in place: "
            f"the panel lists {nodes} nodes"
        )
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )
