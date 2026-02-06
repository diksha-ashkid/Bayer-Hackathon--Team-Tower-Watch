"""
Code Review Automation Service.

This package provides business logic and ingestion APIs for an automated
code reviewer that integrates with GitHub.

Modules:
    - agents: Pluggable review agent interfaces
    - clients: External service clients (GitHub)
    - config: Configuration management
    - ingestion: FastAPI ingestion endpoints
    - models: Data models
    - normalization: Code normalization utilities
    - workflow: Review workflow engine
"""

from app.deployment.main import app

__all__ = ["app"]
