"""Unit tests for the mock review agent."""

import pytest

from app.agents import MockReviewAgent
from app.agents.base import ReviewAgentError


class TestMockReviewAgent:
    """Tests for MockReviewAgent."""

    def test_review_valid_context(self):
        """Test review with valid context."""
        agent = MockReviewAgent()
        context = {
            "review_type": "commit",
            "repository": {"owner": "acme", "name": "myapp"},
            "ref": "abc123",
            "files": {
                "main.py": "# TODO: implement this\nprint('hello')",
            },
        }

        result = agent.review(context)

        assert "findings" in result
        assert "summary" in result
        assert len(result["findings"]) > 0

    def test_review_detects_todo(self):
        """Test that TODO comments are detected."""
        agent = MockReviewAgent()
        context = {
            "review_type": "commit",
            "repository": {"owner": "acme", "name": "myapp"},
            "ref": "abc123",
            "files": {
                "main.py": "# TODO: fix this later",
            },
        }

        result = agent.review(context)

        findings = result["findings"]
        assert any(f["rule_id"] == "MOCK001" for f in findings)

    def test_review_detects_hardcoded_secret(self):
        """Test that hardcoded secrets are detected."""
        agent = MockReviewAgent()
        context = {
            "review_type": "commit",
            "repository": {"owner": "acme", "name": "myapp"},
            "ref": "abc123",
            "files": {
                "config.py": "password = 'secret123'",
            },
        }

        result = agent.review(context)

        findings = result["findings"]
        critical_findings = [f for f in findings if f["severity"] == "critical"]
        assert any(f["rule_id"] == "MOCK002" for f in critical_findings)

    def test_review_detects_eval(self):
        """Test that eval/exec usage is detected."""
        agent = MockReviewAgent()
        context = {
            "review_type": "commit",
            "repository": {"owner": "acme", "name": "myapp"},
            "ref": "abc123",
            "files": {
                "main.py": "result = eval(user_input)",
            },
        }

        result = agent.review(context)

        findings = result["findings"]
        assert any(f["rule_id"] == "MOCK003" for f in findings)

    def test_review_invalid_context(self):
        """Test review with invalid context raises error."""
        agent = MockReviewAgent()
        context = {"files": {}}  # Missing required fields

        with pytest.raises(ReviewAgentError):
            agent.review(context)

    def test_review_agent_configured_to_fail(self):
        """Test that agent fails when configured."""
        agent = MockReviewAgent(should_fail=True)
        context = {
            "review_type": "commit",
            "repository": {"owner": "acme", "name": "myapp"},
            "ref": "abc123",
            "files": {},
        }

        with pytest.raises(ReviewAgentError):
            agent.review(context)

    def test_validate_context_valid(self):
        """Test context validation with valid context."""
        agent = MockReviewAgent()
        context = {
            "review_type": "commit",
            "repository": {"owner": "acme", "name": "myapp"},
            "ref": "abc123",
        }

        assert agent.validate_context(context) is True

    def test_validate_context_invalid(self):
        """Test context validation with invalid context."""
        agent = MockReviewAgent()
        context = {"files": {}}

        assert agent.validate_context(context) is False
