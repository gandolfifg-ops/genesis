"""Prefer real onQ rows when fixture demo courses share the same SQLite file."""

from __future__ import annotations

from sqlalchemy.orm import Session

from school_secretary.db.models import Course

# Must match ingest.fixtures org unit ids (CISC 235 / 365 / PHIL 111 demo).
FIXTURE_ORG_UNIT_IDS = frozenset({"66123", "66124", "66125"})


def live_course_ids(session: Session) -> list[int] | None:
    """Primary keys of non-fixture courses, or None if the DB is fixtures-only."""
    live = [
        course.id
        for course in session.query(Course).all()
        if course.org_unit_id not in FIXTURE_ORG_UNIT_IDS
    ]
    return live or None


def course_by_code(session: Session, code: str) -> Course | None:
    """First course with this code. Live onQ can have duplicate codes (two org units)."""
    q = (code or "").strip()
    if not q:
        return None
    return (
        session.query(Course)
        .filter(Course.code.ilike(q))
        .order_by(Course.id.asc())
        .first()
    )
