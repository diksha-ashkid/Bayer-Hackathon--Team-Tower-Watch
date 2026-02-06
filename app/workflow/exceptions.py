"""Workflow-specific exceptions."""


class WorkflowError(Exception):
    """Base exception for workflow errors."""

    pass


class ReviewerNotAssignedError(WorkflowError):
    """Raised when automation account is not assigned as reviewer."""

    def __init__(self, pr_number: int, automation_user: str) -> None:
        self.pr_number = pr_number
        self.automation_user = automation_user
        super().__init__(
            f"Automation account '{automation_user}' is not assigned "
            f"as a reviewer on PR #{pr_number}"
        )


class RepositoryAccessError(WorkflowError):
    """Raised when repository cannot be accessed."""

    def __init__(self, owner: str, repo: str, reason: str) -> None:
        self.owner = owner
        self.repo = repo
        self.reason = reason
        super().__init__(
            f"Cannot access repository {owner}/{repo}: {reason}"
        )


class InvalidRefError(WorkflowError):
    """Raised when a commit ref is invalid."""

    def __init__(self, ref: str, reason: str) -> None:
        self.ref = ref
        self.reason = reason
        super().__init__(f"Invalid ref '{ref}': {reason}")


class FileFetchError(WorkflowError):
    """Raised when files cannot be fetched."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Cannot fetch file '{path}': {reason}")
