"""
Incident Commander API Router
==============================

FastAPI router providing REST endpoints for incident investigation.
"""

import logging
from typing import Any
from datetime import datetime

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from app.incident_commander.commander import (
    run_incident_investigation,
    SAMPLE_ALERT,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/incident",
    tags=["incident-commander"],
)


def normalize_investigation_plan(plan: Any) -> str:
    """Convert investigation_plan to string, handling Bedrock content blocks."""
    if isinstance(plan, list):
        # Extract text from content blocks (Bedrock response format)
        return " ".join(
            block.get("text", str(block)) if isinstance(block, dict) else str(block)
            for block in plan
        )
    elif isinstance(plan, str):
        return plan
    else:
        return str(plan) if plan else ""


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class CloudWatchLogEvent(BaseModel):
    """Single CloudWatch log event."""
    id: str
    timestamp: int
    message: str


class CloudWatchAlert(BaseModel):
    """CloudWatch alert input schema."""
    messageType: str = Field(default="DATA_MESSAGE")
    owner: str = Field(default="123456789012")
    logGroup: str
    logStream: str
    subscriptionFilters: list[str] = Field(default_factory=list)
    logEvents: list[CloudWatchLogEvent]


class InvestigationResponse(BaseModel):
    """Investigation result response."""
    incident_id: str
    status: str
    root_cause: str
    recommendations: list[str]
    log_analysis: dict[str, Any]
    metrics_analysis: dict[str, Any]
    deploy_timeline: dict[str, Any]
    investigation_plan: str


class InvestigationReportResponse(BaseModel):
    """Full investigation report response."""
    incident_id: str
    status: str
    final_report: str


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    service: str
    timestamp: str


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Health check endpoint for incident commander service.
    """
    return HealthResponse(
        status="healthy",
        service="incident-commander",
        timestamp=datetime.now().isoformat()
    )


@router.post("/investigate", response_model=InvestigationResponse)
async def investigate_incident(alert: CloudWatchAlert) -> InvestigationResponse:
    """
    Trigger an incident investigation based on CloudWatch alert.
    
    This endpoint accepts a CloudWatch log alert and runs the multi-agent
    investigation workflow to identify root cause and recommendations.
    
    Args:
        alert: CloudWatch alert containing log events
        
    Returns:
        Investigation results including root cause and recommendations
    """
    try:
        logger.info(f"Starting investigation for log group: {alert.logGroup}")
        
        # Convert Pydantic model to dict
        alert_dict = {
            "messageType": alert.messageType,
            "owner": alert.owner,
            "logGroup": alert.logGroup,
            "logStream": alert.logStream,
            "subscriptionFilters": alert.subscriptionFilters,
            "logEvents": [event.model_dump() for event in alert.logEvents]
        }
        
        # Run investigation
        result = run_incident_investigation(alert_dict)
        
        return InvestigationResponse(
            incident_id=result.get("incident_id", ""),
            status=result.get("status", "completed"),
            root_cause=result.get("root_cause", ""),
            recommendations=result.get("recommendations", []),
            log_analysis=result.get("log_analysis", {}),
            metrics_analysis=result.get("metrics_analysis", {}),
            deploy_timeline=result.get("deploy_timeline", {}),
            investigation_plan=normalize_investigation_plan(result.get("investigation_plan", ""))
        )
        
    except Exception as e:
        logger.error(f"Investigation failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Investigation failed: {str(e)}"
        )


@router.post("/investigate/report", response_model=InvestigationReportResponse)
async def investigate_with_report(alert: CloudWatchAlert) -> InvestigationReportResponse:
    """
    Trigger investigation and return the full markdown report.
    
    Args:
        alert: CloudWatch alert containing log events
        
    Returns:
        Full investigation report in markdown format
    """
    try:
        logger.info(f"Starting investigation with full report for: {alert.logGroup}")
        
        alert_dict = {
            "messageType": alert.messageType,
            "owner": alert.owner,
            "logGroup": alert.logGroup,
            "logStream": alert.logStream,
            "subscriptionFilters": alert.subscriptionFilters,
            "logEvents": [event.model_dump() for event in alert.logEvents]
        }
        
        result = run_incident_investigation(alert_dict)
        
        return InvestigationReportResponse(
            incident_id=result.get("incident_id", ""),
            status=result.get("status", "completed"),
            final_report=result.get("final_report", "")
        )
        
    except Exception as e:
        logger.error(f"Investigation failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Investigation failed: {str(e)}"
        )


@router.post("/investigate/demo", response_model=InvestigationResponse)
async def run_demo_investigation() -> InvestigationResponse:
    """
    Run a demo investigation using the sample latency spike scenario.
    
    This endpoint uses pre-configured sample data to demonstrate
    the incident commander's capabilities.
    
    Returns:
        Investigation results for the demo scenario
    """
    try:
        logger.info("Running demo investigation with sample alert")
        
        result = run_incident_investigation(SAMPLE_ALERT)
        
        return InvestigationResponse(
            incident_id=result.get("incident_id", ""),
            status=result.get("status", "completed"),
            root_cause=result.get("root_cause", ""),
            recommendations=result.get("recommendations", []),
            log_analysis=result.get("log_analysis", {}),
            metrics_analysis=result.get("metrics_analysis", {}),
            deploy_timeline=result.get("deploy_timeline", {}),
            investigation_plan=normalize_investigation_plan(result.get("investigation_plan", ""))
        )
        
    except Exception as e:
        logger.error(f"Demo investigation failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Demo investigation failed: {str(e)}"
        )


@router.get("/sample-alert")
async def get_sample_alert() -> dict[str, Any]:
    """
    Get the sample CloudWatch alert used for demos.
    
    Returns:
        Sample CloudWatch alert JSON that can be used to test the /investigate endpoint
    """
    return SAMPLE_ALERT
