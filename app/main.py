"""FastAPI application for code review automation service."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.routing import APIRouter
from fastapi.middleware.cors import CORSMiddleware

from app.ingestion import router as review_router
from app.incident_commander.router import router as incident_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# API version prefix
API_V1_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifespan handler."""
        logger.info("Starting code review automation service")
        yield
        logger.info("Shutting down code review automation service")

    app = FastAPI(
        title="Code Review Automation Service",
        description=(
            "Automated code review service that integrates with GitHub. "
            "Provides ingestion APIs for CI/CD workflows and pull request reviews."
        ),
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers with API v1 prefix
    app.include_router(review_router, prefix=API_V1_PREFIX)
    app.include_router(incident_router, prefix=API_V1_PREFIX)

    @app.get("/", tags=["root"])
    async def root() -> dict[str, str]:
        """Root endpoint."""
        return {
            "service": "code-review-automation",
            "version": "1.0.0",
            "docs": "/docs",
            "api_prefix": API_V1_PREFIX,
        }

    return app


