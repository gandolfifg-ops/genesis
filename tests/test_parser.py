from __future__ import annotations

from datetime import datetime, timezone

from school_secretary.ingest.parser import (
    extract_due_from_text,
    infer_assignment_type,
    is_clutter_material,
    is_noisy_assignment_title,
    is_target_announcement,
    is_target_assignment,
    is_target_content_topic,
    parse_dropbox_item,
    parse_enrollment_item,
    parse_news_item,
    should_download_file,
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
    iso = extract_due_from_text("Available until 2026-09-20T23:59:00.000Z")
    assert iso is not None and iso.month == 9 and iso.day == 20
    us = extract_due_from_text("Due Date 9/20/2026 11:59 PM")
    assert us is not None and us.month == 9 and us.day == 20


def test_noisy_dropbox_status_titles_are_skipped():
    assert is_noisy_assignment_title("Not Submitted")
    assert is_noisy_assignment_title("1 Submission, 1 File")
    assert is_noisy_assignment_title("2 Submissions, 2 Files")
    assert is_noisy_assignment_title("0 Files")
    assert is_noisy_assignment_title("Dropbox")
    assert not is_noisy_assignment_title("Lab 2 — Binary Search Trees")
    assert not is_noisy_assignment_title("Assignment 1 CAD drawing")


def test_clutter_filter_skips_residence_readings_and_psets():
    assert is_clutter_material("Residence Contract 2026")
    assert is_clutter_material("Week 4 Required Reading", filename="week4-reading.pdf")
    assert is_clutter_material("Problem Set 3", filename="pset3.pdf")
    assert is_clutter_material("Homework 4 worksheet")
    assert not is_clutter_material("Lab 2 — Binary Search Trees")
    assert not is_clutter_material("CISC 235 Syllabus")
    assert not is_clutter_material("Quiz 2")
    assert not is_target_assignment("Problem Set 3", "Due Friday", None)
    assert is_target_assignment("Lab 2", "Implement BST", None)
    assert is_target_assignment("Midterm", "", "2026-10-16")
    assert is_target_content_topic("Syllabus")
    assert is_target_content_topic("Lab 2 handout")
    assert not is_target_content_topic("Week 3 Reading")
    assert not is_target_content_topic("Residence Agreement")
    assert is_target_announcement("Lab 1 deadline extended")
    assert not is_target_announcement("Residence contract posted", "Sign your lease.")
    assert should_download_file("cisc235-syllabus.pdf", strict=True)
    assert not should_download_file("week4-reading.pdf", strict=True)
    assert not should_download_file("pset3.pdf", "https://onq.example/pset3.pdf", strict=True)


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
