"""GitHub-specific data models."""

from dataclasses import dataclass, field
from typing import Optional

from app.deployment.models.common import FileChangeType


@dataclass(frozen=True)
class GitHubTreeEntry:
    """Represents a single entry in a GitHub repository tree."""

    path: str
    sha: str
    type: str  # "blob" | "tree"
    size: Optional[int] = None
    mode: str = "100644"


@dataclass(frozen=True)
class GitHubFile:
    """Represents a file with its content from GitHub."""

    path: str
    sha: str
    content: str
    encoding: str = "utf-8"
    size: int = 0


@dataclass(frozen=True)
class GitHubDiffHunk:
    """Represents a diff hunk within a file change."""

    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    content: str


@dataclass
class GitHubFileDiff:
    """Represents a file diff in a pull request."""

    filename: str
    sha: str
    status: FileChangeType
    additions: int
    deletions: int
    patch: Optional[str] = None
    previous_filename: Optional[str] = None
    hunks: list[GitHubDiffHunk] = field(default_factory=list)


@dataclass(frozen=True)
class GitHubCommit:
    """Represents a GitHub commit."""

    sha: str
    message: str
    author: str
    timestamp: str
    tree_sha: str


@dataclass
class GitHubPullRequest:
    """Represents a GitHub pull request."""

    number: int
    title: str
    state: str
    head_sha: str
    base_sha: str
    head_ref: str
    base_ref: str
    author: str
    requested_reviewers: list[str] = field(default_factory=list)
    files: list[GitHubFileDiff] = field(default_factory=list)


@dataclass(frozen=True)
class GitHubRepository:
    """Represents a GitHub repository."""

    owner: str
    name: str
    default_branch: str
    full_name: str
