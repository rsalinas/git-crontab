"""Static analysis of crontab modules."""

import re
from dataclasses import dataclass
from typing import Optional

from .repo import get_module_path, list_modules


@dataclass
class Issue:
    level: str  # "error" | "warning" | "info"
    module: Optional[str]
    lineno: Optional[int]
    message: str

    def __str__(self) -> str:
        loc = ""
        if self.module:
            loc = f"{self.module}.cron"
            if self.lineno:
                loc += f":{self.lineno}"
            loc += "  "
        return f"{loc}{self.message}"


_VAR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)")
_SPECIAL_RE = re.compile(r"^(@\w+)")
_UNESCAPED_PCT = re.compile(r"(?<!\\)%")
_SHELL_META = re.compile(r"[;&|<>`$()]")


def _check_module(module_name: str) -> list[Issue]:
    """Per-module static checks."""
    content = get_module_path(module_name).read_text()
    issues: list[Issue] = []

    for lineno, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _VAR_RE.match(stripped):
            continue
        if _SPECIAL_RE.match(stripped):
            continue

        parts = stripped.split(None, 5)
        if len(parts) < 6:
            continue  # caught by validate()

        cmd = parts[5]

        # Unescaped % — cron interprets as newline (stdin separator)
        if _UNESCAPED_PCT.search(cmd):
            issues.append(
                Issue(
                    "warning",
                    module_name,
                    lineno,
                    f"unescaped '%' — cron passes it as a newline/stdin separator: {cmd!r}",
                )
            )

        # Relative path for executable
        exe = cmd.split()[0]
        if exe and not exe.startswith("/") and not exe.startswith("$"):
            issues.append(
                Issue(
                    "warning",
                    module_name,
                    lineno,
                    f"relative executable path '{exe}' — prefer absolute path",
                )
            )

        # Very high frequency (every minute) — just informational
        mn, hr = parts[0], parts[1]
        if mn == "*" and hr == "*":
            issues.append(
                Issue(
                    "info",
                    module_name,
                    lineno,
                    "job runs every minute — intentional?",
                )
            )

    # Missing trailing newline
    if content and not content.endswith("\n"):
        issues.append(
            Issue(
                "warning",
                module_name,
                None,
                "file does not end with a newline (some cron daemons silently skip the last line)",
            )
        )

    return issues


def _check_cross_module() -> list[Issue]:
    """Checks that require looking across all modules simultaneously."""
    issues: list[Issue] = []

    # variable name → [(module, lineno, value)]
    var_defs: dict[str, list[tuple[str, int, str]]] = {}
    # (schedule, command) → [(module, lineno)]
    job_defs: dict[tuple[str, str], list[tuple[str, int]]] = {}

    for module_name in list_modules():
        content = get_module_path(module_name).read_text()
        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            m = _VAR_RE.match(stripped)
            if m:
                var, val = m.group(1), m.group(2).strip()
                var_defs.setdefault(var, []).append((module_name, lineno, val))
                continue

            parts = stripped.split(None, 5)
            if len(parts) >= 6:
                key = (" ".join(parts[:5]), parts[5])
                job_defs.setdefault(key, []).append((module_name, lineno))

    for var, defs in var_defs.items():
        if len(defs) > 1:
            locs = ", ".join(f"{m}.cron:{lineno} (={v!r})" for m, lineno, v in defs)
            issues.append(
                Issue(
                    "warning",
                    None,
                    None,
                    f"variable '{var}' redefined across modules: {locs}",
                )
            )

    for (sched, cmd), occurrences in job_defs.items():
        if len(occurrences) > 1:
            locs = ", ".join(f"{m}.cron:{lineno}" for m, lineno in occurrences)
            issues.append(
                Issue(
                    "warning",
                    None,
                    None,
                    f"duplicate job '{cmd}' at [{sched}] in: {locs}",
                )
            )

    return issues


def lint_all() -> list[Issue]:
    issues: list[Issue] = []
    for name in list_modules():
        issues.extend(_check_module(name))
    issues.extend(_check_cross_module())
    return issues
