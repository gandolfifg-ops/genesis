from school_secretary.agents.triage import categorize_text


def test_deadline_change():
    assert categorize_text("Lab 1 deadline extended to Friday 6pm", "") == "deadline_change"


def test_logistics_midterm():
    assert categorize_text("Midterm date confirmed: 16 October", "") == "logistics"


def test_guest_lecture():
    assert categorize_text("Guest lecture Thursday", "usual room") == "logistics"
