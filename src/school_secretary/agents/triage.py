from __future__ import annotations

from sqlalchemy.orm import Session

from school_secretary.agents.llm import complete
from school_secretary.db.models import Announcement

CATEGORIES = (
    "deadline_change",
    "new_assignment",
    "grades",
    "logistics",
    "content",
    "reminder",
    "other",
)

KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("deadline_change", ("extended", "deadline", "due date", "postponed", "moved to")),
    ("new_assignment", ("posted", "new assignment", "new lab", "now available", "dropbox")),
    ("grades", ("grade", "marks", "feedback released", "returned")),
    ("logistics", ("cancelled", "canceled", "room", "zoom", "guest lecture", "midterm", "exam", "office hour")),
    ("content", ("slides", "reading", "chapter", "notes posted")),
    ("reminder", ("reminder", "don't forget", "do not forget")),
]


def categorize_text(title: str, body: str) -> str:
    blob = f"{title}\n{body}".lower()
    for category, words in KEYWORDS:
        if any(word in blob for word in words):
            return category
    return "other"


def triage_announcement(announcement: Announcement) -> str:
    category = categorize_text(announcement.title, announcement.body)
    llm = complete(
        system="Classify a Brightspace announcement into one of: " + ", ".join(CATEGORIES) + ". Reply with the label only.",
        user=f"{announcement.title}\n{announcement.body[:1500]}",
    )
    if llm:
        label = llm.strip().split()[0].lower().replace(".", "")
        if label in CATEGORIES:
            category = label
    announcement.category = category
    announcement.triaged = True
    return category


def triage_pending(session: Session) -> list[Announcement]:
    pending = session.query(Announcement).filter_by(triaged=False).all()
    for announcement in pending:
        triage_announcement(announcement)
    return pending
