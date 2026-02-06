"""Configuration management for the code review service."""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GitHubConfig:
    """GitHub API configuration."""

    access_token: str
    api_base_url: str = "https://api.github.com"
    timeout_seconds: int = 30
    max_retries: int = 3


@dataclass(frozen=True)
class ReviewConfig:
    """Review service configuration."""

    automation_account_username: str
    max_files_per_review: int = 100
    max_file_size_bytes: int = 1_000_000  # 1MB
    supported_extensions: tuple[str, ...] = (
        ".py",
        ".js",
        ".ts",
        ".java",
        ".go",
        ".rs",
        ".cpp",
        ".c",
        ".h",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
        ".swift",
        ".kt",
        ".scala",
        ".json",
        ".yaml",
        ".yml",
        ".xml",
        ".sql",
        ".sh",
        ".bash",
        ".zsh",
        ".dockerfile",
        ".tf",
        ".hcl",
    )


@dataclass(frozen=True)
class AppConfig:
    """Application configuration."""

    github: GitHubConfig
    review: ReviewConfig
    debug: bool = False


def load_config() -> AppConfig:
    """Load configuration from environment variables."""
    github_token = os.environ.get("GITHUB_ACCESS_TOKEN")
    if not github_token:
        raise ValueError("GITHUB_ACCESS_TOKEN environment variable is required")

    automation_username = os.environ.get("AUTOMATION_ACCOUNT_USERNAME")
    if not automation_username:
        raise ValueError(
            "AUTOMATION_ACCOUNT_USERNAME environment variable is required"
        )

    github_config = GitHubConfig(
        access_token=github_token,
        api_base_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        timeout_seconds=int(os.environ.get("GITHUB_TIMEOUT_SECONDS", "30")),
        max_retries=int(os.environ.get("GITHUB_MAX_RETRIES", "3")),
    )

    review_config = ReviewConfig(
        automation_account_username=automation_username,
        max_files_per_review=int(os.environ.get("MAX_FILES_PER_REVIEW", "100")),
        max_file_size_bytes=int(os.environ.get("MAX_FILE_SIZE_BYTES", "1000000")),
    )

    return AppConfig(
        github=github_config,
        review=review_config,
        debug=os.environ.get("DEBUG", "false").lower() == "true",
    )
