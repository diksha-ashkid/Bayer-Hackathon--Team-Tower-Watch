"""GitHub API client for repository and PR operations."""

import base64
import re
from typing import Optional
import httpx

from app.config import GitHubConfig
from app.models.common import FileChangeType
from app.models.github import (
    GitHubCommit,
    GitHubDiffHunk,
    GitHubFile,
    GitHubFileDiff,
    GitHubPullRequest,
    GitHubRepository,
    GitHubTreeEntry,
)


class GitHubClientError(Exception):
    """Base exception for GitHub client errors."""

    pass


class GitHubAuthenticationError(GitHubClientError):
    """Authentication failed with GitHub API."""

    pass


class GitHubRateLimitError(GitHubClientError):
    """GitHub API rate limit exceeded."""

    pass


class GitHubNotFoundError(GitHubClientError):
    """Requested resource not found."""

    pass


class GitHubClient:
    """Token-based GitHub API client."""

    def __init__(self, config: GitHubConfig) -> None:
        """Initialize the GitHub client with configuration."""
        self._config = config
        self._client = httpx.Client(
            base_url=config.api_base_url,
            headers={
                "Authorization": f"Bearer {config.access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=config.timeout_seconds,
        )

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._client.close()

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def _request(
        self, method: str, endpoint: str, **kwargs
    ) -> dict:
        """Make an HTTP request with error handling and retries."""
        last_error = None
        for attempt in range(self._config.max_retries):
            try:
                response = self._client.request(method, endpoint, **kwargs)
                return self._handle_response(response)
            except httpx.TimeoutException as e:
                last_error = GitHubClientError(f"Request timeout: {e}")
            except httpx.RequestError as e:
                last_error = GitHubClientError(f"Request failed: {e}")

        raise last_error or GitHubClientError("Unknown error")

    def _handle_response(self, response: httpx.Response) -> dict:
        """Handle HTTP response and raise appropriate exceptions."""
        if response.status_code == 200:
            return response.json()
        if response.status_code == 401:
            raise GitHubAuthenticationError("Invalid or expired access token")
        if response.status_code == 403:
            if "rate limit" in response.text.lower():
                raise GitHubRateLimitError("API rate limit exceeded")
            raise GitHubClientError(f"Access forbidden: {response.text}")
        if response.status_code == 404:
            raise GitHubNotFoundError(f"Resource not found: {response.url}")
        raise GitHubClientError(
            f"GitHub API error: {response.status_code} - {response.text}"
        )

    def get_repository(self, owner: str, repo: str) -> GitHubRepository:
        """Fetch repository metadata."""
        data = self._request("GET", f"/repos/{owner}/{repo}")
        return GitHubRepository(
            owner=data["owner"]["login"],
            name=data["name"],
            default_branch=data["default_branch"],
            full_name=data["full_name"],
        )

    def get_commit(self, owner: str, repo: str, ref: str) -> GitHubCommit:
        """Fetch commit details by SHA or ref."""
        data = self._request("GET", f"/repos/{owner}/{repo}/commits/{ref}")
        return GitHubCommit(
            sha=data["sha"],
            message=data["commit"]["message"],
            author=data["commit"]["author"]["name"],
            timestamp=data["commit"]["author"]["date"],
            tree_sha=data["commit"]["tree"]["sha"],
        )

    def get_tree(
        self, owner: str, repo: str, tree_sha: str, recursive: bool = True
    ) -> list[GitHubTreeEntry]:
        """Fetch repository tree at a given SHA."""
        params = {"recursive": "1"} if recursive else {}
        data = self._request(
            "GET", f"/repos/{owner}/{repo}/git/trees/{tree_sha}", params=params
        )
        return [
            GitHubTreeEntry(
                path=entry["path"],
                sha=entry["sha"],
                type=entry["type"],
                size=entry.get("size"),
                mode=entry["mode"],
            )
            for entry in data.get("tree", [])
        ]

    def get_file_content(
        self, owner: str, repo: str, path: str, ref: str
    ) -> GitHubFile:
        """Fetch file content at a specific ref."""
        data = self._request(
            "GET", f"/repos/{owner}/{repo}/contents/{path}", params={"ref": ref}
        )

        if data.get("type") != "file":
            raise GitHubClientError(f"Path is not a file: {path}")

        content = data.get("content", "")
        encoding = data.get("encoding", "base64")

        if encoding == "base64":
            decoded_content = base64.b64decode(content).decode("utf-8")
        else:
            decoded_content = content

        return GitHubFile(
            path=data["path"],
            sha=data["sha"],
            content=decoded_content,
            encoding="utf-8",
            size=data.get("size", 0),
        )

    def get_blob_content(self, owner: str, repo: str, sha: str) -> str:
        """Fetch blob content by SHA."""
        data = self._request("GET", f"/repos/{owner}/{repo}/git/blobs/{sha}")

        content = data.get("content", "")
        encoding = data.get("encoding", "base64")

        if encoding == "base64":
            return base64.b64decode(content).decode("utf-8")
        return content

    def get_pull_request(
        self, owner: str, repo: str, pr_number: int
    ) -> GitHubPullRequest:
        """Fetch pull request details."""
        data = self._request("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}")

        requested_reviewers = [
            reviewer["login"] for reviewer in data.get("requested_reviewers", [])
        ]

        return GitHubPullRequest(
            number=data["number"],
            title=data["title"],
            state=data["state"],
            head_sha=data["head"]["sha"],
            base_sha=data["base"]["sha"],
            head_ref=data["head"]["ref"],
            base_ref=data["base"]["ref"],
            author=data["user"]["login"],
            requested_reviewers=requested_reviewers,
        )

    def get_pull_request_files(
        self, owner: str, repo: str, pr_number: int
    ) -> list[GitHubFileDiff]:
        """Fetch list of files changed in a pull request."""
        data = self._request("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}/files")

        files = []
        for file_data in data:
            status_map = {
                "added": FileChangeType.ADDED,
                "modified": FileChangeType.MODIFIED,
                "removed": FileChangeType.DELETED,
                "renamed": FileChangeType.RENAMED,
            }

            patch = file_data.get("patch", "")
            hunks = self._parse_diff_hunks(patch) if patch else []

            files.append(
                GitHubFileDiff(
                    filename=file_data["filename"],
                    sha=file_data["sha"],
                    status=status_map.get(
                        file_data["status"], FileChangeType.MODIFIED
                    ),
                    additions=file_data.get("additions", 0),
                    deletions=file_data.get("deletions", 0),
                    patch=patch,
                    previous_filename=file_data.get("previous_filename"),
                    hunks=hunks,
                )
            )

        return files

    def _parse_diff_hunks(self, patch: str) -> list[GitHubDiffHunk]:
        """Parse diff patch into structured hunks."""
        hunks = []
        hunk_pattern = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

        lines = patch.split("\n")
        current_hunk_content: list[str] = []
        current_hunk_header: Optional[re.Match] = None

        for line in lines:
            match = hunk_pattern.match(line)
            if match:
                if current_hunk_header and current_hunk_content:
                    hunks.append(
                        self._create_hunk(current_hunk_header, current_hunk_content)
                    )
                current_hunk_header = match
                current_hunk_content = []
            elif current_hunk_header is not None:
                current_hunk_content.append(line)

        if current_hunk_header and current_hunk_content:
            hunks.append(self._create_hunk(current_hunk_header, current_hunk_content))

        return hunks

    def _create_hunk(
        self, match: re.Match, content_lines: list[str]
    ) -> GitHubDiffHunk:
        """Create a GitHubDiffHunk from parsed data."""
        return GitHubDiffHunk(
            old_start=int(match.group(1)),
            old_lines=int(match.group(2) or 1),
            new_start=int(match.group(3)),
            new_lines=int(match.group(4) or 1),
            content="\n".join(content_lines),
        )

    def get_authenticated_user(self) -> str:
        """Get the username of the authenticated user."""
        data = self._request("GET", "/user")
        return data["login"]
