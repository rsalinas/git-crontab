import os
from typing import Optional


def _extract_changes(diff: str) -> tuple[list[str], list[str]]:
    """Return (added_lines, removed_lines) from a unified diff, skipping headers and context."""
    added, removed = [], []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:].strip())
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:].strip())
    return added, removed


def generate_commit_message(diff: str) -> Optional[str]:
    """
    Call OpenAI gpt-4o-mini to produce a commit message from a crontab diff.
    Returns None if OPENAI_API_KEY is absent or the call fails.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    added, removed = _extract_changes(diff)

    # Skip AI for pure whitespace changes — the simple fallback is more accurate.
    if not any(added) and not any(removed):
        return None
    if all(line == "" for line in added + removed):
        return None

    sections = []
    if added:
        sections.append("ADDED:\n" + "\n".join(f"  {ln}" for ln in added if ln))
    if removed:
        sections.append("REMOVED:\n" + "\n".join(f"  {ln}" for ln in removed if ln))
    changes_text = "\n".join(sections) or "(only blank lines changed)"

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Write a concise git commit message (max 72 chars, imperative mood, "
                        "no conventional-commit prefix) for this crontab change.\n\n"
                        f"{changes_text[:3000]}"
                    ),
                }
            ],
            max_tokens=80,
            temperature=0.2,
        )
        msg = response.choices[0].message.content.strip().strip("'\"")
        return msg if msg else None
    except Exception:
        return None


def simple_commit_message(diff: str) -> str:
    """Generate a basic commit message from diff line counts."""
    added = sum(
        1
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1
        for line in diff.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    if added and removed:
        return f"Update crontab: +{added} -{removed} lines"
    if added:
        return f"Update crontab: add {added} line{'s' if added != 1 else ''}"
    if removed:
        return f"Update crontab: remove {removed} line{'s' if removed != 1 else ''}"
    return "Update crontab"
