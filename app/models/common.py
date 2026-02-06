"""Common enumerations and types for the code review service."""

from enum import Enum


class Severity(str, Enum):
    """Severity levels for review findings."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class ReviewType(str, Enum):
    """Type of review being performed."""

    FULL_REPOSITORY = "full_repository"
    PULL_REQUEST = "pull_request"
    COMMIT = "commit"


class FileChangeType(str, Enum):
    """Type of change in a file."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
