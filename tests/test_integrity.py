from school_secretary.agents.llm import INTEGRITY_RULE
from school_secretary.telegram_app.handlers import route_locally


def test_integrity_rule_present():
    assert "Never write finished" in INTEGRITY_RULE or "Never" in INTEGRITY_RULE
