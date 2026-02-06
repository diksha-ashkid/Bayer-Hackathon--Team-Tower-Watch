"""Integration tests for the ingestion API endpoints."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from app.main import app
from app.ingestion.dependencies import get_workflow_engine
from app.models.common import ReviewType, Severity
from app.models.review import CodeLocation, ReviewFinding, ReviewResult
from app.workflow import ReviewerNotAssignedError, RepositoryAccessError


@pytest.fixture
def mock_engine():
    """Create a mock workflow engine."""
    return MagicMock()


@pytest.fixture
def client(mock_engine):
    """Create a test client with mocked dependencies."""
    app.dependency_overrides[get_workflow_engine] = lambda: mock_engine
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


class TestCommitReviewEndpoint:
    """Tests for the commit review endpoint."""

    def test_review_commit_success(self, client, mock_engine):
        """Test successful commit review."""
        mock_engine.review_commit.return_value = ReviewResult(
            success=True,
            review_type=ReviewType.COMMIT,
            ref="abc123",
            findings=[
                ReviewFinding(
                    severity=Severity.WARNING,
                    category="security",
                    message="Issue found",
                    location=CodeLocation(file_path="main.py", start_line=10),
                )
            ],
            summary="Found 1 issue",
        )

        response = client.post(
            "/api/v1/review/commit",
            json={"owner": "acme", "repo": "myapp", "ref": "main"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["review_type"] == "commit"
        assert len(data["findings"]) == 1
        assert data["statistics"]["warning"] == 1

    def test_review_commit_repository_not_found(self, client, mock_engine):
        """Test commit review when repository is not found."""
        mock_engine.review_commit.side_effect = RepositoryAccessError(
            "acme", "myapp", "Not found"
        )

        response = client.post(
            "/api/v1/review/commit",
            json={"owner": "acme", "repo": "myapp", "ref": "main"},
        )

        assert response.status_code == 404


class TestPullRequestReviewEndpoint:
    """Tests for the pull request review endpoint."""

    def test_review_pr_success(self, client, mock_engine):
        """Test successful PR review."""
        mock_engine.review_pull_request.return_value = ReviewResult(
            success=True,
            review_type=ReviewType.PULL_REQUEST,
            ref="def456",
            findings=[],
            summary="No issues found",
        )

        response = client.post(
            "/api/v1/review/pull-request",
            json={"owner": "acme", "repo": "myapp", "pr_number": 42},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["review_type"] == "pull_request"

    def test_review_pr_reviewer_not_assigned(self, client, mock_engine):
        """Test PR review when automation account is not assigned."""
        mock_engine.review_pull_request.side_effect = ReviewerNotAssignedError(
            42, "bot-reviewer"
        )

        response = client.post(
            "/api/v1/review/pull-request",
            json={"owner": "acme", "repo": "myapp", "pr_number": 42},
        )

        assert response.status_code == 403


class TestHealthEndpoint:
    """Tests for the health check endpoint."""

    def test_health_check(self, client, mock_engine):
        """Test health check returns healthy status."""
        response = client.get("/api/v1/review/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"


class TestRootEndpoint:
    """Tests for the root endpoint."""

    def test_root(self, client, mock_engine):
        """Test root endpoint returns service info."""
        response = client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "code-review-automation"
        assert "version" in data
