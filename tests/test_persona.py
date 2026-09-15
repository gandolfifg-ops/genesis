from school_secretary.agents.persona import polish_outgoing


def test_polish_strips_source_refs_and_local_paths():
    raw = (
        "Late work loses 10% per day (Source: announcement-961f37597baf.txt). "
        "Import /workspace/data/calendar/school-secretary.ics into Google Calendar. "
        "See C:\\Users\\me\\data\\raw\\lab.pdf as well."
    )
    cleaned = polish_outgoing(raw)
    assert "(Source:" not in cleaned
    assert "announcement-961f37597baf.txt" not in cleaned
    assert "/workspace" not in cleaned
    assert "school-secretary.ics" not in cleaned
    assert "C:\\Users" not in cleaned
    assert "10%" in cleaned
    assert "your calendar file" in cleaned
    assert "local file" in cleaned
