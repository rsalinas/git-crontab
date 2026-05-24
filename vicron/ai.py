import os
from typing import Optional


def generate_commit_message(diff: str) -> Optional[str]:
    """
    Call OpenAI gpt-4o-mini to produce a commit message from a crontab diff.
    Returns None if OPENAI_API_KEY is absent or the call fails.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Write a concise git commit message (max 72 chars, no prefix like 'feat:') "
                        "describing the change to this crontab file. "
                        "Focus on the scheduled tasks that were added, removed or modified.\n\n"
                        f"Diff:\n{diff[:3000]}"
                    ),
                }
            ],
            max_tokens=80,
            temperature=0.3,
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
