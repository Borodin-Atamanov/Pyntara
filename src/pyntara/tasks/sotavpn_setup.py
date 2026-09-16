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

1. The access key is read from the source vault entry the config names. An
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
from pyntara.config import (
    EngineConfig,
    SotavpnSetupConfig,
    ThreeXuiXraySetupConfig,
)
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


def _read_access_key(ctx: Context, cfg: SotavpnSetupConfig) -> str | None:
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
        ctx.repo_root, ctx.config.local_vault_setup, ctx.vault_password
    )
    if source is None:
        _log("the source vaults are not available: the Sota pool stays off")
        return None
    vault, vault_path = source
    entry = vault.find_entries(
        title=cfg.key_entry_title,
        group=vault.root_group,
        recursive=False,
        first=True,
    )
    if entry is None:
        _log(
            f"the source vault {vault_path} has no {cfg.key_entry_title} entry: "
            "the Sota pool stays off"
        )
        return None
    key: str | None = entry.password
    if not key:
        _log(
            f"the {cfg.key_entry_title} entry of {vault_path} carries no key: "
            "the Sota pool stays off"
        )
        return None
    _log(
        f"the Sota access key is read from the {cfg.key_entry_title} entry of "
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
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                try:
                    return ast.literal_eval(node.value)
                except ValueError:
                    return None
    return None


def _installed_settings_path(cfg: SotavpnSetupConfig) -> Path:
    """Path of the settings file of the installed bridge.

    The installation lives in the home directory of the desktop user, the
    same layout the installer of the bridge builds, so the HTTP port is
    read from the installed file and never held here.
    """

    return Path(cfg.home_dir) / cfg.user_install_relative_path / cfg.settings_file_name


def _service_state_command(cfg: SotavpnSetupConfig) -> list[str]:
    """The configured state query with the account and the unit filled in."""

    return [
        part.replace("{username}", cfg.username).replace(
            "{unit}", cfg.service_unit_name
        )
        for part in cfg.user_service_is_active_command
    ]


def _service_is_active(cfg: SotavpnSetupConfig, timeout: float) -> bool:
    """Whether the user service of the bridge reports itself active.

    The state is read through the user manager of the account, which works
    from a root run without a live session. A missing manager, a failed
    query and every other state answer False, so the caller installs the
    bridge or reports it.
    """

    try:
        result = run_command(
            _service_state_command(cfg), check=False, capture=True, timeout=timeout
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        _log(f"cannot read the state of {cfg.service_unit_name}: {exc}")
        return False
    return result.returncode == 0 and result.stdout.strip() == "active"


def _hand_the_work_directory_to_the_user(
    cfg: SotavpnSetupConfig, work_dir: Path
) -> None:
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
        record = pwd.getpwnam(cfg.username)
    except KeyError:
        _log(
            f"the account {cfg.username} is unknown: the installer may not "
            "read the extracted archive"
        )
        return
    apply_owner(work_dir, record.pw_uid, record.pw_gid)


def _fetch_the_bridge(
    cfg: SotavpnSetupConfig,
    engine: EngineConfig,
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

    work_dir = Path(tempfile.mkdtemp(prefix=cfg.archive_temp_prefix))
    _hand_the_work_directory_to_the_user(cfg, work_dir)
    archive = work_dir / f"{cfg.archive_temp_prefix}{cfg.archive_temp_suffix}"
    try:
        run_command(
            download_command(engine, archive, cfg.archive_url), timeout=timeout
        )
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
        for path in work_dir.rglob(cfg.installer_file_name)
        if path.is_file()
    ]
    if len(roots) != 1:
        warnings.append(
            f"the bridge archive carries no single {cfg.installer_file_name}"
        )
        shutil.rmtree(work_dir, ignore_errors=True)
        return None
    root = roots[0]
    if not (root / cfg.settings_file_name).is_file():
        warnings.append(
            f"the bridge archive carries no {cfg.settings_file_name} next to "
            f"the {cfg.installer_file_name}"
        )
        shutil.rmtree(work_dir, ignore_errors=True)
        return None
    return work_dir, root


def _run_the_installer(
    cfg: SotavpnSetupConfig,
    engine: EngineConfig,
    installer_path: Path,
    timeout: float,
) -> tuple[bool, str]:
    """Run the bridge installer as the desktop user, or report why not.

    The command is the configured one with the account, the home
    directory, the interpreter of the managed system and the path of the
    extracted installer filled in. The installer is started through the
    configured user wrapper, so the user-mode installation and its user
    service belong to the desktop account; the session environment the
    engine reads for that account goes into the environment of the call,
    so the user manager is reachable from a run that has no session of its
    own.
    """

    values = {
        "username": cfg.username,
        "home_dir": cfg.home_dir,
        "python": engine.system_python,
        "installer_path": str(installer_path),
    }
    command = [
        part.format_map(values)
        for part in (*cfg.runuser_command, *cfg.installer_command)
    ]
    environment = user_session_environment(
        cfg.username,
        command_template=engine.session_environment_command,
        keys=engine.session_environment_keys,
        timeout=timeout,
    )
    try:
        run_command(command, timeout=timeout, extra_env=environment or None)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return False, f"the bridge installer failed: {exc}"
    return True, "the bridge installer finished"


def _wait_for_the_bridge(
    cfg: SotavpnSetupConfig,
    engine: EngineConfig,
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
        if _service_is_active(cfg, timeout) and (
            port_listener_pid(engine, port, timeout) is not None
        ):
            _log(f"the bridge service is active and port {port} has a listener")
            return True
        elapsed = time.monotonic() - started
        if elapsed >= cfg.bridge_ready_wait_seconds:
            return False
        _log(
            f"waiting for the bridge on port {port}, {elapsed:.0f}s of "
            f"{cfg.bridge_ready_wait_seconds}s"
        )
        time.sleep(cfg.readiness_check_delay_seconds)


def _subscription_payload(
    cfg: SotavpnSetupConfig,
    sub_cfg: ThreeXuiXraySetupConfig,
    *,
    port: int,
    key: str,
) -> dict[str, object]:
    """The outbound subscription the panel stores, with the key inside.

    The field names are the panel vocabulary of the [three_x_ui_xray_setup]
    table, so a panel version that renames one is answered in the config.
    allow_private is what lets the panel fetch from the loopback address of
    the bridge, and the update interval is the panel job that keeps the
    node list fresh on its own.
    """

    fields = sub_cfg.panel_field_keys
    return {
        fields["subscription_remark"]: cfg.subscription_remark,
        fields["subscription_url"]: cfg.subscription_url_template.format(
            port=port, key=key
        ),
        fields["subscription_tag_prefix"]: sub_cfg.pool_member_prefix,
        fields["subscription_update_interval"]: (
            cfg.subscription_update_interval_seconds
        ),
        fields["subscription_enabled"]: cfg.subscription_enabled,
        fields["subscription_allow_private"]: cfg.subscription_allow_private,
        fields["subscription_allow_insecure"]: cfg.subscription_allow_insecure,
        fields["subscription_prepend"]: cfg.subscription_prepend,
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
    cfg: SotavpnSetupConfig,
    sub_cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    timeout: float,
) -> tuple[int | None, str | None]:
    """Wait for the panel to fetch the list; (nodes, message).

    The refresh call asks the panel to fetch the subscription address; the
    bridge answers from its own cache and asks the vendor when that cache
    is cold, which takes a few seconds. The panel is therefore asked for
    its own count until a list arrives, until it records an error, or until
    the configured budget is spent. Exactly one of the two answers is set:
    the count of the nodes, or a sentence naming what stopped the fetch.
    """

    fields = sub_cfg.panel_field_keys
    started = time.monotonic()
    while True:
        current = xui_client.find_outbound_subscription_by_remark(
            sub_cfg, env, cfg.subscription_remark, timeout
        )
        if current is not None:
            last_error = current.get(fields["subscription_last_error"])
            if last_error:
                return None, f"the panel could not fetch the node list: {last_error}"
            count = current.get(fields["subscription_outbound_count"])
            if isinstance(count, int) and count > 0:
                return count, None
        elapsed = time.monotonic() - started
        if elapsed >= cfg.subscription_fetch_wait_seconds:
            return None, (
                f"the panel listed no node list after "
                f"{cfg.subscription_fetch_wait_seconds} s: it fetches the "
                "subscription again on its own schedule"
            )
        _log(
            f"waiting for the node list, {elapsed:.0f}s of "
            f"{cfg.subscription_fetch_wait_seconds}s"
        )
        time.sleep(cfg.readiness_check_delay_seconds)


def _wait_for_the_pool(
    sub_cfg: ThreeXuiXraySetupConfig,
    env: dict[str, str],
    timeout: float,
    warnings: list[str],
) -> None:
    """Wait for the running core to report the pool, then log its pick.

    The panel rebuilds the core wherever a written configuration changes,
    and the outbounds the subscription brought in are such a change, so the
    first question can land in that window. The budget and the pause are
    the values of the three_x_ui table, the same ones its own stages wait
    with. A core that never reports the pool is a warning naming it.
    """

    tag_key = sub_cfg.xray_field_keys["tag"]
    fields = sub_cfg.panel_field_keys
    started = time.monotonic()
    while True:
        entries = xui_client.list_balancer_status(
            sub_cfg, env, (sub_cfg.pool_balancer_tag,), timeout
        )
        entry = next(
            (
                item
                for item in entries
                if item.get(tag_key) == sub_cfg.pool_balancer_tag
            ),
            None,
        )
        if entry is not None:
            _log(
                f"the pool {sub_cfg.pool_balancer_tag}: "
                f"running={entry.get(fields['balancer_running'])}, "
                f"selected={entry.get(fields['balancer_selected'])}"
            )
            return
        if time.monotonic() - started >= sub_cfg.core_ready_wait_seconds:
            warnings.append(
                f"the running core does not report the pool "
                f"{sub_cfg.pool_balancer_tag} yet: the pool of the panel "
                "applies with its next start"
            )
            return
        time.sleep(sub_cfg.readiness_check_delay_seconds)


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

    cfg = ctx.config.sotavpn_setup
    sub_cfg = ctx.config.three_x_ui_xray_setup
    engine = ctx.config.engine
    timeout = engine.command_timeout_seconds

    key = _read_access_key(ctx, cfg)
    if key is None:
        return TaskResult(
            success=True,
            changed=False,
            message=(
                "the Sota pool is not configured: the source vault carries no "
                f"{cfg.key_entry_title} entry with a key"
            ),
        )

    warnings: list[str] = []
    changed = False
    settings_path = _installed_settings_path(cfg)

    fetched = _fetch_the_bridge(cfg, engine, timeout, warnings)
    if fetched is not None:
        work_dir, root = fetched
        try:
            # The installer runs as it is, on every run: the task neither
            # looks at the settings of the archive nor decides whether the
            # install is needed, and what the installer does with the
            # settings on the machine is its own business.
            _log(f"installing the bridge for the account {cfg.username}")
            installed, message = _run_the_installer(
                cfg, engine, root / cfg.installer_file_name, timeout
            )
            _log(message)
            if installed:
                changed = True
            else:
                warnings.append(message)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    port = _settings_value(settings_path, cfg.settings_http_port_key)
    if not isinstance(port, int):
        warnings.append(
            f"the installed bridge settings {settings_path} carry no "
            f"{cfg.settings_http_port_key}: the panel has no address to "
            "subscribe to"
        )
        return TaskResult(
            success=True, changed=changed, warnings=tuple(warnings)
        )
    if not _wait_for_the_bridge(cfg, engine, port=port, timeout=timeout):
        warnings.append(
            f"the bridge did not answer with an active service on port {port} "
            f"within {cfg.bridge_ready_wait_seconds} s"
        )

    try:
        env = xui_client.panel_environment(sub_cfg, timeout)
    except (FileNotFoundError, RuntimeError) as exc:
        warnings.append(f"the Sota subscription was not configured: {exc}")
        return TaskResult(
            success=True, changed=changed, warnings=tuple(warnings)
        )

    payload = _subscription_payload(cfg, sub_cfg, port=port, key=key)
    existing = xui_client.find_outbound_subscription_by_remark(
        sub_cfg, env, cfg.subscription_remark, timeout
    )
    fields = sub_cfg.panel_field_keys
    if existing is not None and _subscription_matches(existing, payload):
        _log(
            f"the panel subscription {cfg.subscription_remark} is configured "
            "already"
        )
    else:
        ok, message = xui_client.upsert_outbound_subscription(
            sub_cfg, env, payload, timeout
        )
        if not ok:
            warnings.append(
                "the panel subscription was not written: "
                f"{_without_the_key(message, key)}"
            )
            return TaskResult(
                success=True, changed=changed, warnings=tuple(warnings)
            )
        changed = True
        _log(
            f"the panel subscription {cfg.subscription_remark}: "
            f"{_without_the_key(message, key)}"
        )

    current = xui_client.find_outbound_subscription_by_remark(
        sub_cfg, env, cfg.subscription_remark, timeout
    )
    subscription_id = None if current is None else current.get(fields["id"])
    nodes: int | None = None
    if subscription_id is None:
        warnings.append(
            f"the panel does not list the subscription {cfg.subscription_remark} "
            "after the write"
        )
    else:
        ok, message = xui_client.refresh_outbound_subscription(
            sub_cfg, env, subscription_id, timeout
        )
        _log(
            "the panel fetched the node list: "
            f"{_without_the_key(message, key)}"
        )
        if not ok:
            warnings.append(
                "the panel did not fetch the node list: "
                f"{_without_the_key(message, key)}"
            )
        nodes, note = _wait_for_the_nodes(cfg, sub_cfg, env, timeout)
        if note is not None:
            warnings.append(_without_the_key(note, key))
        elif nodes is not None:
            _log(f"the subscription carries {nodes} nodes")

    _wait_for_the_pool(sub_cfg, env, timeout, warnings)

    if nodes is None:
        message = (
            f"the Sota subscription {cfg.subscription_remark} is in place: "
            "the panel has no node list yet"
        )
    else:
        message = (
            f"the Sota subscription {cfg.subscription_remark} is in place: "
            f"the panel lists {nodes} nodes"
        )
    return TaskResult(
        success=True, changed=changed, message=message, warnings=tuple(warnings)
    )
