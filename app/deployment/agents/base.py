"""Base interface for pluggable review agents."""

from abc import ABC, abstractmethod
from typing import Any


class ReviewAgent(ABC):
    """
    Abstract base class for code review agents.

    This interface enables dependency injection of different review
    implementations (LLM-based, rule-based, hybrid, etc.)

    Usage:
        class MyLLMAgent(ReviewAgent):
            def review(self, context: dict) -> dict:
                # Call LLM API and return findings
                return {"findings": [...]}

        agent = MyLLMAgent()
        result = agent.review(context)
    """

    @abstractmethod
    def review(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        Perform code review on the provided context.

        Args:
            context: Dictionary containing:
                - review_type: "full_repository" | "pull_request" | "commit"
                - repository: {"owner": str, "name": str}
                - ref: str (commit SHA or branch)
                - files: dict[str, str] (path -> content mapping)
                - diff_context: Optional dict with diff information
                - metadata: Additional context-specific metadata

        Returns:
            Dictionary containing:
                - findings: List of finding dictionaries with:
                    - severity: "critical" | "warning" | "info"
                    - category: str (e.g., "syntax", "security", "style")
                    - message: str (description of the issue)
                    - location: Optional dict with file_path, start_line, end_line
                    - suggestion: Optional str with recommended fix
                    - rule_id: Optional str for the rule that triggered this
                - summary: Optional str with overall assessment
                - error: Optional str if review failed

        Raises:
            ReviewAgentError: If the review cannot be completed
        """
        pass

    def validate_context(self, context: dict[str, Any]) -> bool:
        """
        Validate that the context contains required fields.

        Args:
            context: The context dictionary to validate

        Returns:
            True if context is valid, False otherwise
        """
        required_fields = ["review_type", "repository", "ref"]
        return all(field in context for field in required_fields)


class ReviewAgentError(Exception):
    """Exception raised when a review agent encounters an error."""

    pass
