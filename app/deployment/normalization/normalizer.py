"""Code normalization utilities for repository trees and diffs."""

from dataclasses import dataclass, field
from typing import Optional, Any

from app.deployment.config import ReviewConfig
from app.deployment.models.github import (
    GitHubFile,
    GitHubFileDiff,
    GitHubTreeEntry,
)
from app.deployment.models.common import FileChangeType


@dataclass
class NormalizedFile:
    """A normalized file representation for analysis."""

    path: str
    content: str
    language: str
    size: int
    sha: Optional[str] = None


@dataclass
class DiffLine:
    """Represents a single line in a diff."""

    line_number: int
    content: str
    change_type: str  # "added" | "removed" | "context"


@dataclass
class NormalizedDiff:
    """A normalized diff representation for analysis."""

    path: str
    change_type: FileChangeType
    language: str
    additions: int
    deletions: int
    previous_path: Optional[str] = None
    added_lines: list[DiffLine] = field(default_factory=list)
    removed_lines: list[DiffLine] = field(default_factory=list)
    context_lines: list[DiffLine] = field(default_factory=list)
    full_new_content: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for agent context."""
        return {
            "path": self.path,
            "change_type": self.change_type.value,
            "language": self.language,
            "additions": self.additions,
            "deletions": self.deletions,
            "previous_path": self.previous_path,
            "added_lines": [
                {"line": ln.line_number, "content": ln.content}
                for ln in self.added_lines
            ],
            "removed_lines": [
                {"line": ln.line_number, "content": ln.content}
                for ln in self.removed_lines
            ],
        }


@dataclass
class NormalizedRepository:
    """A normalized repository representation for analysis."""

    owner: str
    name: str
    ref: str
    files: list[NormalizedFile] = field(default_factory=list)

    def to_file_dict(self) -> dict[str, str]:
        """Convert files to path->content dictionary."""
        return {f.path: f.content for f in self.files}


class CodeNormalizer:
    """Normalizes GitHub repository data for code analysis."""

    LANGUAGE_MAP: dict[str, str] = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".jsx": "javascript",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "c",
        ".hpp": "cpp",
        ".cs": "csharp",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".kt": "kotlin",
        ".scala": "scala",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".xml": "xml",
        ".sql": "sql",
        ".sh": "shell",
        ".bash": "shell",
        ".zsh": "shell",
        ".dockerfile": "dockerfile",
        ".tf": "terraform",
        ".hcl": "hcl",
        ".md": "markdown",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
        ".less": "less",
    }

    def __init__(self, config: ReviewConfig) -> None:
        """Initialize normalizer with configuration."""
        self._config = config

    def detect_language(self, file_path: str) -> str:
        """Detect programming language from file extension."""
        path_lower = file_path.lower()

        # Handle special cases
        if path_lower.endswith("dockerfile") or "/dockerfile" in path_lower:
            return "dockerfile"

        # Check extension
        for ext, lang in self.LANGUAGE_MAP.items():
            if path_lower.endswith(ext):
                return lang

        return "unknown"

    def is_supported_file(self, path: str) -> bool:
        """Check if file extension is supported for review."""
        path_lower = path.lower()
        return any(
            path_lower.endswith(ext) for ext in self._config.supported_extensions
        )

    def filter_tree_entries(
        self, entries: list[GitHubTreeEntry]
    ) -> list[GitHubTreeEntry]:
        """Filter tree entries to only supported files."""
        filtered = []
        for entry in entries:
            if entry.type != "blob":
                continue
            if not self.is_supported_file(entry.path):
                continue
            if entry.size and entry.size > self._config.max_file_size_bytes:
                continue
            filtered.append(entry)

        return filtered[: self._config.max_files_per_review]

    def normalize_file(self, github_file: GitHubFile) -> NormalizedFile:
        """Normalize a GitHub file for analysis."""
        return NormalizedFile(
            path=github_file.path,
            content=github_file.content,
            language=self.detect_language(github_file.path),
            size=github_file.size,
            sha=github_file.sha,
        )

    def normalize_files(self, files: list[GitHubFile]) -> list[NormalizedFile]:
        """Normalize multiple GitHub files."""
        return [
            self.normalize_file(f)
            for f in files
            if self.is_supported_file(f.path)
            and len(f.content) <= self._config.max_file_size_bytes
        ]

    def normalize_diff(
        self, file_diff: GitHubFileDiff, full_content: Optional[str] = None
    ) -> NormalizedDiff:
        """Normalize a file diff for analysis."""
        added_lines: list[DiffLine] = []
        removed_lines: list[DiffLine] = []
        context_lines: list[DiffLine] = []

        for hunk in file_diff.hunks:
            current_new_line = hunk.new_start
            current_old_line = hunk.old_start

            for line in hunk.content.split("\n"):
                if not line:
                    continue

                if line.startswith("+"):
                    added_lines.append(
                        DiffLine(
                            line_number=current_new_line,
                            content=line[1:],
                            change_type="added",
                        )
                    )
                    current_new_line += 1
                elif line.startswith("-"):
                    removed_lines.append(
                        DiffLine(
                            line_number=current_old_line,
                            content=line[1:],
                            change_type="removed",
                        )
                    )
                    current_old_line += 1
                else:
                    # Context line (starts with space or is unchanged)
                    content = line[1:] if line.startswith(" ") else line
                    context_lines.append(
                        DiffLine(
                            line_number=current_new_line,
                            content=content,
                            change_type="context",
                        )
                    )
                    current_new_line += 1
                    current_old_line += 1

        return NormalizedDiff(
            path=file_diff.filename,
            change_type=file_diff.status,
            language=self.detect_language(file_diff.filename),
            additions=file_diff.additions,
            deletions=file_diff.deletions,
            previous_path=file_diff.previous_filename,
            added_lines=added_lines,
            removed_lines=removed_lines,
            context_lines=context_lines,
            full_new_content=full_content,
        )

    def normalize_diffs(
        self,
        file_diffs: list[GitHubFileDiff],
        file_contents: Optional[dict[str, str]] = None,
    ) -> list[NormalizedDiff]:
        """Normalize multiple file diffs."""
        file_contents = file_contents or {}
        normalized = []

        for diff in file_diffs:
            if not self.is_supported_file(diff.filename):
                continue
            full_content = file_contents.get(diff.filename)
            normalized.append(self.normalize_diff(diff, full_content))

        return normalized[: self._config.max_files_per_review]

    def build_repository_context(
        self,
        owner: str,
        name: str,
        ref: str,
        files: list[NormalizedFile],
    ) -> NormalizedRepository:
        """Build a normalized repository context for review."""
        return NormalizedRepository(
            owner=owner,
            name=name,
            ref=ref,
            files=files,
        )
