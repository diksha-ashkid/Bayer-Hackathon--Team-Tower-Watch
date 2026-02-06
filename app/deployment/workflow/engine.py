"""Review workflow engine for orchestrating code reviews."""

import logging
from typing import Any, Optional

from app.deployment.agents.base import ReviewAgent, ReviewAgentError
from app.deployment.clients.github_client import (
    GitHubClient,
    GitHubClientError,
    GitHubNotFoundError,
)
from app.deployment.config import AppConfig
from app.deployment.models.common import ReviewType, Severity
from app.deployment.models.github import GitHubFile
from app.deployment.models.review import (
    CodeLocation,
    ReviewContext,
    ReviewFinding,
    ReviewResult,
)
from app.deployment.normalization import CodeNormalizer
from app.deployment.workflow.exceptions import (
    RepositoryAccessError,
    ReviewerNotAssignedError,
    WorkflowError,
)

logger = logging.getLogger(__name__)


class ReviewWorkflowEngine:
    """
    Orchestrates the code review workflow.

    Handles:
    - Repository/commit review (CI/CD trigger)
    - Pull request review (PR trigger)

    Uses dependency injection for the review agent.
    """

    def __init__(
        self,
        config: AppConfig,
        github_client: GitHubClient,
        agent: ReviewAgent,
    ) -> None:
        """
        Initialize the workflow engine.

        Args:
            config: Application configuration
            github_client: GitHub API client
            agent: Pluggable review agent
        """
        self._config = config
        self._github = github_client
        self._agent = agent
        self._normalizer = CodeNormalizer(config.review)

    def review_commit(
        self,
        owner: str,
        repo: str,
        ref: str,
    ) -> ReviewResult:
        """
        Review repository code at a specific commit/ref.

        This is triggered by CI/CD workflows.

        Args:
            owner: Repository owner
            repo: Repository name
            ref: Commit SHA or branch ref

        Returns:
            ReviewResult with structured findings
        """
        logger.info(f"Starting commit review for {owner}/{repo}@{ref}")

        try:
            # Fetch commit info
            commit = self._github.get_commit(owner, repo, ref)
            logger.debug(f"Fetched commit: {commit.sha}")

            # Get repository tree
            tree_entries = self._github.get_tree(owner, repo, commit.tree_sha)
            logger.debug(f"Fetched tree with {len(tree_entries)} entries")

            # Filter to supported files
            filtered_entries = self._normalizer.filter_tree_entries(tree_entries)
            logger.info(f"Filtered to {len(filtered_entries)} reviewable files")

            # Fetch file contents
            files = self._fetch_files(owner, repo, ref, filtered_entries)
            normalized_files = self._normalizer.normalize_files(files)

            # Build context
            repo_context = self._normalizer.build_repository_context(
                owner=owner,
                name=repo,
                ref=commit.sha,
                files=normalized_files,
            )

            review_context = ReviewContext(
                review_type=ReviewType.COMMIT,
                repository_owner=owner,
                repository_name=repo,
                ref=commit.sha,
                files=repo_context.to_file_dict(),
                metadata={
                    "commit_message": commit.message,
                    "commit_author": commit.author,
                    "commit_timestamp": commit.timestamp,
                },
            )

            # Execute review
            return self._execute_review(review_context)

        except GitHubNotFoundError as e:
            logger.error(f"Repository or ref not found: {e}")
            raise RepositoryAccessError(owner, repo, str(e))
        except GitHubClientError as e:
            logger.error(f"GitHub API error: {e}")
            raise WorkflowError(f"GitHub API error: {e}")

    def review_pull_request(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> ReviewResult:
        """
        Review a pull request.

        Only proceeds if the automation account is assigned as a reviewer.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: Pull request number

        Returns:
            ReviewResult with structured findings

        Raises:
            ReviewerNotAssignedError: If automation account is not a reviewer
        """
        logger.info(f"Starting PR review for {owner}/{repo}#{pr_number}")

        try:
            # Fetch PR details
            pr = self._github.get_pull_request(owner, repo, pr_number)
            logger.debug(f"Fetched PR: {pr.title} ({pr.state})")

            # Verify automation account is assigned as reviewer
            automation_user = self._config.review.automation_account_username
            if automation_user not in pr.requested_reviewers:
                logger.warning(
                    f"Automation account '{automation_user}' not in reviewers: "
                    f"{pr.requested_reviewers}"
                )
                raise ReviewerNotAssignedError(pr_number, automation_user)

            # Fetch PR files
            file_diffs = self._github.get_pull_request_files(owner, repo, pr_number)
            logger.info(f"PR has {len(file_diffs)} changed files")

            # Fetch full content for added/modified files
            file_contents: dict[str, str] = {}
            for diff in file_diffs:
                if self._normalizer.is_supported_file(diff.filename):
                    try:
                        content = self._github.get_blob_content(
                            owner, repo, diff.sha
                        )
                        file_contents[diff.filename] = content
                    except GitHubClientError as e:
                        logger.warning(
                            f"Could not fetch content for {diff.filename}: {e}"
                        )

            # Normalize diffs
            normalized_diffs = self._normalizer.normalize_diffs(
                file_diffs, file_contents
            )

            # Build context
            diff_context = {
                "base_ref": pr.base_ref,
                "head_ref": pr.head_ref,
                "base_sha": pr.base_sha,
                "head_sha": pr.head_sha,
                "diffs": [d.to_dict() for d in normalized_diffs],
            }

            review_context = ReviewContext(
                review_type=ReviewType.PULL_REQUEST,
                repository_owner=owner,
                repository_name=repo,
                ref=pr.head_sha,
                files=file_contents,
                diff_context=diff_context,
                metadata={
                    "pr_number": pr.number,
                    "pr_title": pr.title,
                    "pr_author": pr.author,
                    "base_ref": pr.base_ref,
                    "head_ref": pr.head_ref,
                },
            )

            # Execute review
            return self._execute_review(review_context)

        except GitHubNotFoundError as e:
            logger.error(f"PR not found: {e}")
            raise WorkflowError(f"Pull request #{pr_number} not found")
        except GitHubClientError as e:
            logger.error(f"GitHub API error: {e}")
            raise WorkflowError(f"GitHub API error: {e}")

    def _fetch_files(
        self,
        owner: str,
        repo: str,
        ref: str,
        entries: list,
    ) -> list[GitHubFile]:
        """Fetch file contents for tree entries."""
        files = []
        for entry in entries:
            try:
                file = self._github.get_file_content(owner, repo, entry.path, ref)
                files.append(file)
            except GitHubClientError as e:
                logger.warning(f"Could not fetch {entry.path}: {e}")
        return files

    def _execute_review(self, context: ReviewContext) -> ReviewResult:
        """Execute the review agent and process results."""
        try:
            logger.info(f"Executing review agent for {context.review_type.value}")
            agent_result = self._agent.review(context.to_dict())

            # Parse findings from agent response
            findings = self._parse_findings(agent_result.get("findings", []))

            return ReviewResult(
                success=True,
                review_type=context.review_type,
                ref=context.ref,
                findings=findings,
                summary=agent_result.get("summary"),
                metadata=context.metadata,
            )

        except ReviewAgentError as e:
            logger.error(f"Review agent error: {e}")
            return ReviewResult(
                success=False,
                review_type=context.review_type,
                ref=context.ref,
                error=str(e),
                metadata=context.metadata,
            )

    def _parse_findings(
        self, raw_findings: list[dict[str, Any]]
    ) -> list[ReviewFinding]:
        """Parse raw agent findings into ReviewFinding objects."""
        findings = []

        for raw in raw_findings:
            severity_str = raw.get("severity", "info").lower()
            severity_map = {
                "critical": Severity.CRITICAL,
                "warning": Severity.WARNING,
                "info": Severity.INFO,
            }
            severity = severity_map.get(severity_str, Severity.INFO)

            location = None
            if raw.get("location"):
                loc = raw["location"]
                location = CodeLocation(
                    file_path=loc.get("file_path", ""),
                    start_line=loc.get("start_line", 0),
                    end_line=loc.get("end_line"),
                    start_column=loc.get("start_column"),
                    end_column=loc.get("end_column"),
                )

            findings.append(
                ReviewFinding(
                    severity=severity,
                    category=raw.get("category", "general"),
                    message=raw.get("message", ""),
                    location=location,
                    suggestion=raw.get("suggestion"),
                    rule_id=raw.get("rule_id"),
                )
            )

        return findings
