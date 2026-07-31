from __future__ import annotations

import os
import stat
import subprocess
import tarfile
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _build_source_tar(archive_path: Path) -> None:
    source_root = archive_path.parent / "repo-tree"
    project_dir = source_root / "Pyntara-main"
    project_dir.mkdir(parents=True)
    (project_dir / "pyproject.toml").write_text(
        "[project]\nname='pyntara'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    with tarfile.open(archive_path, "w") as archive:
        archive.add(project_dir / "pyproject.toml", arcname="pyproject.toml")


def test_bootstrap_uses_persistent_git_cache_when_piped(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_text = (repo_root / "i.sh").read_text(encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trace_path = tmp_path / "trace.log"
    source_tar = tmp_path / "source.tar"
    _build_source_tar(source_tar)

    _write_executable(
        fake_bin / "apt-get",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'apt-get %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
        ),
    )
    _write_executable(
        fake_bin / "uv",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "if [[ -z \"${UV_CACHE_DIR:-}\" ]]; then\n"
            "  exit 1\n"
            "fi\n"
            "printf 'uv %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
            "printf 'uv_cache %s\\n' \"$UV_CACHE_DIR\" >> \"$PYNTARA_TEST_TRACE\"\n"
        ),
    )
    _write_executable(
        fake_bin / "git",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'git %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
            "if [[ \"$1\" == clone ]]; then\n"
            "  cache_dir=\"${@: -1}\"\n"
            "  mkdir -p \"$cache_dir\"\n"
            "  exit 0\n"
            "fi\n"
            "if [[ \"$3\" == rev-parse ]]; then\n"
            "  ref=\"${@: -1}\"\n"
            "  if [[ \"$ref\" == origin/main ]] || \\\n"
            "     [[ \"$ref\" == main ]] || \\\n"
            "     [[ \"$ref\" == FETCH_HEAD ]]; then\n"
            "    exit 0\n"
            "  fi\n"
            "  exit 1\n"
            "fi\n"
            "if [[ \"$3\" == archive ]]; then\n"
            "  output=''\n"
            "  while (($#)); do\n"
            "    if [[ \"$1\" == --output ]]; then\n"
            "      output=\"$2\"\n"
            "      break\n"
            "    fi\n"
            "    shift\n"
            "  done\n"
            "  cp \"$PYNTARA_TEST_SOURCE_TAR\" \"$output\"\n"
            "  exit 0\n"
            "fi\n"
        ),
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["PYNTARA_ROOT_EUID"] = str(os.geteuid())
    env["PYNTARA_STATE_DIR"] = str(tmp_path / "state")
    env["PYNTARA_LOG_DIR"] = str(tmp_path / "logs")
    env["PYNTARA_WORK_BASE_DIR"] = str(tmp_path / "work")
    env["PYNTARA_REPO_CACHE_DIR"] = str(tmp_path / "cache" / "Pyntara.git")
    env["PYNTARA_UV_CACHE_DIR"] = str(tmp_path / "cache" / "uv")
    env["PYNTARA_TEST_TRACE"] = str(trace_path)
    env["PYNTARA_TEST_SOURCE_TAR"] = str(source_tar)

    first = subprocess.run(
        ["bash"],
        input=script_text,
        text=True,
        cwd=repo_root,
        env=env,
        capture_output=True,
        check=False,
    )
    second = subprocess.run(
        ["bash"],
        input=script_text,
        text=True,
        cwd=repo_root,
        env=env,
        capture_output=True,
        check=False,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    trace = trace_path.read_text(encoding="utf-8")
    assert (
        "git clone --bare --depth 1 --branch main "
        "https://github.com/Borodin-Atamanov/Pyntara.git"
    ) in trace
    assert (
        "git --git-dir" in trace
        and "fetch --depth 1 --prune origin +refs/heads/main:refs/heads/main" in trace
    )
    assert "git --git-dir" in trace and "archive --format=tar --output" in trace
    assert "uv sync --locked" in trace
    assert f"uv_cache {tmp_path / 'cache' / 'uv'}" in trace
    assert "uv run pyntara" in trace
    assert "uv run pyntara run" not in trace


def test_bootstrap_falls_back_to_local_branch_ref_when_origin_ref_is_missing(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_text = (repo_root / "i.sh").read_text(encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trace_path = tmp_path / "trace.log"
    source_tar = tmp_path / "source.tar"
    _build_source_tar(source_tar)

    _write_executable(
        fake_bin / "apt-get",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'apt-get %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
        ),
    )
    _write_executable(
        fake_bin / "uv",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'uv %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
        ),
    )
    _write_executable(
        fake_bin / "git",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'git %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
            "if [[ \"$1\" == clone ]]; then\n"
            "  cache_dir=\"${@: -1}\"\n"
            "  mkdir -p \"$cache_dir\"\n"
            "  exit 0\n"
            "fi\n"
            "if [[ \"$3\" == rev-parse ]]; then\n"
            "  ref=\"${@: -1}\"\n"
            "  if [[ \"$ref\" == origin/main ]]; then\n"
            "    exit 1\n"
            "  fi\n"
            "  if [[ \"$ref\" == main || \"$ref\" == FETCH_HEAD ]]; then\n"
            "    exit 0\n"
            "  fi\n"
            "  exit 1\n"
            "fi\n"
            "if [[ \"$3\" == archive ]]; then\n"
            "  ref=\"${@: -1}\"\n"
            "  if [[ \"$ref\" != main ]]; then\n"
            "    exit 1\n"
            "  fi\n"
            "  output=''\n"
            "  while (($#)); do\n"
            "    if [[ \"$1\" == --output ]]; then\n"
            "      output=\"$2\"\n"
            "      break\n"
            "    fi\n"
            "    shift\n"
            "  done\n"
            "  cp \"$PYNTARA_TEST_SOURCE_TAR\" \"$output\"\n"
            "  exit 0\n"
            "fi\n"
        ),
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["PYNTARA_ROOT_EUID"] = str(os.geteuid())
    env["PYNTARA_STATE_DIR"] = str(tmp_path / "state")
    env["PYNTARA_LOG_DIR"] = str(tmp_path / "logs")
    env["PYNTARA_WORK_BASE_DIR"] = str(tmp_path / "work")
    env["PYNTARA_REPO_CACHE_DIR"] = str(tmp_path / "cache" / "Pyntara.git")
    env["PYNTARA_UV_CACHE_DIR"] = str(tmp_path / "cache" / "uv")
    env["PYNTARA_TEST_TRACE"] = str(trace_path)
    env["PYNTARA_TEST_SOURCE_TAR"] = str(source_tar)

    completed = subprocess.run(
        ["bash"],
        input=script_text,
        text=True,
        cwd=repo_root,
        env=env,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    trace = trace_path.read_text(encoding="utf-8")
    assert "git --git-dir" in trace and "rev-parse --verify --quiet FETCH_HEAD" in trace
    assert "git --git-dir" in trace and "rev-parse --verify --quiet origin/main" in trace
    assert "git --git-dir" in trace and "rev-parse --verify --quiet main" in trace
    assert "git --git-dir" in trace and "archive --format=tar --output" in trace
    assert "archive --format=tar --output" in trace and " main" in trace


def test_bootstrap_falls_back_when_origin_ref_archive_fails(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_text = (repo_root / "i.sh").read_text(encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trace_path = tmp_path / "trace.log"
    source_tar = tmp_path / "source.tar"
    _build_source_tar(source_tar)

    _write_executable(
        fake_bin / "apt-get",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'apt-get %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
        ),
    )
    _write_executable(
        fake_bin / "uv",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'uv %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
        ),
    )
    _write_executable(
        fake_bin / "git",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'git %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
            "if [[ \"$1\" == clone ]]; then\n"
            "  cache_dir=\"${@: -1}\"\n"
            "  mkdir -p \"$cache_dir\"\n"
            "  exit 0\n"
            "fi\n"
            "if [[ \"$3\" == rev-parse ]]; then\n"
            "  ref=\"${@: -1}\"\n"
            "  if [[ \"$ref\" == origin/main ]] || [[ \"$ref\" == main ]]; then\n"
            "    exit 0\n"
            "  fi\n"
            "  if [[ \"$ref\" == FETCH_HEAD ]]; then\n"
            "    exit 1\n"
            "  fi\n"
            "  exit 1\n"
            "fi\n"
            "if [[ \"$3\" == archive ]]; then\n"
            "  ref=\"${@: -1}\"\n"
            "  if [[ \"$ref\" == origin/main ]]; then\n"
            "    exit 1\n"
            "  fi\n"
            "  if [[ \"$ref\" != main ]]; then\n"
            "    exit 1\n"
            "  fi\n"
            "  output=''\n"
            "  while (($#)); do\n"
            "    if [[ \"$1\" == --output ]]; then\n"
            "      output=\"$2\"\n"
            "      break\n"
            "    fi\n"
            "    shift\n"
            "  done\n"
            "  cp \"$PYNTARA_TEST_SOURCE_TAR\" \"$output\"\n"
            "  exit 0\n"
            "fi\n"
        ),
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["PYNTARA_ROOT_EUID"] = str(os.geteuid())
    env["PYNTARA_STATE_DIR"] = str(tmp_path / "state")
    env["PYNTARA_LOG_DIR"] = str(tmp_path / "logs")
    env["PYNTARA_WORK_BASE_DIR"] = str(tmp_path / "work")
    env["PYNTARA_REPO_CACHE_DIR"] = str(tmp_path / "cache" / "Pyntara.git")
    env["PYNTARA_UV_CACHE_DIR"] = str(tmp_path / "cache" / "uv")
    env["PYNTARA_TEST_TRACE"] = str(trace_path)
    env["PYNTARA_TEST_SOURCE_TAR"] = str(source_tar)

    completed = subprocess.run(
        ["bash"],
        input=script_text,
        text=True,
        cwd=repo_root,
        env=env,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    trace = trace_path.read_text(encoding="utf-8")
    assert (
        "archive --format=tar --output" in trace
        and " origin/main" in trace
        and " main" in trace
    )


def test_bootstrap_prefers_fetch_head_over_stale_main(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_text = (repo_root / "i.sh").read_text(encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trace_path = tmp_path / "trace.log"
    source_tar = tmp_path / "source.tar"
    _build_source_tar(source_tar)

    _write_executable(
        fake_bin / "apt-get",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'apt-get %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
        ),
    )
    _write_executable(
        fake_bin / "uv",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'uv %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
        ),
    )
    _write_executable(
        fake_bin / "git",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'git %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
            "if [[ \"$1\" == clone ]]; then\n"
            "  cache_dir=\"${@: -1}\"\n"
            "  mkdir -p \"$cache_dir\"\n"
            "  exit 0\n"
            "fi\n"
            "if [[ \"$3\" == rev-parse ]]; then\n"
            "  ref=\"${@: -1}\"\n"
            "  if [[ \"$ref\" == FETCH_HEAD ]] || [[ \"$ref\" == main ]] || \\\n"
            "     [[ \"$ref\" == origin/main ]]; then\n"
            "    exit 0\n"
            "  fi\n"
            "  exit 1\n"
            "fi\n"
            "if [[ \"$3\" == archive ]]; then\n"
            "  ref=\"${@: -1}\"\n"
            "  if [[ \"$ref\" != FETCH_HEAD ]]; then\n"
            "    exit 1\n"
            "  fi\n"
            "  output=''\n"
            "  while (($#)); do\n"
            "    if [[ \"$1\" == --output ]]; then\n"
            "      output=\"$2\"\n"
            "      break\n"
            "    fi\n"
            "    shift\n"
            "  done\n"
            "  cp \"$PYNTARA_TEST_SOURCE_TAR\" \"$output\"\n"
            "  exit 0\n"
            "fi\n"
        ),
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["PYNTARA_ROOT_EUID"] = str(os.geteuid())
    env["PYNTARA_STATE_DIR"] = str(tmp_path / "state")
    env["PYNTARA_LOG_DIR"] = str(tmp_path / "logs")
    env["PYNTARA_WORK_BASE_DIR"] = str(tmp_path / "work")
    env["PYNTARA_REPO_CACHE_DIR"] = str(tmp_path / "cache" / "Pyntara.git")
    env["PYNTARA_UV_CACHE_DIR"] = str(tmp_path / "cache" / "uv")
    env["PYNTARA_TEST_TRACE"] = str(trace_path)
    env["PYNTARA_TEST_SOURCE_TAR"] = str(source_tar)

    completed = subprocess.run(
        ["bash"],
        input=script_text,
        text=True,
        cwd=repo_root,
        env=env,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    trace = trace_path.read_text(encoding="utf-8")
    assert "archive --format=tar --output" in trace and " FETCH_HEAD" in trace


def test_bootstrap_uses_local_source_without_git(tmp_path: Path) -> None:
    script_src = Path(__file__).resolve().parents[1] / "i.sh"
    local_source = tmp_path / "flash-source"
    local_source.mkdir()
    script_path = local_source / "i.sh"
    script_path.write_text(script_src.read_text(encoding="utf-8"), encoding="utf-8")
    (local_source / "pyproject.toml").write_text(
        "[project]\nname='pyntara'\nversion='0.1.0'\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trace_path = tmp_path / "trace.log"

    _write_executable(
        fake_bin / "apt-get",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'apt-get %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
        ),
    )
    _write_executable(
        fake_bin / "uv",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'uv %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
        ),
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["PYNTARA_ROOT_EUID"] = str(os.geteuid())
    env["PYNTARA_STATE_DIR"] = str(tmp_path / "state")
    env["PYNTARA_LOG_DIR"] = str(tmp_path / "logs")
    env["PYNTARA_WORK_BASE_DIR"] = str(tmp_path / "work")
    env["PYNTARA_UV_CACHE_DIR"] = str(tmp_path / "cache" / "uv")
    env["PYNTARA_TEST_TRACE"] = str(trace_path)

    completed = subprocess.run(
        ["bash", str(script_path)],
        text=True,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    trace = trace_path.read_text(encoding="utf-8")
    assert "git " not in trace
    assert "uv sync --locked" in trace
    assert "uv run pyntara" in trace
    assert "uv run pyntara run" not in trace


def test_bootstrap_uses_unlocked_sync_when_lock_is_outdated(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_text = (repo_root / "i.sh").read_text(encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trace_path = tmp_path / "trace.log"
    source_tar = tmp_path / "source.tar"
    _build_source_tar(source_tar)

    _write_executable(
        fake_bin / "apt-get",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'apt-get %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
        ),
    )
    _write_executable(
        fake_bin / "uv",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'uv %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
            "if [[ \"$1\" == lock && \"$2\" == --check ]]; then\n"
            "  exit 1\n"
            "fi\n"
        ),
    )
    _write_executable(
        fake_bin / "git",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'git %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
            "if [[ \"$1\" == clone ]]; then\n"
            "  cache_dir=\"${@: -1}\"\n"
            "  mkdir -p \"$cache_dir\"\n"
            "  exit 0\n"
            "fi\n"
            "if [[ \"$3\" == rev-parse ]]; then\n"
            "  ref=\"${@: -1}\"\n"
            "  if [[ \"$ref\" == FETCH_HEAD ]] || [[ \"$ref\" == main ]] || \\\n"
            "     [[ \"$ref\" == origin/main ]]; then\n"
            "    exit 0\n"
            "  fi\n"
            "  exit 1\n"
            "fi\n"
            "if [[ \"$3\" == archive ]]; then\n"
            "  output=''\n"
            "  while (($#)); do\n"
            "    if [[ \"$1\" == --output ]]; then\n"
            "      output=\"$2\"\n"
            "      break\n"
            "    fi\n"
            "    shift\n"
            "  done\n"
            "  cp \"$PYNTARA_TEST_SOURCE_TAR\" \"$output\"\n"
            "  exit 0\n"
            "fi\n"
        ),
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["PYNTARA_ROOT_EUID"] = str(os.geteuid())
    env["PYNTARA_STATE_DIR"] = str(tmp_path / "state")
    env["PYNTARA_LOG_DIR"] = str(tmp_path / "logs")
    env["PYNTARA_WORK_BASE_DIR"] = str(tmp_path / "work")
    env["PYNTARA_REPO_CACHE_DIR"] = str(tmp_path / "cache" / "Pyntara.git")
    env["PYNTARA_UV_CACHE_DIR"] = str(tmp_path / "cache" / "uv")
    env["PYNTARA_TEST_TRACE"] = str(trace_path)
    env["PYNTARA_TEST_SOURCE_TAR"] = str(source_tar)

    completed = subprocess.run(
        ["bash"],
        input=script_text,
        text=True,
        cwd=repo_root,
        env=env,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    trace = trace_path.read_text(encoding="utf-8")
    assert "uv lock --check" in trace
    assert "uv sync --locked" not in trace
    assert "uv sync" in trace


def test_bootstrap_creates_global_uv_link_when_uv_exists_only_in_root_path(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_text = (repo_root / "i.sh").read_text(encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trace_path = tmp_path / "trace.log"
    source_tar = tmp_path / "source.tar"
    _build_source_tar(source_tar)

    root_uv_bin = tmp_path / "root-local" / "uv"
    root_uv_bin.parent.mkdir(parents=True)
    _write_executable(
        root_uv_bin,
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'uv %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
        ),
    )
    global_uv_bin = tmp_path / "usr-local" / "bin" / "uv"
    global_uv_bin.parent.mkdir(parents=True)

    _write_executable(
        fake_bin / "apt-get",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'apt-get %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
        ),
    )
    _write_executable(
        fake_bin / "git",
        (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'git %s\\n' \"$*\" >> \"$PYNTARA_TEST_TRACE\"\n"
            "if [[ \"$1\" == clone ]]; then\n"
            "  cache_dir=\"${@: -1}\"\n"
            "  mkdir -p \"$cache_dir\"\n"
            "  exit 0\n"
            "fi\n"
            "if [[ \"$3\" == rev-parse ]]; then\n"
            "  ref=\"${@: -1}\"\n"
            "  if [[ \"$ref\" == FETCH_HEAD ]] || [[ \"$ref\" == main ]] || \\\n"
            "     [[ \"$ref\" == origin/main ]]; then\n"
            "    exit 0\n"
            "  fi\n"
            "  exit 1\n"
            "fi\n"
            "if [[ \"$3\" == archive ]]; then\n"
            "  output=''\n"
            "  while (($#)); do\n"
            "    if [[ \"$1\" == --output ]]; then\n"
            "      output=\"$2\"\n"
            "      break\n"
            "    fi\n"
            "    shift\n"
            "  done\n"
            "  cp \"$PYNTARA_TEST_SOURCE_TAR\" \"$output\"\n"
            "  exit 0\n"
            "fi\n"
        ),
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{root_uv_bin.parent}:{env['PATH']}"
    env["PYNTARA_ROOT_EUID"] = str(os.geteuid())
    env["PYNTARA_STATE_DIR"] = str(tmp_path / "state")
    env["PYNTARA_LOG_DIR"] = str(tmp_path / "logs")
    env["PYNTARA_WORK_BASE_DIR"] = str(tmp_path / "work")
    env["PYNTARA_REPO_CACHE_DIR"] = str(tmp_path / "cache" / "Pyntara.git")
    env["PYNTARA_UV_CACHE_DIR"] = str(tmp_path / "cache" / "uv")
    env["PYNTARA_UV_ROOT_BIN"] = str(root_uv_bin)
    env["PYNTARA_UV_GLOBAL_BIN"] = str(global_uv_bin)
    env["PYNTARA_TEST_TRACE"] = str(trace_path)
    env["PYNTARA_TEST_SOURCE_TAR"] = str(source_tar)

    completed = subprocess.run(
        ["bash"],
        input=script_text,
        text=True,
        cwd=repo_root,
        env=env,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert global_uv_bin.is_symlink() is True
    assert global_uv_bin.resolve() == root_uv_bin.resolve()
