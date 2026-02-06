"""Ingestion API router for code review endpoints."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.deployment.ingestion.schemas import (
    CommitReviewRequest,
    ErrorResponse,
    FindingResponse,
    CodeLocationResponse,
    PullRequestReviewRequest,
    ReviewResponse,
    StatisticsResponse,
)
from app.deployment.ingestion.dependencies import get_workflow_engine
from app.deployment.models.review import ReviewResult
from app.deployment.workflow import (
    ReviewWorkflowEngine,
    ReviewerNotAssignedError,
    RepositoryAccessError,
    WorkflowError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/review", tags=["review"])


def _convert_result_to_response(result: ReviewResult) -> ReviewResponse:
    """Convert domain ReviewResult to API response."""
    findings = []
    for f in result.findings:
        location = None
        if f.location:
            location = CodeLocationResponse(
                file_path=f.location.file_path,
                start_line=f.location.start_line,
                end_line=f.location.end_line,
                start_column=f.location.start_column,
                end_column=f.location.end_column,
            )
        findings.append(
            FindingResponse(
                severity=f.severity.value,
                category=f.category,
                message=f.message,
                location=location,
                suggestion=f.suggestion,
                rule_id=f.rule_id,
            )
        )

    result_dict = result.to_dict()
    stats = result_dict["statistics"]

    return ReviewResponse(
        success=result.success,
        review_type=result.review_type.value,
        ref=result.ref,
        findings=findings,
        summary=result.summary,
        error=result.error,
        metadata=result.metadata,
        statistics=StatisticsResponse(
            total=stats["total"],
            critical=stats["critical"],
            warning=stats["warning"],
            info=stats["info"],
        ),
    )


@router.post(
    "/commit",
    response_model=ReviewResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
    summary="Review repository at a specific commit",
    description=(
        "Trigger a code review for a repository at a specific commit or ref. "
        "This endpoint is designed to be called from CI/CD workflows. "
        "It fetches the repository tree, analyzes all supported files, "
        "and returns structured findings."
    ),
)
async def review_commit(
    request: CommitReviewRequest,
    engine: Annotated[ReviewWorkflowEngine, Depends(get_workflow_engine)],
) -> ReviewResponse:
    """
    Review repository code at a specific commit.

    - **owner**: Repository owner (user or organization)
    - **repo**: Repository name
    - **ref**: Commit SHA, branch name, or tag
    """
    logger.info(f"Received commit review request: {request.owner}/{request.repo}@{request.ref}")

    try:
        result = engine.review_commit(
            owner=request.owner,
            repo=request.repo,
            ref=request.ref,
        )
        return _convert_result_to_response(result)

    except RepositoryAccessError as e:
        logger.error(f"Repository access error: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorResponse(
                error="Repository not found",
                detail=str(e),
                code="REPOSITORY_NOT_FOUND",
            ).model_dump(),
        )
    except WorkflowError as e:
        logger.error(f"Workflow error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorResponse(
                error="Review failed",
                detail=str(e),
                code="WORKFLOW_ERROR",
            ).model_dump(),
        )


@router.post(
    "/pull-request",
    response_model=ReviewResponse,
    responses={
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
    summary="Review a pull request",
    description=(
        "Trigger a code review for a pull request. "
        "The review will only proceed if the automation account is assigned as a reviewer. "
        "It analyzes only the changed files and returns structured findings."
    ),
)
async def review_pull_request(
    request: PullRequestReviewRequest,
    engine: Annotated[ReviewWorkflowEngine, Depends(get_workflow_engine)],
) -> ReviewResponse:
    """
    Review a pull request.

    - **owner**: Repository owner (user or organization)
    - **repo**: Repository name
    - **pr_number**: Pull request number

    Note: The automation account must be assigned as a reviewer on the PR.
    """
    logger.info(
        f"Received PR review request: {request.owner}/{request.repo}#{request.pr_number}"
    )

    try:
        result = engine.review_pull_request(
            owner=request.owner,
            repo=request.repo,
            pr_number=request.pr_number,
        )
        return _convert_result_to_response(result)

    except ReviewerNotAssignedError as e:
        logger.warning(f"Reviewer not assigned: {e}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ErrorResponse(
                error="Reviewer not assigned",
                detail=str(e),
                code="REVIEWER_NOT_ASSIGNED",
            ).model_dump(),
        )
    except RepositoryAccessError as e:
        logger.error(f"Repository access error: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorResponse(
                error="Repository not found",
                detail=str(e),
                code="REPOSITORY_NOT_FOUND",
            ).model_dump(),
        )
    except WorkflowError as e:
        logger.error(f"Workflow error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorResponse(
                error="Review failed",
                detail=str(e),
                code="WORKFLOW_ERROR",
            ).model_dump(),
        )


@router.get(
    "/health",
    summary="Health check endpoint",
    description="Check if the review service is healthy and ready to accept requests.",
)
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}
