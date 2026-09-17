"""Task imagemagick_setup: install ImageMagick and tune its policy.

The target goal is a working, unthrottled ImageMagick on the command line.
On Kubuntu 26.04 and newer the archive already ships ImageMagick 7 (the meta
package imagemagick pulls imagemagick-7.q16), so apt is the whole install
path: no third-party repository, no AppImage, no source build and no version
chase. The task installs the configured packages through the shared
install_packages helper (utils.py) and succeeds only when every configured
package is installed, so a package that still fails is an error TaskResult:
the runner continues with the remaining tasks and never stops here.

After the packages are in place the task deploys the tuned security policy:
the template task_data/imagemagick_setup/policy.xml is written over the
system policy at POLICY_PATH. The package original is saved once next to
it as POLICY_PATH with POLICY_BACKUP_FILE_SUFFIX appended; ImageMagick
loads only the file named policy.xml, so the backup is never picked up.
A value the values module does not declare is reported as a warning and
nothing is changed.
"""

from __future__ import annotations

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import install_packages, package_is_installed, task_data_dir
from pyntara.values import common as common_values
from pyntara.values import imagemagick_setup as imagemagick_values
from pyntara.values import missing_value_names


def _deploy_policy(ctx: Context) -> tuple[bool, str | None]:
    """Write the tuned policy over the system file; return (changed, error).

    The backup is created once. The first time the system policy differs
    from the template, the current system file is copied to the file the
    configured backup suffix names, then the template is written to
    policy_path. When the target already matches the template nothing is
    written and an existing backup is never overwritten.
    """

    target = imagemagick_values.POLICY_PATH
    backup = target.with_name(
        f"{target.name}{imagemagick_values.POLICY_BACKUP_FILE_SUFFIX}"
    )
    template_path = (
        task_data_dir(ctx.repo_root, ctx.task_name)
        / imagemagick_values.POLICY_TEMPLATE_FILE_NAME
    )
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"cannot read policy template: {exc}"
    try:
        if target.exists():
            current = target.read_text(encoding="utf-8")
            if current == template:
                return False, None
            if not backup.exists():
                backup.write_text(current, encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(template, encoding="utf-8")
    except OSError as exc:
        return False, f"cannot write policy: {exc}"
    return True, None


def task(ctx: Context) -> TaskResult:
    """Install ImageMagick and deploy the tuned policy; skip when done.

    The goal is reached when every configured package is installed and the
    policy file already matches the template; the task then returns
    changed=False. Otherwise it installs the missing packages with the
    shared install_packages helper (apt index refreshed once unless
    skip_apt_update), deploys the policy and reports what it did. The
    version is not verified: the archive on the target platform carries the
    current ImageMagick and receives its updates through the regular apt
    upgrade.
    """

    absent = missing_value_names(
        imagemagick_values, imagemagick_values.READ_VALUE_NAMES
    ) + missing_value_names(common_values, common_values.READ_VALUE_NAMES)
    if absent:
        # A value that is not declared costs the task and never the run:
        # the names are reported in plain words and the runner carries on
        # with the remaining tasks.
        return TaskResult(
            success=True,
            message="the imagemagick values are not declared, nothing was changed",
            warnings=(
                "the imagemagick values are not declared: " + ", ".join(absent),
            ),
        )
    engine = ctx.config.engine
    install_timeout = engine.command_timeout_seconds
    status_timeout = common_values.PACKAGE_STATUS_TIMEOUT_SECONDS

    installed_packages: list[str] = []
    warnings: list[str] = []
    missing = [
        package
        for package in imagemagick_values.PACKAGES
        if not package_is_installed(engine, package, status_timeout)
    ]
    if missing:
        _log(f"installing: {', '.join(missing)}")
        installed, failures, install_warnings = install_packages(
            engine,
            missing,
            install_timeout=install_timeout,
            update_timeout=install_timeout,
            retries=common_values.PACKAGE_INSTALL_RETRIES,
            skip_update=ctx.skip_apt_update,
        )
        installed_packages = installed
        warnings.extend(install_warnings)
        if failures:
            failed_names = "; ".join(f"{name}: {reason}" for name, reason in failures)
            warnings.append(f"failed to install: {failed_names}")
    policy_changed, policy_error = _deploy_policy(ctx)
    if policy_error:
        # The deployed policy is the part of the machine the task owns, so
        # the reason is reported and the run completes with the packages
        # that were installed.
        warnings.append(policy_error)
    changed = bool(installed_packages) or policy_changed
    messages: list[str] = []
    if installed_packages:
        messages.append(f"installed {', '.join(installed_packages)}")
    if policy_changed:
        messages.append(f"policy written to {imagemagick_values.POLICY_PATH}")
    if not messages:
        messages.append("already installed")
    if warnings:
        messages.append(f"warnings: {'; '.join(warnings)}")
    return TaskResult(
        success=True,
        changed=changed,
        message="; ".join(messages),
        warnings=tuple(warnings),
    )
