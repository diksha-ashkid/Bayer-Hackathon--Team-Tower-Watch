"""Dependency injection for ingestion API."""

from functools import lru_cache
from typing import Generator

from app.agents import ReviewAgent, MockReviewAgent
from app.clients import GitHubClient
from app.config import AppConfig, load_config
from app.workflow import ReviewWorkflowEngine


@lru_cache()
def get_config() -> AppConfig:
    """Get application configuration (cached)."""
    return load_config()


def get_github_client() -> Generator[GitHubClient, None, None]:
    """Get GitHub client as a dependency."""
    config = get_config()
    client = GitHubClient(config.github)
    try:
        yield client
    finally:
        client.close()


def get_review_agent() -> ReviewAgent:
    """
    Get the review agent.

    This is the injection point for plugging in different
    review agent implementations.

    Override this dependency to use a custom agent:

        from app.ingestion.dependencies import get_review_agent
        from myapp.agents import MyLLMAgent

        app.dependency_overrides[get_review_agent] = lambda: MyLLMAgent()
    """
    # Default to mock agent - replace with actual implementation
    return MockReviewAgent()


def get_workflow_engine() -> Generator[ReviewWorkflowEngine, None, None]:
    """Get the review workflow engine as a dependency."""
    config = get_config()
    client = GitHubClient(config.github)
    agent = get_review_agent()

    engine = ReviewWorkflowEngine(
        config=config,
        github_client=client,
        agent=agent,
    )

    try:
        yield engine
    finally:
        client.close()
