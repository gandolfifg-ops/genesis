from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from school_secretary.config import Settings, get_settings
from school_secretary.db.models import Course
from school_secretary.db.session import init_db, session_scope
from school_secretary.ingest.brightspace import ingest_payloads
from school_secretary.rag.extract import write_pdf

TZ = ZoneInfo("America/Toronto")

# Demo "today" is aligned with the seeded announcements (mid-September 2026).
DEMO_NOW = datetime(2026, 9, 14, 19, 0, tzinfo=TZ)

CISC235 = "66123"
CISC365 = "66124"
PHIL111 = "66125"


def _pdfs(settings: Settings) -> dict[str, Path]:
    pdf_dir = settings.data_dir / "generated_pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    files["cisc235-syllabus.pdf"] = write_pdf(
        pdf_dir / "cisc235-syllabus.pdf",
        "CISC 235 — Data Structures  Fall 2026 Syllabus",
        [
            "Queen's University, School of Computing. Instructor: Prof. A. Turing. "
            "Office: Goodwin Hall 532. Office hours: Tuesdays 14:00–16:00.",
            "Late Policy. Assignments and labs submitted after the due date are subject to a "
            "late penalty of 10% of the earned grade per 24-hour period, for a maximum of 3 days (30%). "
            "Work submitted more than 3 days late receives a grade of zero. Extensions require "
            "academic consideration through the Faculty of Arts and Science.",
            "Labs are due Fridays at 18:00 Kingston time unless an announcement says otherwise. "
            "You must implement solutions yourself. Using generated code as your submission is an "
            "academic integrity offence.",
            "Required text: Goodrich, Tamassia, Goldwasser — Data Structures and Algorithms in Python.",
        ],
    )
    files["cisc235-lab2.pdf"] = write_pdf(
        pdf_dir / "cisc235-lab2.pdf",
        "CISC 235 Lab 2 — Binary Search Trees",
        [
            "Due: Friday, October 3, 2026 at 11:59 PM (Kingston). Submit via the onQ dropbox.",
            "Implement a binary search tree supporting insert, search, and delete. Handle an empty tree, "
            "duplicate keys (reject or count — document your choice), and deletion of a node with two children.",
            "You must write the algorithms yourself. Starter repositories may contain method stubs and "
            "test shells only. Do not submit unmodified generated implementations.",
            "Deliverables: bst.py, test_bst.py, and a short README describing complexity of each operation.",
        ],
    )
    files["cisc235-lab2-rubric.pdf"] = write_pdf(
        pdf_dir / "cisc235-lab2-rubric.pdf",
        "CISC 235 Lab 2 Rubric",
        [
            "Correctness of insert, search, and delete: 40%.",
            "Unit tests covering empty tree, duplicates, and two-child delete: 20%.",
            "Style and documentation (PEP 8, docstrings, complexity notes): 20%.",
            "Write-up / README: 20%.",
        ],
    )
    files["cisc365-syllabus.pdf"] = write_pdf(
        pdf_dir / "cisc365-syllabus.pdf",
        "CISC 365 — Algorithms I  Fall 2026 Syllabus",
        [
            "Queen's University, School of Computing. Instructor: Prof. K. Dijkstra. "
            "Office hours: Wednesdays 10:00–12:00, Goodwin 625.",
            "Late Policy. Late submissions lose 5% per day for at most 2 days. After 48 hours the "
            "dropbox closes and the grade is zero. This is stricter than CISC 235.",
            "Midterm: Thursday, October 16, 2026, in class. No aids except a handwritten one-page sheet.",
        ],
    )
    files["cisc365-a1.pdf"] = write_pdf(
        pdf_dir / "cisc365-a1.pdf",
        "CISC 365 Assignment 1 — Dynamic Programming",
        [
            "Due: Sunday, September 28, 2026 at 11:59 PM. Implement the recurrence described in the handout "
            "for the assigned optimization problem. Prove correctness in a short write-up.",
            "Starter code may include function stubs and skipped tests. Filling those in with a model's "
            "finished algorithm is not permitted.",
        ],
    )
    files["phil111-syllabus.pdf"] = write_pdf(
        pdf_dir / "phil111-syllabus.pdf",
        "PHIL 111 — What is Philosophy?  Fall 2026 Syllabus",
        [
            "Queen's University, Department of Philosophy. Instructor: Prof. M. Nussbaum. "
            "Office hours: Thursdays 13:00–15:00, Watson Hall 312.",
            "Late Policy. Essays are not accepted late without documented academic consideration. "
            "Plan backwards from the deadline; the secretary will only help you outline, not write.",
            "Essay 1 is 1500 words on a topic from Mill's Utilitarianism.",
        ],
    )
    files["phil111-essay-rubric.pdf"] = write_pdf(
        pdf_dir / "phil111-essay-rubric.pdf",
        "PHIL 111 Essay 1 Rubric (1500 words)",
        [
            "Thesis: 20%. A specific, contestable claim about Mill.",
            "Argument: 40%. Premises support the thesis; objections are considered.",
            "Sources: 20%. At least two course readings, properly cited (Chicago notes).",
            "Style: 20%. Clear prose, within 1500 words plus or minus 10%.",
        ],
    )
    return files


def enrollments() -> list[dict]:
    return [
        {
            "OrgUnit": {
                "Id": int(CISC235),
                "Name": "CISC 235 Data Structures",
                "Code": "CISC235",
                "Type": {"Id": 3, "Code": "Course Offering"},
            }
        },
        {
            "OrgUnit": {
                "Id": int(CISC365),
                "Name": "CISC 365 Algorithms I",
                "Code": "CISC365",
                "Type": {"Id": 3, "Code": "Course Offering"},
            }
        },
        {
            "OrgUnit": {
                "Id": int(PHIL111),
                "Name": "PHIL 111 What is Philosophy?",
                "Code": "PHIL111",
                "Type": {"Id": 3, "Code": "Course Offering"},
            }
        },
    ]


def news_by_org() -> dict[str, list[dict]]:
    return {
        CISC235: [
            {
                "Id": 9001,
                "Title": "Lab 1 deadline extended to Friday 6pm",
                "Body": {
                    "Text": "The Lab 1 dropbox deadline has been extended to Friday at 6:00 PM Kingston time.",
                    "Html": "<p>The Lab 1 dropbox <strong>deadline has been extended</strong> to Friday at 6:00 PM.</p>",
                },
                "StartDate": "2026-09-13T18:00:00.000Z",
                "Attachments": [],
            }
        ],
        CISC365: [
            {
                "Id": 9101,
                "Title": "Midterm date confirmed: 16 October",
                "Body": {
                    "Text": "The midterm is Thursday, October 16, 2026, in class. One handwritten sheet is allowed.",
                    "Html": "<p>The <em>midterm</em> is Thursday, October 16, 2026, in class.</p>",
                },
                "StartDate": "2026-09-12T15:30:00.000Z",
                "Attachments": [],
            }
        ],
        PHIL111: [
            {
                "Id": 9201,
                "Title": "Guest lecture Thursday — utilitarianism",
                "Body": {
                    "Text": "Guest lecture this Thursday in our usual room. Reading: Mill, chapter 2.",
                    "Html": "<p>Guest lecture this Thursday. Reading: Mill, chapter 2.</p>",
                },
                "StartDate": "2026-09-14T12:00:00.000Z",
                "Attachments": [],
            }
        ],
    }


def dropbox_by_org() -> dict[str, list[dict]]:
    return {
        CISC235: [
            {
                "Id": 7001,
                "Name": "Lab 2 — Binary Search Trees",
                "DueDate": "2026-10-03T23:59:00.000Z",
                "Instructions": {
                    "Text": "Implement a binary search tree. See the attached lab PDF and rubric. "
                    "Due: Friday, October 3, 2026 at 11:59 PM. Write unit tests.",
                    "Html": "<p>Implement a binary search tree. See <a href=\"cisc235-lab2.pdf\">lab PDF</a>.</p>",
                },
                "Attachments": [
                    {"FileId": 1, "FileName": "cisc235-lab2.pdf"},
                    {"FileId": 2, "FileName": "cisc235-lab2-rubric.pdf"},
                ],
            }
        ],
        CISC365: [
            {
                "Id": 7101,
                "Name": "Assignment 1 — Dynamic Programming",
                "DueDate": "2026-09-28T23:59:00.000Z",
                "Instructions": {
                    "Text": "Implement the DP recurrence from the handout. Coding lab with unit tests.",
                    "Html": "<p>Implement the DP recurrence. <a href=\"cisc365-a1.pdf\">Assignment PDF</a></p>",
                },
                "Attachments": [{"FileId": 3, "FileName": "cisc365-a1.pdf"}],
            }
        ],
        PHIL111: [
            {
                "Id": 7201,
                "Name": "Essay 1 — Mill and utilitarianism (1500 words)",
                "DueDate": "2026-10-10T21:00:00.000Z",
                "Instructions": {
                    "Text": "Write a 1500 word essay on a contestable claim about Mill's Utilitarianism. See rubric.",
                    "Html": "<p>Write a 1500 word essay. <a href=\"phil111-essay-rubric.pdf\">Rubric</a></p>",
                },
                "Attachments": [{"FileId": 4, "FileName": "phil111-essay-rubric.pdf"}],
            }
        ],
    }


def extra_syllabi() -> dict[str, str]:
    return {
        CISC235: "cisc235-syllabus.pdf",
        CISC365: "cisc365-syllabus.pdf",
        PHIL111: "phil111-syllabus.pdf",
    }


def seed_habits(settings: Settings) -> int:
    from school_secretary.agents.memory import record_study_session
    from school_secretary.db.models import StudyHabitEvent

    with session_scope(settings) as session:
        if session.query(StudyHabitEvent).count():
            return 0

    seeds = [
        ("CISC 235", 90, "Read BST chapter", DEMO_NOW - timedelta(days=2, hours=2)),
        ("CISC 365", 60, "DP practice problems", DEMO_NOW - timedelta(days=1, hours=4)),
        ("PHIL 111", 45, "Mill chapter 2", DEMO_NOW - timedelta(days=3)),
        ("CISC 235", 40, "Worked insert examples on paper", DEMO_NOW - timedelta(hours=26)),
    ]
    count = 0
    with session_scope(settings) as session:
        for code, minutes, notes, when in seeds:
            course = session.query(Course).filter(Course.code == code).one()
            record_study_session(session, minutes=minutes, notes=notes, course=course, when=when)
            count += 1
    return count


def ingest_fixtures(settings: Settings | None = None) -> dict[str, int]:
    settings = settings or get_settings()
    settings.ensure_dirs()
    init_db(settings)
    pdfs = _pdfs(settings)
    extra = extra_syllabi()
    # Copy syllabi into the attachment map under stable names
    attachment_files = dict(pdfs)
    counts = ingest_payloads(
        settings,
        enrollments(),
        news_by_org(),
        dropbox_by_org(),
        attachment_files=attachment_files,
        term="Fall 2026",
    )
    from school_secretary.ingest.brightspace import upsert_document
    from school_secretary.db.models import Course

    with session_scope(settings) as session:
        for org_id, filename in extra.items():
            course = session.query(Course).filter_by(org_unit_id=org_id).one()
            source = pdfs[filename]
            dest = settings.raw_dir / org_id / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source.read_bytes())
            upsert_document(session, course, dest, filename, doc_type="syllabus")
            counts["documents"] += 1
    counts["habits"] = seed_habits(settings)
    return counts
