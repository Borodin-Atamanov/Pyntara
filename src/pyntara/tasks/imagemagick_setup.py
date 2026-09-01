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
system policy at cfg.policy_path. The package original is saved once next to
it as policy_path.bak; ImageMagick loads only the file named policy.xml, so
the backup is never picked up.
"""

from __future__ import annotations

from pathlib import Path

from pyntara.context import Context
from pyntara.logger import log_progress as _log
from pyntara.models import TaskResult
from pyntara.utils import install_packages, package_is_installed

# The template lives in the repository clone; REPO_ROOT is monkeypatched by
# the tests to point at a fixture (docs/guides/developer-guide.md).
REPO_ROOT = Path(__file__).resolve().parents[3]


def _deploy_policy(ctx: Context) -> tuple[bool, str | None]:
    """Write the tuned policy over the system file; return (changed, error).

    The backup is created once. The first time the system policy differs
    from the template, the current system file is copied to
    policy_path.bak, then the template is written to policy_path. When the
    target already matches the template nothing is written and an existing
    backup is never overwritten.
    """

    cfg = ctx.config.imagemagick_setup
    target = cfg.policy_path
    backup = target.with_name(f"{target.name}.bak")
    template_path = REPO_ROOT / "task_data" / "imagemagick_setup" / "policy.xml"
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

    cfg = ctx.config.imagemagick_setup
    install_timeout = ctx.config.engine.command_timeout_seconds

    installed_packages: list[str] = []
    warnings: list[str] = []
    missing = [
        package
        for package in cfg.packages
        if not package_is_installed(package, cfg.package_status_timeout_seconds)
    ]
    if missing:
        _log(f"installing: {', '.join(missing)}")
        installed, failures, install_warnings = install_packages(
            missing,
            install_timeout=install_timeout,
            update_timeout=install_timeout,
            retries=cfg.package_install_retries,
            skip_update=ctx.skip_apt_update,
        )
        installed_packages = installed
        warnings.extend(install_warnings)
        if failures:
            failed_names = "; ".join(f"{name}: {reason}" for name, reason in failures)
            detail = f"failed to install: {failed_names}"
            if warnings:
                detail = f"{detail}; {'; '.join(warnings)}"
            return TaskResult(
                success=False, changed=bool(installed_packages), error=detail
            )
    policy_changed, policy_error = _deploy_policy(ctx)
    if policy_error:
        return TaskResult(
            success=False, changed=bool(installed_packages), error=policy_error
        )
    changed = bool(installed_packages) or policy_changed
    messages: list[str] = []
    if installed_packages:
        messages.append(f"installed {', '.join(installed_packages)}")
    if policy_changed:
        messages.append(f"policy written to {cfg.policy_path}")
    if not messages:
        messages.append("already installed")
    if warnings:
        messages.append(f"warnings: {'; '.join(warnings)}")
    return TaskResult(success=True, changed=changed, message="; ".join(messages))
