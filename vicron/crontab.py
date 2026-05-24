import hashlib
import re
import subprocess


def get_installed() -> str:
    """Return current installed crontab; empty string if none or crontab unavailable."""
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    except FileNotFoundError:
        return ""
    if result.returncode == 0:
        return result.stdout
    # Exit code 1 with "no crontab for user" is normal
    return ""


def install(content: str) -> None:
    try:
        proc = subprocess.run(
            ["crontab", "-"], input=content, capture_output=True, text=True
        )
    except FileNotFoundError:
        raise RuntimeError(
            "'crontab' command not found. "
            "Install cron (e.g. 'apt install cron') to enable system crontab support."
        )
    if proc.returncode != 0:
        raise RuntimeError(f"crontab install failed: {proc.stderr.strip()}")


def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


_SPECIAL_STRINGS = {
    "@reboot",
    "@yearly",
    "@annually",
    "@monthly",
    "@weekly",
    "@daily",
    "@hourly",
    "@midnight",
}

_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")


def validate(content: str) -> list[str]:
    """Return list of error messages; empty list means content is valid."""
    try:
        from croniter import croniter

        has_croniter = True
    except ImportError:
        has_croniter = False

    errors = []
    for lineno, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _VAR_RE.match(stripped):
            continue
        if stripped.startswith("@"):
            parts = stripped.split(None, 1)
            if parts[0].lower() not in _SPECIAL_STRINGS:
                errors.append(f"Line {lineno}: unknown special string '{parts[0]}'")
            elif len(parts) < 2:
                errors.append(f"Line {lineno}: '{parts[0]}' requires a command")
            continue
        parts = stripped.split(None, 5)
        if len(parts) < 6:
            errors.append(
                f"Line {lineno}: expected 5 time fields + command, "
                f"got {len(parts)} token(s): {stripped!r}"
            )
            continue
        if has_croniter:
            expr = " ".join(parts[:5])
            try:
                croniter(expr)
            except Exception as exc:
                errors.append(f"Line {lineno}: invalid expression '{expr}': {exc}")
    return errors
