"""Pydantic schemas for ingestion API endpoints."""

from typing import Optional, Any

from pydantic import BaseModel, Field


class CommitReviewRequest(BaseModel):
    """Request schema for commit/repository review."""

    owner: str = Field(..., description="Repository owner (user or organization)")
    repo: str = Field(..., description="Repository name")
    ref: str = Field(
        ...,
        description="Commit SHA, branch name, or tag to review",
        examples=["main", "abc123def", "v1.0.0"],
    )

    model_config = {"json_schema_extra": {"example": {"owner": "acme", "repo": "myapp", "ref": "main"}}}


class PullRequestReviewRequest(BaseModel):
    """Request schema for pull request review."""

    owner: str = Field(..., description="Repository owner (user or organization)")
    repo: str = Field(..., description="Repository name")
    pr_number: int = Field(..., gt=0, description="Pull request number")

    model_config = {"json_schema_extra": {"example": {"owner": "acme", "repo": "myapp", "pr_number": 42}}}


class CodeLocationResponse(BaseModel):
    """Response schema for code location."""

    file_path: str
    start_line: int
    end_line: Optional[int] = None
    start_column: Optional[int] = None
    end_column: Optional[int] = None


class FindingResponse(BaseModel):
    """Response schema for a single review finding."""

    severity: str = Field(..., description="Severity level: critical | warning | info")
    category: str = Field(..., description="Category of the finding")
    message: str = Field(..., description="Description of the issue")
    location: Optional[CodeLocationResponse] = None
    suggestion: Optional[str] = Field(None, description="Recommended fix")
    rule_id: Optional[str] = Field(None, description="Rule identifier")


class StatisticsResponse(BaseModel):
    """Response schema for review statistics."""

    total: int
    critical: int
    warning: int
    info: int


class ReviewResponse(BaseModel):
    """Response schema for review results."""

    success: bool
    review_type: str
    ref: str
    findings: list[FindingResponse] = Field(default_factory=list)
    summary: Optional[str] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    statistics: StatisticsResponse

    model_config = {
        "json_schema_extra": {
            "example": {
                "success": True,
                "review_type": "pull_request",
                "ref": "abc123",
                "findings": [
                    {
                        "severity": "warning",
                        "category": "security",
                        "message": "Hardcoded API key detected",
                        "location": {
                            "file_path": "src/config.py",
                            "start_line": 15,
                            "end_line": 15,
                        },
                        "suggestion": "Use environment variables",
                        "rule_id": "SEC001",
                    }
                ],
                "summary": "Found 1 issue in 5 files",
                "metadata": {"pr_number": 42},
                "statistics": {"total": 1, "critical": 0, "warning": 1, "info": 0},
            }
        }
    }


class ErrorResponse(BaseModel):
    """Response schema for error responses."""

    error: str
    detail: Optional[str] = None
    code: str = Field(..., description="Error code for programmatic handling")

    model_config = {
        "json_schema_extra": {
            "example": {
                "error": "Reviewer not assigned",
                "detail": "Automation account 'bot-reviewer' is not assigned as a reviewer on PR #42",
                "code": "REVIEWER_NOT_ASSIGNED",
            }
        }
    }
