from __future__ import annotations

import re

SOURCE_RE = re.compile(r"\(Source:\s*[^)]+\)", re.IGNORECASE)
RELATED_RE = re.compile(r"^Related \([^)]+\):\s*", re.IGNORECASE | re.MULTILINE)
ABS_PATH_RE = re.compile(
    r"(?:(?:file://)?(?:/workspace|/home|/Users|/mnt)/[^\s)\]>]+)",
)
ICS_PATH_RE = re.compile(r"\S*school-secretary\.ics", re.IGNORECASE)
WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s)\]>]+")


def polish_outgoing(text: str) -> str:
    """Executive-assistant copy: no raw source filenames or local filesystem paths."""
    cleaned = SOURCE_RE.sub("", text or "")
    cleaned = RELATED_RE.sub("", cleaned)
    cleaned = ICS_PATH_RE.sub("your calendar file", cleaned)
    cleaned = ABS_PATH_RE.sub("local file", cleaned)
    cleaned = WINDOWS_PATH_RE.sub("local file", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
