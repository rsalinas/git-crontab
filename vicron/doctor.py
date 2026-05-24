"""Runtime health checks for vicron doctor."""

import re
import shutil
import subprocess
from pathlib import Path

from .config import REPO_DIR
from .crontab import validate
from .drift import check_drift
from .lint import Issue, lint_all
from .repo import get_merged_content, get_module_path, list_modules


_VAR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)")
_SPECIAL_RE = re.compile(r"^(@\w+)")
_ENV_RE = re.compile(r"^/usr/bin/env\s+(\S+)")


def _resolve_exe(cmd: str) -> str | None:
    """Return the executable to check, or None if it can't be determined."""
    first = cmd.split()[0] if cmd.split() else None
    if not first:
        return None
    # Handle /usr/bin/env <prog> ...
    m = _ENV_RE.match(cmd)
    if m:
        return m.group(1)  # relative name, checked via PATH
    return first


def check_executables() -> list[Issue]:
    """Check that every executable referenced in cron jobs exists."""
    issues: list[Issue] = []
    for module_name in list_modules():
        content = get_module_path(module_name).read_text()
        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if _VAR_RE.match(stripped):
                continue

            if _SPECIAL_RE.match(stripped):
                parts = stripped.split(None, 1)
                cmd = parts[1] if len(parts) > 1 else ""
            else:
                parts = stripped.split(None, 5)
                if len(parts) < 6:
                    continue
                cmd = parts[5]

            exe = _resolve_exe(cmd)
            if not exe:
                continue

            if exe.startswith("/"):
                if not Path(exe).exists():
                    issues.append(
                        Issue(
                            "error",
                            module_name,
                            lineno,
                            f"executable not found: {exe}",
                        )
                    )
                elif not Path(exe).stat().st_mode & 0o111:
                    issues.append(
                        Issue(
                            "warning",
                            module_name,
                            lineno,
                            f"file exists but is not executable: {exe}",
                        )
                    )
            else:
                if not shutil.which(exe):
                    issues.append(
                        Issue(
                            "warning",
                            module_name,
                            lineno,
                            f"'{exe}' not found in PATH",
                        )
                    )

    return issues


def check_syntax() -> list[Issue]:
    """Validate cron syntax of the merged crontab."""
    merged = get_merged_content()
    errors = validate(merged)
    return [Issue("error", None, None, e) for e in errors]


def check_git_clean() -> list[Issue]:
    """Warn if there are uncommitted changes in the vicron repo."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        return [
            Issue(
                "warning",
                None,
                None,
                "uncommitted changes in vicron repo (run 'vicron edit' or commit manually)",
            )
        ]
    return []


def check_drift_status() -> list[Issue]:
    """Report if installed crontab drifted from repo."""
    has_drift, _ = check_drift()
    if has_drift:
        return [
            Issue(
                "error",
                None,
                None,
                "installed crontab differs from repo — run 'vicron status' to reconcile",
            )
        ]
    return []


def run_all() -> list[Issue]:
    issues: list[Issue] = []
    issues.extend(check_drift_status())
    issues.extend(check_syntax())
    issues.extend(check_executables())
    issues.extend(check_git_clean())
    issues.extend(lint_all())
    return issues
