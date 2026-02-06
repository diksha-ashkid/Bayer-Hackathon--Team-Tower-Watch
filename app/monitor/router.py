"""
Monitor Agent API Router
========================

FastAPI router providing REST endpoints for metrics monitoring.
"""

import logging
from typing import Any
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.monitor.agent import get_monitor_agent, MetricsCollector

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/monitor",
    tags=["monitor-agent"],
)


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class LogEvent(BaseModel):
    """Single log event."""
    id: str = ""
    timestamp: int = 0
    message: str


class CloudWatchLogPayload(BaseModel):
    """CloudWatch log payload."""
    messageType: str = Field(default="DATA_MESSAGE")
    owner: str = Field(default="")
    logGroup: str = Field(default="")
    logStream: str = Field(default="")
    subscriptionFilters: list[str] = Field(default_factory=list)
    logEvents: list[LogEvent]


class MetricsSummary(BaseModel):
    """Metrics summary response."""
    counters: dict[str, int]
    status_codes: dict[str, int]
    predictions: dict[str, int]
    latency_stats: dict[str, Any] | None = None
    request_size_stats: dict[str, Any] | None = None
    confidence_stats: dict[str, Any] | None = None
    last_update: str | None = None
    otel_enabled: bool = False
    endpoint: str | None = None


class ProcessEventRequest(BaseModel):
    """Request to process a single log message."""
    message: str


class ProcessEventResponse(BaseModel):
    """Response from processing a log event."""
    extracted: dict[str, Any]
    timestamp: str


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    service: str
    otel_enabled: bool
    timestamp: str


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Health check endpoint for monitor agent.
    """
    agent = get_monitor_agent()
    return HealthResponse(
        status="healthy",
        service="monitor-agent",
        otel_enabled=agent._otel_initialized,
        timestamp=datetime.now().isoformat()
    )


@router.get("/metrics", response_model=MetricsSummary)
async def get_metrics() -> MetricsSummary:
    """
    Get current metrics summary.
    
    Returns aggregated metrics from all processed log events.
    """
    agent = get_monitor_agent()
    summary = agent.get_summary()
    
    return MetricsSummary(
        counters=summary.get("counters", {}),
        status_codes=summary.get("status_codes", {}),
        predictions=summary.get("predictions", {}),
        latency_stats=summary.get("latency_stats"),
        request_size_stats=summary.get("request_size_stats"),
        confidence_stats=summary.get("confidence_stats"),
        last_update=summary.get("last_update"),
        otel_enabled=summary.get("otel_enabled", False),
        endpoint=summary.get("endpoint"),
    )


@router.post("/metrics/reset")
async def reset_metrics() -> dict[str, str]:
    """
    Reset all metrics to zero.
    """
    agent = get_monitor_agent()
    agent.reset()
    return {"status": "reset", "timestamp": datetime.now().isoformat()}


@router.post("/process/event", response_model=ProcessEventResponse)
async def process_single_event(request: ProcessEventRequest) -> ProcessEventResponse:
    """
    Process a single log message and extract metrics.
    
    Args:
        request: Log message to process
        
    Returns:
        Extracted metrics from the message
    """
    agent = get_monitor_agent()
    extracted = agent.process_event(request.message)
    
    return ProcessEventResponse(
        extracted=extracted,
        timestamp=datetime.now().isoformat()
    )


@router.post("/process/events", response_model=MetricsSummary)
async def process_multiple_events(events: list[LogEvent]) -> MetricsSummary:
    """
    Process multiple log events and return metrics summary.
    
    Args:
        events: List of log events to process
        
    Returns:
        Updated metrics summary
    """
    agent = get_monitor_agent()
    
    event_dicts = [{"message": e.message, "timestamp": e.timestamp, "id": e.id} for e in events]
    agent.process_events(event_dicts)
    
    summary = agent.get_summary()
    
    return MetricsSummary(
        counters=summary.get("counters", {}),
        status_codes=summary.get("status_codes", {}),
        predictions=summary.get("predictions", {}),
        latency_stats=summary.get("latency_stats"),
        request_size_stats=summary.get("request_size_stats"),
        confidence_stats=summary.get("confidence_stats"),
        last_update=summary.get("last_update"),
        otel_enabled=summary.get("otel_enabled", False),
        endpoint=summary.get("endpoint"),
    )


@router.post("/process/cloudwatch", response_model=MetricsSummary)
async def process_cloudwatch_log(payload: CloudWatchLogPayload) -> MetricsSummary:
    """
    Process a CloudWatch log payload and extract metrics.
    
    This endpoint accepts the same format as CloudWatch log subscriptions.
    
    Args:
        payload: CloudWatch log JSON payload
        
    Returns:
        Metrics summary after processing all events
    """
    try:
        agent = get_monitor_agent()
        
        log_data = {
            "messageType": payload.messageType,
            "owner": payload.owner,
            "logGroup": payload.logGroup,
            "logStream": payload.logStream,
            "subscriptionFilters": payload.subscriptionFilters,
            "logEvents": [e.model_dump() for e in payload.logEvents]
        }
        
        logger.info(f"Processing CloudWatch log from {payload.logGroup} with {len(payload.logEvents)} events")
        agent.process_cloudwatch_log(log_data)
        
        summary = agent.get_summary()
        
        return MetricsSummary(
            counters=summary.get("counters", {}),
            status_codes=summary.get("status_codes", {}),
            predictions=summary.get("predictions", {}),
            latency_stats=summary.get("latency_stats"),
            request_size_stats=summary.get("request_size_stats"),
            confidence_stats=summary.get("confidence_stats"),
            last_update=summary.get("last_update"),
            otel_enabled=summary.get("otel_enabled", False),
            endpoint=summary.get("endpoint"),
        )
        
    except Exception as e:
        logger.error(f"Failed to process CloudWatch log: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process log: {str(e)}"
        )


@router.get("/sample-events")
async def get_sample_events() -> dict[str, Any]:
    """
    Get sample log events for testing.
    
    Returns:
        Sample CloudWatch log payload that can be used to test the /process/cloudwatch endpoint
    """
    return {
        "messageType": "DATA_MESSAGE",
        "owner": "123456789012",
        "logGroup": "/aws/service/plant-disease-detector",
        "logStream": "2024/02/06/detector-prod",
        "subscriptionFilters": ["AllLogs"],
        "logEvents": [
            {
                "id": "evt0001",
                "timestamp": 1707220800000,
                "message": "INFO request_id=req-001 bytes=1024000 stage=start"
            },
            {
                "id": "evt0002",
                "timestamp": 1707220801000,
                "message": "INFO request_id=req-001 latency_ms=150 confidence=0.95 prediction=healthy"
            },
            {
                "id": "evt0003",
                "timestamp": 1707220802000,
                "message": "INFO request_id=req-001 status=200 gpu_queue_depth=3"
            },
            {
                "id": "evt0004",
                "timestamp": 1707220810000,
                "message": "ERROR request_id=req-002 TimeoutError: inference timeout latency_ms=5000"
            },
            {
                "id": "evt0005",
                "timestamp": 1707220815000,
                "message": "ERROR request_id=req-003 CUDA out of memory"
            },
            {
                "id": "evt0006",
                "timestamp": 1707220820000,
                "message": "WARN request_id=req-004 image too large bytes=15000000"
            },
            {
                "id": "evt0007",
                "timestamp": 1707220825000,
                "message": "INFO request_id=req-005 latency_ms=200 confidence=0.87 prediction=rust status=200"
            }
        ]
    }
