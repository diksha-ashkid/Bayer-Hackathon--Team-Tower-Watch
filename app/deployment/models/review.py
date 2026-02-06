"""Review-specific data models."""

from dataclasses import dataclass, field
from typing import Optional, Any

from app.deployment.models.common import Severity, ReviewType


@dataclass(frozen=True)
class CodeLocation:
    """Represents a specific location in code."""

    file_path: str
    start_line: int
    end_line: Optional[int] = None
    start_column: Optional[int] = None
    end_column: Optional[int] = None


@dataclass(frozen=True)
class ReviewFinding:
    """Represents a single finding from code review."""

    severity: Severity
    category: str
    message: str
    location: Optional[CodeLocation] = None
    suggestion: Optional[str] = None
    rule_id: Optional[str] = None


@dataclass
class ReviewContext:
    """Context passed to the review agent."""

    review_type: ReviewType
    repository_owner: str
    repository_name: str
    ref: str
    files: dict[str, str] = field(default_factory=dict)  # path -> content
    diff_context: Optional[dict[str, Any]] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert context to dictionary for agent consumption."""
        return {
            "review_type": self.review_type.value,
            "repository": {
                "owner": self.repository_owner,
                "name": self.repository_name,
            },
            "ref": self.ref,
            "files": self.files,
            "diff_context": self.diff_context,
            "metadata": self.metadata,
        }


@dataclass
class ReviewResult:
    """Result of a code review operation."""

    success: bool
    review_type: ReviewType
    ref: str
    findings: list[ReviewFinding] = field(default_factory=list)
    summary: Optional[str] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert result to structured JSON output."""
        return {
            "success": self.success,
            "review_type": self.review_type.value,
            "ref": self.ref,
            "findings": [
                {
                    "severity": f.severity.value,
                    "category": f.category,
                    "message": f.message,
                    "location": (
                        {
                            "file_path": f.location.file_path,
                            "start_line": f.location.start_line,
                            "end_line": f.location.end_line,
                            "start_column": f.location.start_column,
                            "end_column": f.location.end_column,
                        }
                        if f.location
                        else None
                    ),
                    "suggestion": f.suggestion,
                    "rule_id": f.rule_id,
                }
                for f in self.findings
            ],
            "summary": self.summary,
            "error": self.error,
            "metadata": self.metadata,
            "statistics": {
                "total": len(self.findings),
                "critical": sum(
                    1 for f in self.findings if f.severity == Severity.CRITICAL
                ),
                "warning": sum(
                    1 for f in self.findings if f.severity == Severity.WARNING
                ),
                "info": sum(1 for f in self.findings if f.severity == Severity.INFO),
            },
        }
