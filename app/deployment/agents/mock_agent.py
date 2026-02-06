"""Mock review agent for testing purposes."""

from typing import Any

from app.deployment.agents.base import ReviewAgent, ReviewAgentError


class MockReviewAgent(ReviewAgent):
    """
    Mock implementation of ReviewAgent for testing.

    This agent performs basic syntactic checks and returns
    deterministic findings for testing the workflow.
    """

    def __init__(self, should_fail: bool = False) -> None:
        """
        Initialize the mock agent.

        Args:
            should_fail: If True, the agent will raise an error on review
        """
        self._should_fail = should_fail

    def review(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        Perform mock code review.

        Returns basic findings based on file content patterns.
        """
        if not self.validate_context(context):
            raise ReviewAgentError("Invalid context: missing required fields")

        if self._should_fail:
            raise ReviewAgentError("Mock agent configured to fail")

        findings = []
        files = context.get("files", {})

        for file_path, content in files.items():
            file_findings = self._analyze_file(file_path, content)
            findings.extend(file_findings)

        return {
            "findings": findings,
            "summary": f"Analyzed {len(files)} files, found {len(findings)} issues",
        }

    def _analyze_file(
        self, file_path: str, content: str
    ) -> list[dict[str, Any]]:
        """Perform basic pattern-based analysis on a file."""
        findings = []
        lines = content.split("\n")

        for line_num, line in enumerate(lines, start=1):
            # Check for common issues (mock patterns)
            if "TODO" in line or "FIXME" in line:
                findings.append(
                    {
                        "severity": "info",
                        "category": "maintenance",
                        "message": "Unresolved TODO/FIXME comment found",
                        "location": {
                            "file_path": file_path,
                            "start_line": line_num,
                            "end_line": line_num,
                        },
                        "rule_id": "MOCK001",
                    }
                )

            if "password" in line.lower() or "secret" in line.lower():
                if "=" in line and not line.strip().startswith("#"):
                    findings.append(
                        {
                            "severity": "critical",
                            "category": "security",
                            "message": "Potential hardcoded secret detected",
                            "location": {
                                "file_path": file_path,
                                "start_line": line_num,
                                "end_line": line_num,
                            },
                            "suggestion": "Use environment variables for secrets",
                            "rule_id": "MOCK002",
                        }
                    )

            if "eval(" in line or "exec(" in line:
                findings.append(
                    {
                        "severity": "warning",
                        "category": "security",
                        "message": "Use of eval/exec detected - potential security risk",
                        "location": {
                            "file_path": file_path,
                            "start_line": line_num,
                            "end_line": line_num,
                        },
                        "suggestion": "Avoid eval/exec when possible",
                        "rule_id": "MOCK003",
                    }
                )

            # Check for very long lines
            if len(line) > 120:
                findings.append(
                    {
                        "severity": "info",
                        "category": "style",
                        "message": f"Line exceeds 120 characters ({len(line)})",
                        "location": {
                            "file_path": file_path,
                            "start_line": line_num,
                            "end_line": line_num,
                        },
                        "rule_id": "MOCK004",
                    }
                )

        return findings
