"""Colored, annotated view of a crontab."""

import re
from datetime import datetime
from typing import Optional

import click


_VAR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)")
_SPECIAL_RE = re.compile(r"^(@\w+)\s+(.*)")

_FIELD_NAMES = ["min", "hour", "dom", "mon", "dow"]

_DOW_NAMES = {
    "0": "Sun",
    "1": "Mon",
    "2": "Tue",
    "3": "Wed",
    "4": "Thu",
    "5": "Fri",
    "6": "Sat",
    "7": "Sun",
    "sun": "Sun",
    "mon": "Mon",
    "tue": "Tue",
    "wed": "Wed",
    "thu": "Thu",
    "fri": "Fri",
    "sat": "Sat",
}

_MON_NAMES = {
    "1": "Jan",
    "2": "Feb",
    "3": "Mar",
    "4": "Apr",
    "5": "May",
    "6": "Jun",
    "7": "Jul",
    "8": "Aug",
    "9": "Sep",
    "10": "Oct",
    "11": "Nov",
    "12": "Dec",
}


def _field_summary(field: str, kind: str) -> str:
    """Return a short human-readable description of one cron field."""
    if field == "*":
        return f"every {kind}"
    if field.startswith("*/"):
        n = field[2:]
        return f"every {n} {kind}s"
    if "-" in field and "/" not in field:
        return f"{kind} {field}"
    if "," in field:
        parts = field.split(",")
        if kind == "dow":
            parts = [_DOW_NAMES.get(p.lower(), p) for p in parts]
        elif kind == "mon":
            parts = [_MON_NAMES.get(p, p) for p in parts]
        return "/".join(parts)
    # Single value
    if kind == "dow":
        return _DOW_NAMES.get(field.lower(), field)
    if kind == "mon":
        return _MON_NAMES.get(field, field)
    return field


def _schedule_description(fields: list[str]) -> str:
    """Produce a compact English description of 5 cron fields."""
    mn, hr, dom, mon, dow = fields
    parts = []

    if hr == "*" and mn == "*":
        parts.append("every minute")
    elif hr.startswith("*/") and mn == "0":
        parts.append(f"every {hr[2:]}h")
    elif mn.startswith("*/") and hr == "*":
        parts.append(f"every {mn[2:]}min")
    elif mn == "0" and hr.isdigit():
        parts.append(f"at {int(hr):02d}:00")
    elif mn.isdigit() and hr.isdigit():
        parts.append(f"at {int(hr):02d}:{int(mn):02d}")
    else:
        if mn != "*":
            parts.append(f"min={mn}")
        if hr != "*":
            parts.append(f"hour={hr}")

    if dom != "*":
        parts.append(f"day {_field_summary(dom, 'dom')}")
    if mon != "*":
        parts.append(f"in {_field_summary(mon, 'mon')}")
    if dow != "*":
        parts.append(f"on {_field_summary(dow, 'dow')}")

    return ", ".join(parts) if parts else "continuously"


def _next_run(expr: str) -> Optional[str]:
    try:
        from croniter import croniter

        it = croniter(expr, datetime.now())
        nxt = it.get_next(datetime)
        delta = nxt - datetime.now()
        hours = int(delta.total_seconds() // 3600)
        mins = int((delta.total_seconds() % 3600) // 60)
        if hours > 48:
            days = hours // 24
            return f"next in ~{days}d"
        if hours > 0:
            return f"next in {hours}h{mins:02d}m"
        return f"next in {mins}m"
    except Exception:
        return None


def render(content: str) -> None:
    """Print a colorized, annotated crontab to stdout."""
    lines = content.splitlines()
    if not lines:
        click.echo("(empty)")
        return

    for line in lines:
        stripped = line.strip()

        # Blank line
        if not stripped:
            click.echo("")
            continue

        # Comment
        if stripped.startswith("#"):
            click.secho(line, dim=True)
            continue

        # Variable assignment
        m = _VAR_RE.match(stripped)
        if m:
            click.secho(m.group(1), fg="yellow", nl=False, bold=True)
            click.secho("=", fg="white", nl=False, dim=True)
            click.secho(m.group(2), fg="cyan")
            continue

        # @special
        m = _SPECIAL_RE.match(stripped)
        if m:
            special, cmd = m.group(1), m.group(2)
            click.secho(f"{special:<12}", fg="magenta", bold=True, nl=False)
            click.secho(" " + cmd, fg="white")
            continue

        # Standard 5-field entry
        parts = stripped.split(None, 5)
        if len(parts) >= 6:
            fields = parts[:5]
            cmd = parts[5]
            expr = " ".join(fields)
            desc = _schedule_description(fields)
            nxt = _next_run(expr)

            # Colour each time field
            field_colours = ["cyan", "blue", "green", "yellow", "magenta"]
            for i, (f, col) in enumerate(zip(fields, field_colours)):
                click.secho(f"{f:<6}", fg=col, nl=False, bold=True)
            click.secho(cmd, nl=False)

            # Annotation on the right
            annotation = f"  # {desc}"
            if nxt:
                annotation += f"  [{nxt}]"
            click.secho(annotation, dim=True)
            continue

        # Unrecognised — print as-is
        click.secho(line, fg="red")
