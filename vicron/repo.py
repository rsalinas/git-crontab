import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from .config import DEFAULT_MODULE, REPO_DIR, STATE_FILE


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


def is_initialised() -> bool:
    return (REPO_DIR / ".git").exists()


def ensure_repo() -> bool:
    """Create and initialise ~/.config/vicron/ git repo if it does not exist.

    Returns True if the repo was just initialised (first run).
    """
    REPO_DIR.mkdir(parents=True, exist_ok=True)

    if not (REPO_DIR / ".git").exists():
        _init_repo()
        return True
    return False


def _init_repo() -> None:
    from .crontab import get_installed, hash_content
    import click

    _run(["git", "init"])
    _run(["git", "config", "user.name", os.getenv("USER", "vicron")])
    _run(["git", "config", "user.email", f"{os.getenv('USER', 'vicron')}@localhost"])
    # The vicron data repo is local-only; no commit signing needed
    _run(["git", "config", "commit.gpgsign", "false"])

    (REPO_DIR / ".gitignore").write_text(".vicron_state\n*.swp\n*.swo\n*~\n")

    installed = get_installed()
    main_path = get_module_path(DEFAULT_MODULE)
    if installed.strip():
        main_path.write_text(installed)
        click.echo(f"Imported existing crontab into {main_path}")
    else:
        main_path.write_text("# vicron main module\n")

    _run(["git", "add", ".gitignore", str(main_path)])
    _run(["git", "commit", "-m", "Initial import"])

    save_state(hash_content(installed))
    click.echo(f"Initialised vicron repository at {REPO_DIR}")


# ---------------------------------------------------------------------------
# Module management
# ---------------------------------------------------------------------------


def get_module_path(name: str) -> Path:
    if not name.endswith(".cron"):
        name = f"{name}.cron"
    return REPO_DIR / name


def list_modules() -> list[str]:
    """Return module names (no extension), main first, rest alphabetical."""
    names = []
    main = get_module_path(DEFAULT_MODULE)
    if main.exists():
        names.append(DEFAULT_MODULE)
    for f in sorted(REPO_DIR.glob("*.cron")):
        if f.stem != DEFAULT_MODULE:
            names.append(f.stem)
    return names


def module_separator(name: str) -> str:
    return f"\n# --- vicron module: {name} ---\n"


_SEPARATOR_RE = re.compile(r"\n# --- vicron module: (\S+) ---\n")


def get_merged_content() -> str:
    """Concatenate all modules in order; add separator comments between them."""
    parts: list[str] = []
    for name in list_modules():
        content = get_module_path(name).read_text()
        if parts:
            parts.append(module_separator(name))
        parts.append(content)
    return "".join(parts)


def split_merged(content: str) -> dict[str, str]:
    """Inverse of get_merged_content(): map module name -> its own content.

    Text before the first separator belongs to the default module.
    """
    parts = _SEPARATOR_RE.split(content)
    modules = {DEFAULT_MODULE: parts[0]}
    for name, body in zip(parts[1::2], parts[2::2]):
        modules[name] = body
    return modules


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------


def get_dirty_files() -> str:
    """Return `git status --short` output, empty string if repo is clean."""
    return _run(["git", "status", "--short"], capture=True).strip()


def get_pending_diff() -> str:
    """Return unified diff of all uncommitted changes (staged + unstaged)."""
    _run(["git", "add", "-A"])
    return _run(["git", "diff", "--staged"], capture=True)


def reset_hard_to_head() -> None:
    _run(["git", "reset", "--hard", "HEAD"])


def stage_all() -> None:
    _run(["git", "add", "-A"])


def get_staged_diff() -> str:
    stage_all()
    return _run(["git", "diff", "--staged"], capture=True)


def commit(message: str) -> bool:
    """Stage everything and commit. Returns False if nothing to commit."""
    stage_all()
    result = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
            return False
        raise RuntimeError(f"git commit failed: {result.stderr.strip()}")
    return True


def get_log(n: int = 10) -> str:
    return _run(
        ["git", "log", f"--max-count={n}", "--oneline", "--decorate"], capture=True
    )


def has_parent_commit() -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD~1"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def get_last_commit_summary() -> str:
    return _run(["git", "log", "--max-count=1", "--oneline"], capture=True).strip()


def reset_hard_to_parent() -> None:
    _run(["git", "reset", "--hard", "HEAD~1"])


# ---------------------------------------------------------------------------
# State persistence (tracks hash of last installed crontab)
# ---------------------------------------------------------------------------


def save_state(crontab_hash: str) -> None:
    STATE_FILE.write_text(
        json.dumps(
            {
                "hash": crontab_hash,
                "updated_at": datetime.now().isoformat(),
            }
        )
    )


def load_state() -> dict | None:
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], capture: bool = False) -> str:
    result = subprocess.run(
        cmd,
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {result.stderr.strip()}")
    return result.stdout if capture else ""
