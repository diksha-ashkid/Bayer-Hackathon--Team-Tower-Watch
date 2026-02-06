"""Unit tests for the code normalizer."""

import pytest

from app.deployment.config import ReviewConfig
from app.deployment.models.common import FileChangeType
from app.deployment.models.github import GitHubDiffHunk, GitHubFile, GitHubFileDiff, GitHubTreeEntry
from app.deployment.normalization import CodeNormalizer


@pytest.fixture
def review_config() -> ReviewConfig:
    """Create a review config for testing."""
    return ReviewConfig(
        automation_account_username="test-bot",
        max_files_per_review=10,
        max_file_size_bytes=10000,
    )


@pytest.fixture
def normalizer(review_config: ReviewConfig) -> CodeNormalizer:
    """Create a normalizer for testing."""
    return CodeNormalizer(review_config)


class TestCodeNormalizer:
    """Tests for CodeNormalizer."""

    def test_detect_language_python(self, normalizer: CodeNormalizer):
        """Test language detection for Python files."""
        assert normalizer.detect_language("main.py") == "python"
        assert normalizer.detect_language("src/utils/helper.py") == "python"

    def test_detect_language_javascript(self, normalizer: CodeNormalizer):
        """Test language detection for JavaScript files."""
        assert normalizer.detect_language("app.js") == "javascript"
        assert normalizer.detect_language("component.jsx") == "javascript"

    def test_detect_language_typescript(self, normalizer: CodeNormalizer):
        """Test language detection for TypeScript files."""
        assert normalizer.detect_language("app.ts") == "typescript"
        assert normalizer.detect_language("component.tsx") == "typescript"

    def test_detect_language_dockerfile(self, normalizer: CodeNormalizer):
        """Test language detection for Dockerfile."""
        assert normalizer.detect_language("Dockerfile") == "dockerfile"
        assert normalizer.detect_language("docker/Dockerfile") == "dockerfile"

    def test_detect_language_unknown(self, normalizer: CodeNormalizer):
        """Test language detection for unknown extensions."""
        assert normalizer.detect_language("file.xyz") == "unknown"
        assert normalizer.detect_language("README") == "unknown"

    def test_is_supported_file(self, normalizer: CodeNormalizer):
        """Test file support detection."""
        assert normalizer.is_supported_file("main.py") is True
        assert normalizer.is_supported_file("app.js") is True
        assert normalizer.is_supported_file("image.png") is False
        assert normalizer.is_supported_file("binary.exe") is False

    def test_filter_tree_entries(self, normalizer: CodeNormalizer):
        """Test filtering of tree entries."""
        entries = [
            GitHubTreeEntry(path="main.py", sha="abc", type="blob", size=100),
            GitHubTreeEntry(path="image.png", sha="def", type="blob", size=100),
            GitHubTreeEntry(path="src", sha="ghi", type="tree"),
            GitHubTreeEntry(path="large.py", sha="jkl", type="blob", size=100000),
        ]

        filtered = normalizer.filter_tree_entries(entries)

        assert len(filtered) == 1
        assert filtered[0].path == "main.py"

    def test_normalize_file(self, normalizer: CodeNormalizer):
        """Test file normalization."""
        github_file = GitHubFile(
            path="src/main.py",
            sha="abc123",
            content="print('hello')",
            size=15,
        )

        normalized = normalizer.normalize_file(github_file)

        assert normalized.path == "src/main.py"
        assert normalized.content == "print('hello')"
        assert normalized.language == "python"
        assert normalized.size == 15
        assert normalized.sha == "abc123"

    def test_normalize_diff(self, normalizer: CodeNormalizer):
        """Test diff normalization."""
        file_diff = GitHubFileDiff(
            filename="main.py",
            sha="abc123",
            status=FileChangeType.MODIFIED,
            additions=2,
            deletions=1,
            hunks=[
                GitHubDiffHunk(
                    old_start=1,
                    old_lines=3,
                    new_start=1,
                    new_lines=4,
                    content="+new line\n context\n-old line\n+another new",
                )
            ],
        )

        normalized = normalizer.normalize_diff(file_diff)

        assert normalized.path == "main.py"
        assert normalized.change_type == FileChangeType.MODIFIED
        assert normalized.language == "python"
        assert len(normalized.added_lines) == 2
        assert len(normalized.removed_lines) == 1

    def test_build_repository_context(self, normalizer: CodeNormalizer):
        """Test repository context building."""
        from app.deployment.normalization.normalizer import NormalizedFile

        files = [
            NormalizedFile(path="main.py", content="print(1)", language="python", size=10),
            NormalizedFile(path="util.py", content="def x(): pass", language="python", size=15),
        ]

        context = normalizer.build_repository_context(
            owner="acme",
            name="myapp",
            ref="main",
            files=files,
        )

        assert context.owner == "acme"
        assert context.name == "myapp"
        assert context.ref == "main"
        assert len(context.files) == 2
        
        file_dict = context.to_file_dict()
        assert "main.py" in file_dict
        assert file_dict["main.py"] == "print(1)"
