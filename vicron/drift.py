import difflib

import click

from .crontab import get_installed, hash_content, install
from .repo import (
    commit,
    get_merged_content,
    get_module_path,
    list_modules,
    load_state,
    save_state,
    split_merged,
)


def check_drift() -> tuple[bool, str]:
    """
    Return (has_drift, installed_content).
    No drift on first run (no saved state).
    """
    installed = get_installed()
    state = load_state()
    if state is None:
        return False, installed
    return hash_content(installed) != state["hash"], installed


def handle_drift(installed_content: str) -> bool:
    """
    Interactive reconciliation when drift is detected.
    Returns True to continue with the pending operation, False to abort.
    """
    merged = get_merged_content()

    click.echo()
    click.secho(
        "Warning: the installed crontab was modified outside vicron.",
        fg="yellow",
        bold=True,
    )
    click.echo()

    diff_lines = list(
        difflib.unified_diff(
            merged.splitlines(keepends=True),
            installed_content.splitlines(keepends=True),
            fromfile="repo (merged)",
            tofile="installed crontab",
        )
    )

    if diff_lines:
        _print_diff(diff_lines)
    else:
        click.echo("(no textual difference, but hash changed — likely whitespace)")

    click.echo()
    click.echo("  1  Import installed changes into repo")
    click.echo("  2  Overwrite installed with repo version")
    click.echo("  3  Abort")
    click.echo()

    choice = click.prompt("Choice", type=click.Choice(["1", "2", "3"]), default="3")

    if choice == "1":
        _import_installed(installed_content)
        return True
    elif choice == "2":
        install(merged)
        save_state(hash_content(merged))
        click.echo("Installed crontab overwritten with repo version.")
        return True
    else:
        click.echo("Aborted.")
        return False


def _import_installed(installed_content: str) -> None:
    """Split the installed crontab back into its module files and commit."""
    modules = split_merged(installed_content)
    for name in list_modules():
        if name not in modules:
            get_module_path(name).unlink()
            click.echo(f"Module '{name}' is gone from the installed crontab: removed.")
    for name, content in modules.items():
        get_module_path(name).write_text(content)

    committed = commit("drift: import manually-edited crontab")
    save_state(hash_content(installed_content))
    if committed:
        click.echo("Installed changes imported into repo and committed.")
    else:
        click.echo("Installed changes written to repo (no git changes detected).")

    if get_merged_content() != installed_content:
        click.secho(
            "Warning: re-merging the repo does not reproduce the installed crontab "
            "(module sections reordered?). Run 'vicron sync' to normalise.",
            fg="yellow",
        )


def _print_diff(lines: list[str], max_lines: int = 60) -> None:
    for line in lines[:max_lines]:
        line = line.rstrip("\n")
        if line.startswith("+"):
            click.secho(line, fg="green")
        elif line.startswith("-"):
            click.secho(line, fg="red")
        else:
            click.echo(line)
    if len(lines) > max_lines:
        click.echo(f"... ({len(lines) - max_lines} more lines omitted)")
