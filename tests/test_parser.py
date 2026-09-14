from __future__ import annotations

from datetime import datetime, timezone

from school_secretary.ingest.parser import (
    extract_due_from_text,
    infer_assignment_type,
    parse_dropbox_item,
    parse_enrollment_item,
    parse_news_item,
)


def test_parse_iso_due_and_html_attachment():
    item = {
        "Id": 7,
        "Name": "Lab 2 — Binary Search Trees",
        "DueDate": "2026-10-03T23:59:00.000Z",
        "Instructions": {
            "Text": "Implement a binary search tree. Write unit tests.",
            "Html": '<p>See <a href="cisc235-lab2.pdf">lab PDF</a></p>',
        },
        "Attachments": [{"FileId": 1, "FileName": "cisc235-lab2-rubric.pdf"}],
    }
    parsed = parse_dropbox_item(item)
    assert parsed["d2l_id"] == "7"
    assert parsed["due_at"].year == 2026
    assert parsed["due_at"].month == 10
    assert parsed["assignment_type"] == "coding_lab"
    names = {a["filename"] for a in parsed["attachments"]}
    assert "cisc235-lab2.pdf" in names
    assert "cisc235-lab2-rubric.pdf" in names


def test_due_date_from_prose_when_api_field_missing():
    dt = extract_due_from_text("Due: Friday, October 3, 2026 at 11:59 PM")
    assert dt is not None
    assert dt.month == 10 and dt.day == 3


def test_essay_type_and_news():
    drop = parse_dropbox_item(
        {
            "Id": 1,
            "Name": "Essay 1 — Mill",
            "Instructions": {"Text": "Write a 1500 word essay on utilitarianism."},
        }
    )
    assert drop["assignment_type"] == "essay"
    news = parse_news_item(
        {
            "Id": 2,
            "Title": "Hello",
            "Body": {"Html": "<p>Lab 1 deadline extended.</p>"},
            "StartDate": "2026-09-13T18:00:00.000Z",
        }
    )
    assert "extended" in news["body"].lower()
    assert news["posted_at"] == datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)


def test_enrollment_course_offering():
    parsed = parse_enrollment_item(
        {
            "OrgUnit": {
                "Id": 66123,
                "Name": "CISC 235 Data Structures",
                "Code": "CISC235",
                "Type": {"Id": 3, "Code": "Course Offering"},
            }
        }
    )
    assert parsed is not None
    assert parsed["code"] == "CISC 235"
    assert parsed["org_unit_id"] == "66123"
