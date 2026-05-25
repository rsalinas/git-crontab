import os
import re
import subprocess
import difflib
from pathlib import Path

import click

from .config import DEFAULT_MODULE, REPO_DIR
from .crontab import get_installed, hash_content, install as crontab_install, validate
from .repo import (
    commit,
    ensure_repo,
    get_dirty_files,
    get_last_commit_summary,
    get_log,
    get_merged_content,
    get_module_path,
    get_staged_diff,
    has_parent_commit,
    is_initialised,
    list_modules,
    reset_hard_to_head,
    reset_hard_to_parent,
    save_state,
)
from .drift import check_drift, handle_drift
from .ai import generate_commit_message, simple_commit_message
from .show import render as render_crontab
from .lint import lint_all
from .doctor import run_all as doctor_run_all
from .export import generate as export_generate


# ---------------------------------------------------------------------------
# Aliased group (supports short aliases like "e" → "edit")
# ---------------------------------------------------------------------------


class _AliasedGroup(click.Group):
    _aliases: dict[str, str] = {"e": "edit"}

    def get_command(self, ctx, cmd_name):
        cmd_name = self._aliases.get(cmd_name, cmd_name)
        return super().get_command(ctx, cmd_name)

    def resolve_command(self, ctx, args):
        cmd_name = self._aliases.get(args[0], args[0])
        args = [cmd_name] + list(args[1:])
        return super().resolve_command(ctx, args)


# ---------------------------------------------------------------------------
# Shell completion helpers
# ---------------------------------------------------------------------------


def _complete_modules(ctx, param, incomplete):
    from click.shell_completion import CompletionItem

    if not is_initialised():
        return []
    return [
        CompletionItem(name) for name in list_modules() if name.startswith(incomplete)
    ]


def _complete_removable_modules(ctx, param, incomplete):
    from click.shell_completion import CompletionItem

    if not is_initialised():
        return []
    return [
        CompletionItem(name)
        for name in list_modules()
        if name != DEFAULT_MODULE and name.startswith(incomplete)
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_editor() -> str:
    return os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"


def _check_clean_repo() -> bool:
    """Warn if the repo has leftover uncommitted changes. Returns False to abort."""
    dirty = get_dirty_files()
    if not dirty:
        return True
    click.echo()
    click.secho("Warning: the vicron repo has uncommitted changes:", fg="yellow")
    click.echo(dirty)
    click.echo()
    action = click.prompt(
        "  [r] reset and discard them   [c] continue (include them)   [a] abort",
        type=click.Choice(["r", "c", "a"]),
        default="r",
        show_choices=False,
    )
    if action == "a":
        return False
    if action == "r":
        reset_hard_to_head()
        click.secho("Reset — repo is clean.", fg="green")
    return True


def _edit_and_commit(module: str, show_diff: bool = False) -> None:
    if not _check_clean_repo():
        return

    path = get_module_path(module)
    content_before = path.read_text()

    editor = _get_editor()
    result = subprocess.run([editor, str(path)])
    if result.returncode != 0:
        raise click.ClickException(f"Editor exited with code {result.returncode}")

    if path.read_text() == content_before:
        click.secho("No changes — crontab unchanged.", fg="yellow")
        return

    merged = get_merged_content()
    errors = validate(merged)
    if errors:
        click.echo()
        click.secho("Validation errors:", fg="red", bold=True)
        for err in errors:
            click.echo(f"  {err}")
        if not click.confirm("\nInstall anyway?", default=False):
            _restore(path, content_before)
            return

    diff = get_staged_diff()
    if not diff.strip():
        click.secho("No changes — crontab unchanged.", fg="yellow")
        return

    if show_diff:
        click.echo()
        for line in diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                click.secho(line, fg="green")
            elif line.startswith("-") and not line.startswith("---"):
                click.secho(line, fg="red")
            else:
                click.echo(line)
        from .ai import _extract_changes

        added, removed = _extract_changes(diff)
        click.echo()
        click.secho("── AI input ──", dim=True)
        for ln in added:
            if ln:
                click.secho(f"  ADDED:   {ln}", fg="green")
        for ln in removed:
            if ln:
                click.secho(f"  REMOVED: {ln}", fg="red")
        if not any(added) and not any(removed):
            click.secho("  (only blank lines — AI will be skipped)", dim=True)
        click.echo()

    try:
        click.echo("Generating commit message...", nl=False)
        msg = generate_commit_message(diff)
        if msg:
            click.echo(f" {msg}")
            if not click.confirm("Use this message?", default=True):
                msg = click.prompt(
                    "Commit message", default=simple_commit_message(diff)
                )
        else:
            fallback = simple_commit_message(diff)
            click.echo()
            msg = click.prompt("Commit message", default=fallback)
    except click.Abort:
        click.echo()
        _restore(path, content_before)
        click.secho("Aborted — crontab restored to previous state.", fg="yellow")
        return

    commit(msg)
    crontab_install(merged)
    save_state(hash_content(merged))
    click.secho("Crontab updated and installed.", fg="green")


def _restore(path, content_before: str) -> None:
    path.write_text(content_before)
    subprocess.run(
        ["git", "reset", "HEAD", "--", str(path)],
        cwd=REPO_DIR,
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.group(
    cls=_AliasedGroup,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.pass_context
def main(ctx: click.Context) -> None:
    """vicron — versioned crontab manager."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(edit)


@main.command(name="init")
def init_cmd() -> None:
    """Initialise vicron repo and import current crontab (first-time setup)."""
    if is_initialised():
        click.secho("Already initialised.", fg="yellow")
        click.echo(get_log(5).rstrip())
        return

    ensure_repo()
    click.echo()
    click.echo("History:")
    click.echo(get_log(5).rstrip())


@main.command()
@click.argument("module", default=DEFAULT_MODULE, shell_complete=_complete_modules)
@click.option(
    "--diff",
    "show_diff",
    is_flag=True,
    help="Show diff before commit message generation.",
)
def edit(module: str, show_diff: bool) -> None:
    """Edit a crontab module (default: main)."""
    if ensure_repo():
        click.secho("Initialised vicron. Run again to start editing.", fg="green")
        return
    has_drift, installed_content = check_drift()
    if has_drift:
        if not handle_drift(installed_content):
            return

    path = get_module_path(module)
    if not path.exists():
        if not click.confirm(f"Module '{module}' does not exist. Create it?"):
            return
        path.write_text(f"# vicron module: {module}\n")

    _edit_and_commit(module, show_diff=show_diff)


@main.command()
def status() -> None:
    """Check coherence: drift, uncommitted changes, and repo/crontab sync."""
    ensure_repo()

    dirty = get_dirty_files()
    has_drift, _ = check_drift()
    all_ok = not has_drift and not dirty

    if has_drift:
        click.secho(
            "DRIFT    installed crontab was modified outside vicron", fg="yellow"
        )
    else:
        click.secho("OK       installed crontab matches repo", fg="green")

    if dirty:
        click.secho(
            "DIRTY    repo has uncommitted changes (leftover from a previous edit?)",
            fg="yellow",
        )
        click.echo(dirty)
    else:
        click.secho("OK       repo working tree is clean", fg="green")

    modules = list_modules()
    if modules:
        click.echo()
        click.echo("Modules:")
        for name in modules:
            p = get_module_path(name)
            active = [
                ln
                for ln in p.read_text().splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            click.echo(
                f"  {name}.cron  ({len(active)} active line{'s' if len(active) != 1 else ''})"
            )

    if all_ok:
        click.echo()
        click.secho("Everything is in sync.", fg="green")


@main.command(name="diff")
def diff_cmd() -> None:
    """Show diff between repo version and installed crontab."""
    ensure_repo()
    merged = get_merged_content()
    installed = get_installed()
    diff_lines = list(
        difflib.unified_diff(
            merged.splitlines(keepends=True),
            installed.splitlines(keepends=True),
            fromfile="repo (merged)",
            tofile="installed",
        )
    )
    if not diff_lines:
        click.echo("No differences.")
        return
    for line in diff_lines:
        line = line.rstrip("\n")
        if line.startswith("+"):
            click.secho(line, fg="green")
        elif line.startswith("-"):
            click.secho(line, fg="red")
        else:
            click.echo(line)


@main.command()
@click.option("-n", "--count", default=10, show_default=True, help="Number of commits.")
def log(count: int) -> None:
    """Show git log of crontab changes."""
    ensure_repo()
    output = get_log(count)
    click.echo(output.rstrip() if output.strip() else "(no commits yet)")


@main.command()
def modules() -> None:
    """List all crontab modules."""
    ensure_repo()
    names = list_modules()
    if not names:
        click.echo("No modules found.")
        return
    for name in names:
        p = get_module_path(name)
        active = [
            ln
            for ln in p.read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        marker = " (default)" if name == DEFAULT_MODULE else ""
        click.echo(
            f"  {name}.cron{marker}  — {len(active)} active line{'s' if len(active) != 1 else ''}"
        )


@main.command()
@click.argument("module")
def add(module: str) -> None:
    """Create and edit a new crontab module."""
    if ensure_repo():
        click.secho("Initialised vicron. Run again to start editing.", fg="green")
        return
    path = get_module_path(module)
    if path.exists():
        raise click.ClickException(
            f"Module '{module}' already exists. Use 'vicron edit {module}'."
        )
    path.write_text(f"# vicron module: {module}\n")
    click.echo(f"Created {path}")
    _edit_and_commit(module)


@main.command(name="rm")
@click.argument("module", shell_complete=_complete_removable_modules)
def rm_cmd(module: str) -> None:
    """Remove a crontab module."""
    ensure_repo()
    if module == DEFAULT_MODULE:
        raise click.ClickException("Cannot remove the main module.")
    path = get_module_path(module)
    if not path.exists():
        raise click.ClickException(f"Module '{module}' not found.")
    if not click.confirm(f"Remove module '{module}'?"):
        return
    path.unlink()
    merged = get_merged_content()
    commit(f"Remove module: {module}")
    crontab_install(merged)
    save_state(hash_content(merged))
    click.secho(f"Module '{module}' removed and crontab reinstalled.", fg="green")


@main.command()
def sync() -> None:
    """Install the merged repo crontab without editing."""
    if ensure_repo():
        click.secho("Initialised vicron. Run again to sync.", fg="green")
        return
    has_drift, installed_content = check_drift()
    if has_drift:
        if not handle_drift(installed_content):
            return
    merged = get_merged_content()
    errors = validate(merged)
    if errors:
        click.secho("Validation errors:", fg="red", bold=True)
        for err in errors:
            click.echo(f"  {err}")
        if not click.confirm("Install anyway?", default=False):
            return
    crontab_install(merged)
    save_state(hash_content(merged))
    click.secho("Crontab installed from repo.", fg="green")


@main.command()
def undo() -> None:
    """Undo the last commit and reinstall the previous crontab."""
    ensure_repo()
    if not has_parent_commit():
        raise click.ClickException("Nothing to undo — only one commit exists.")

    summary = get_last_commit_summary()
    click.echo(f"Will undo: {summary}")
    if not click.confirm("Proceed?", default=False):
        return

    reset_hard_to_parent()
    merged = get_merged_content()
    crontab_install(merged)
    save_state(hash_content(merged))
    click.secho("Last commit undone and crontab reinstalled.", fg="green")


@main.command()
@click.argument("module", default="", shell_complete=_complete_modules)
def show(module: str) -> None:
    """Show crontab with syntax highlighting and next-run times.

    Without arguments shows the fully merged crontab.
    Pass a module name to show only that module.
    """
    ensure_repo()
    if module:
        path = get_module_path(module)
        if not path.exists():
            raise click.ClickException(f"Module '{module}' not found.")
        content = path.read_text()
    else:
        content = get_merged_content()
    render_crontab(content)


# ---------------------------------------------------------------------------
# setup-completion
# ---------------------------------------------------------------------------

_SHELL_SNIPPETS = {
    "bash": 'eval "$(_VICRON_COMPLETE=bash_source vicron)"',
    "zsh": 'eval "$(_VICRON_COMPLETE=zsh_source vicron)"',
    "fish": "_VICRON_COMPLETE=fish_source vicron | source",
}

_SHELL_RC = {
    "bash": ["~/.bashrc", "~/.bash_profile"],
    "zsh": ["~/.zshrc"],
    "fish": ["~/.config/fish/config.fish"],
}


def _detect_shell() -> str | None:
    shell_bin = os.environ.get("SHELL", "")
    for name in ("bash", "zsh", "fish"):
        if name in shell_bin:
            return name
    return None


def _find_rc(shell: str) -> Path | None:
    for candidate in _SHELL_RC.get(shell, []):
        p = Path(candidate).expanduser()
        if p.exists():
            return p
    # Return the first candidate even if it doesn't exist yet
    candidates = _SHELL_RC.get(shell, [])
    return Path(candidates[0]).expanduser() if candidates else None


@main.command(name="setup-completion")
@click.option(
    "--shell",
    "shell_name",
    type=click.Choice(["bash", "zsh", "fish"]),
    default=None,
    help="Target shell (auto-detected from $SHELL if omitted).",
)
@click.option("--rc", "rc_path", default=None, help="RC file path (overrides default).")
@click.option(
    "--dry-run", is_flag=True, help="Print what would be done without writing."
)
def setup_completion(
    shell_name: str | None, rc_path: str | None, dry_run: bool
) -> None:
    """Configure tab completion for your shell.

    Auto-detects the shell from $SHELL. Writes the activation snippet
    into the appropriate RC file (~/.bashrc, ~/.zshrc, etc.) unless
    --rc overrides the path.
    """
    if shell_name is None:
        shell_name = _detect_shell()
        if shell_name is None:
            raise click.ClickException(
                "Could not detect shell from $SHELL. Use --shell bash|zsh|fish."
            )
        click.echo(f"Detected shell: {shell_name}")

    snippet = _SHELL_SNIPPETS[shell_name]

    if rc_path:
        rc = Path(rc_path).expanduser()
    else:
        rc = _find_rc(shell_name)
        if rc is None:
            raise click.ClickException(
                f"No RC file found for {shell_name}. Use --rc to specify one."
            )

    if rc.exists() and snippet in rc.read_text():
        click.secho(f"Already configured in {rc}", fg="yellow")
        return

    if dry_run:
        click.echo(f"Would append to {rc}:")
        click.secho(f"  {snippet}", fg="cyan")
        return

    with rc.open("a") as f:
        f.write(f"\n# vicron tab completion\n{snippet}\n")

    click.secho(f"Added to {rc}", fg="green")
    click.echo(f"  {snippet}")
    click.echo()
    click.secho("Reload your shell or run:", bold=True)
    click.echo(f"  source {rc}")


# ---------------------------------------------------------------------------
# split
# ---------------------------------------------------------------------------

_SPLIT_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")


def _parse_job_blocks(text: str) -> list[dict]:
    """
    Split module text into blocks, each being a dict:
      {'lines': [str, ...], 'job': str | None}
    Cron job lines carry their preceding comments in the same block.
    Variable assignments and blank lines are standalone blocks (job=None).
    """
    blocks: list[dict] = []
    pending: list[str] = []

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            if pending:
                blocks.append({"lines": pending, "job": None})
                pending = []
            blocks.append({"lines": [raw], "job": None})
        elif stripped.startswith("#"):
            pending.append(raw)
        elif _SPLIT_VAR_RE.match(stripped):
            if pending:
                blocks.append({"lines": pending, "job": None})
                pending = []
            blocks.append({"lines": [raw], "job": None})
        else:
            blocks.append({"lines": pending + [raw], "job": raw})
            pending = []

    if pending:
        blocks.append({"lines": pending, "job": None})

    return blocks


@main.command()
@click.argument("module", default=DEFAULT_MODULE, shell_complete=_complete_modules)
def split(module: str) -> None:
    """Interactively move cron jobs from a module into other modules."""
    if ensure_repo():
        click.secho("Initialised vicron. Run again to start editing.", fg="green")
        return

    path = get_module_path(module)
    if not path.exists():
        raise click.ClickException(f"Module '{module}' not found.")

    blocks = _parse_job_blocks(path.read_text())
    job_blocks = [(i, b) for i, b in enumerate(blocks) if b["job"] is not None]

    if not job_blocks:
        click.echo("No active jobs to split.")
        return

    n = len(job_blocks)
    known_modules = list(list_modules())
    decisions: dict[int, str] = {}  # block_index → destination module

    click.echo()
    click.secho(
        f"Splitting '{module}'  ({n} active job{'s' if n != 1 else ''})",
        bold=True,
    )
    click.echo("Press Enter to keep, or type a module name (existing or new).")
    click.echo()

    for seq, (block_i, block) in enumerate(job_blocks, 1):
        click.secho(f"[{seq}/{n}] ", fg="cyan", bold=True, nl=False)
        click.echo(block["job"].strip())
        for line in block["lines"][:-1]:
            click.secho(f"       {line.strip()}", dim=True)

        others = [m for m in known_modules if m != module]
        hint = "  ".join(others) if others else "type a new module name"
        dest = click.prompt(f"  → [{hint}]", default="", show_default=False)
        dest = dest.strip()

        if not dest:
            decisions[block_i] = module
        else:
            if dest not in known_modules:
                dest_path = get_module_path(dest)
                if not dest_path.exists():
                    dest_path.write_text(f"# vicron module: {dest}\n")
                    click.secho(f"     Created module '{dest}'", fg="green")
                known_modules.append(dest)
            decisions[block_i] = dest

        click.echo()

    moves = {bi: d for bi, d in decisions.items() if d != module}
    if not moves:
        click.secho("Nothing moved.", fg="yellow")
        return

    # Rebuild source module keeping only non-moved blocks
    kept: list[str] = []
    for i, block in enumerate(blocks):
        if i not in moves:
            kept.extend(block["lines"])
    path.write_text("\n".join(kept).rstrip("\n") + "\n")

    # Append moved blocks to destination modules
    dest_lines: dict[str, list[str]] = {}
    for bi, dest in moves.items():
        dest_lines.setdefault(dest, []).extend(blocks[bi]["lines"])

    for dest_mod, lines in dest_lines.items():
        dest_path = get_module_path(dest_mod)
        current = dest_path.read_text().rstrip("\n")
        dest_path.write_text(current + "\n\n" + "\n".join(lines) + "\n")

    # Summary
    dest_counts: dict[str, int] = {}
    for d in moves.values():
        dest_counts[d] = dest_counts.get(d, 0) + 1

    click.echo()
    click.secho("Moved:", bold=True)
    for dest_mod, count in dest_counts.items():
        click.echo(f"  {count} job{'s' if count != 1 else ''} → {dest_mod}")

    merged = get_merged_content()
    errors = validate(merged)
    if errors:
        click.secho("Validation errors:", fg="red", bold=True)
        for err in errors:
            click.echo(f"  {err}")
        if not click.confirm("Commit anyway?", default=False):
            return

    moves_str = ", ".join(f"{c}j→{d}" for d, c in dest_counts.items())
    commit(f"split {module}: {moves_str}")
    crontab_install(merged)
    save_state(hash_content(merged))
    click.secho("Done.", fg="green")


# ---------------------------------------------------------------------------
# git passthrough
# ---------------------------------------------------------------------------


@main.command(
    name="git",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def git_cmd(args: tuple) -> None:
    """Run any git command against the vicron repo.

    Example: vicron git log, vicron git diff HEAD~1
    """
    result = subprocess.run(["git", "-C", str(REPO_DIR), *args])
    raise SystemExit(result.returncode)


# ---------------------------------------------------------------------------
# lint
# ---------------------------------------------------------------------------

_LEVEL_COLOR = {"error": "red", "warning": "yellow", "info": "cyan"}
_LEVEL_LABEL = {"error": "ERROR  ", "warning": "WARN   ", "info": "INFO   "}


def _print_issues(issues: list) -> int:
    if not issues:
        click.secho("No issues found.", fg="green")
        return 0
    errors = 0
    for issue in issues:
        col = _LEVEL_COLOR[issue.level]
        label = _LEVEL_LABEL[issue.level]
        click.secho(label, fg=col, bold=True, nl=False)
        click.echo(str(issue))
        if issue.level == "error":
            errors += 1
    return errors


@main.command()
def lint() -> None:
    """Static analysis: style issues, variable conflicts, duplicates."""
    ensure_repo()
    issues = lint_all()
    _print_issues(issues)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@main.command()
def doctor() -> None:
    """Full health check: drift, syntax, missing executables, lint."""
    ensure_repo()
    issues = doctor_run_all()
    errors = _print_issues(issues)
    click.echo()
    if errors:
        click.secho(
            f"Found {errors} error(s). Crontab may not work correctly.", fg="red"
        )
    else:
        click.secho("All checks passed.", fg="green")


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--output-dir",
    "-o",
    default="./vicron-systemd",
    show_default=True,
    help="Directory to write .service and .timer files into.",
)
def export(output_dir: str) -> None:
    """Export crontab jobs as systemd .timer + .service unit files."""
    ensure_repo()
    out = Path(output_dir)
    units = export_generate(output_dir=out)

    if not units:
        click.echo("No active jobs to export.")
        return

    click.echo(f"Wrote {len(units)} unit pair(s) to {out}/\n")
    for u in units:
        if u.reboot:
            click.secho(f"  {u.name}.service", fg="cyan", nl=False)
            click.secho("  (@reboot — no timer, install as service)", dim=True)
        else:
            click.secho(f"  {u.name}.service + {u.name}.timer", fg="green")

    click.echo()
    click.secho("To install (as user):", bold=True)
    click.echo(f"  systemctl --user enable --now {out}/*.timer")
    click.echo()
    click.secho("Or system-wide (as root):", bold=True)
    click.echo(f"  cp {out}/* /etc/systemd/system/")
    click.echo("  systemctl daemon-reload")
    click.echo("  systemctl enable --now vicron-*.timer")
