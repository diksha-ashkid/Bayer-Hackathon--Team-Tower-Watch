#!/usr/bin/env python
"""
Multi-Agent Autonomous Incident Commander - Main Entry Point

This module provides the main entry point for the incident investigation
system. It can be run standalone for testing or imported for integration.

Usage:
    # CLI execution
    python -m app.deployment.incident_commander.main
    
    # Python import
    from app.deployment.incident_commander import run_investigation
    result = await run_investigation(cloudwatch_log_json)

Sprint 3 Success Criteria:
- Orchestrator correctly routes to relevant agents
- Log agent extracts tracebacks from errors
- Metrics agent identifies latency anomalies
- Deploy agent provides deployment timeline
- Final report synthesizes all findings
- Rollback recommendation generated for config issues
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Optional, cast

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

# OpenTelemetry setup (optional - only if installed)
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
    
    # Set up tracing
    provider = TracerProvider()
    processor = SimpleSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    
    OTEL_ENABLED = True
except ImportError:
    OTEL_ENABLED = False
    logger.warning("OpenTelemetry not installed. Tracing disabled.")


# =============================================================================
# Sample CloudWatch Log Data (Sprint 3 Scenario)
# =============================================================================

SAMPLE_CLOUDWATCH_LOG = {
    "messageType": "DATA_MESSAGE",
    "owner": "123456789012",
    "logGroup": "/aws/ecs/checkout-service",
    "logStream": "2026/02/06/checkout-service/abc123",
    "subscriptionFilters": ["AllLogs"],
    "logEvents": [
        {
            "id": "evt0001",
            "timestamp": 1760004600000,
            "message": "INFO request_id=req-0001 endpoint=/checkout stage=start latency=50ms"
        },
        {
            "id": "evt0002",
            "timestamp": 1760004601000,
            "message": "INFO request_id=req-0002 endpoint=/checkout stage=start latency=75ms"
        },
        {
            "id": "evt0003",
            "timestamp": 1760004602000,
            "message": "WARN request_id=req-0003 endpoint=/checkout stage=db_query latency=800ms query_time=780ms connection_pool=45/50"
        },
        {
            "id": "evt0004",
            "timestamp": 1760004603000,
            "message": "ERROR request_id=req-0004 endpoint=/checkout stage=db_query database connection timeout after 5000ms"
        },
        {
            "id": "evt0005",
            "timestamp": 1760004604000,
            "message": "ERROR request_id=req-0005 endpoint=/checkout stage=db_query Traceback (most recent call last):\n  File \"/app/services/checkout.py\", line 142, in process_order\n    result = await db.execute(query)\n  File \"/app/db/client.py\", line 89, in execute\n    conn = await self.pool.acquire(timeout=5.0)\nTimeoutError: connection acquisition timed out"
        },
        {
            "id": "evt0006",
            "timestamp": 1760004605000,
            "message": "ERROR request_id=req-0006 endpoint=/checkout latency=5200ms stage=failed error_code=DB_TIMEOUT"
        },
        {
            "id": "evt0007",
            "timestamp": 1760004606000,
            "message": "CRITICAL request_id=req-0007 endpoint=/checkout service_degraded=true error_rate=45% latency=5500ms active_connections=100/100"
        },
        {
            "id": "evt0008",
            "timestamp": 1760004607000,
            "message": "ERROR request_id=req-0008 endpoint=/checkout Traceback (most recent call last):\n  File \"/app/handlers/api.py\", line 56, in checkout_handler\n    order = await checkout_service.process(cart)\n  File \"/app/services/checkout.py\", line 145, in process\n    raise ServiceUnavailableError('Database connection pool exhausted')\nServiceUnavailableError: Database connection pool exhausted"
        },
        {
            "id": "evt0009",
            "timestamp": 1760004608000,
            "message": "WARN request_id=req-0009 circuit_breaker=checkout_db state=OPEN failures=50 threshold=10"
        },
        {
            "id": "evt0010",
            "timestamp": 1760004609000,
            "message": "INFO alerting incident_created=true severity=critical service=checkout-service metric=latency value=2000ms threshold=500ms"
        },
    ]
}


# =============================================================================
# Main Investigation Function
# =============================================================================

async def run_investigation(
    cloudwatch_log: dict[str, Any],
    incident_id: Optional[str] = None,
    use_langgraph: bool = True,
) -> dict[str, Any]:
    """
    Run an incident investigation on CloudWatch log data.
    
    This is the main entry point for the incident commander system.
    It initializes all components and orchestrates the investigation.
    
    Args:
        cloudwatch_log: CloudWatch log JSON data.
        incident_id: Optional incident ID (auto-generated if not provided).
        use_langgraph: Whether to use LangGraph workflow (True) or direct orchestrator (False).
        
    Returns:
        Investigation results dictionary containing:
        - incident_id: Unique incident identifier
        - status: Investigation status
        - root_cause: Identified root cause
        - recommendations: List of actionable recommendations
        - report: Full investigation report
        
    Example:
        result = await run_investigation(cloudwatch_log_json)
        print(f"Root Cause: {result['root_cause']}")
        print(f"Recommendations: {result['recommendations']}")
    """
    from app.deployment.incident_commander.shared.bedrock_client import BedrockClient
    from app.deployment.incident_commander.shared.redis_client import RedisMemory
    from app.deployment.incident_commander.shared.state import InvestigationState, CloudWatchLog
    from app.deployment.incident_commander.orchestrator.agent import OrchestratorAgent
    from app.deployment.incident_commander.graph import (
        run_investigation_workflow,
        create_initial_state,
    )
    
    logger.info("=" * 60)
    logger.info("INCIDENT COMMANDER - Starting Investigation")
    logger.info("=" * 60)
    
    # Initialize clients
    bedrock_client = BedrockClient()
    redis_memory = RedisMemory()
    
    # Log input summary
    log_group = cloudwatch_log.get("logGroup", "Unknown")
    event_count = len(cloudwatch_log.get("logEvents", []))
    logger.info(f"Log Group: {log_group}")
    logger.info(f"Event Count: {event_count}")
    
    try:
        if use_langgraph:
            # Use LangGraph workflow
            logger.info("Using LangGraph workflow")
            result = await run_investigation_workflow(
                log_data=cast(CloudWatchLog, cloudwatch_log),
                bedrock_client=bedrock_client,
                redis_memory=redis_memory,
                incident_id=incident_id,
            )
            
            # Extract report from result
            report = result.get("final_report", {})
            
            return {
                "incident_id": report.get("incident_id", incident_id),
                "status": report.get("status", "completed"),
                "root_cause": report.get("root_cause", "Unable to determine"),
                "severity": report.get("severity", "medium"),
                "recommendations": report.get("recommendations", []),
                "affected_services": report.get("affected_services", []),
                "report": report,
                "investigation_steps": report.get("investigation_steps", []),
            }
            
        else:
            # Use direct orchestrator
            logger.info("Using direct orchestrator")
            orchestrator = OrchestratorAgent(bedrock_client, redis_memory)
            
            initial_state = InvestigationState.from_cloudwatch_log(cast(CloudWatchLog, cloudwatch_log))
            if incident_id:
                initial_state = initial_state.__class__(
                    incident_id=incident_id,
                    **{k: v for k, v in initial_state.to_dict().items() if k != "incident_id"}
                )
            
            final_state, report = await orchestrator.investigate(initial_state)
            
            return {
                "incident_id": report.incident_id,
                "status": report.status.value,
                "root_cause": report.root_cause,
                "severity": report.severity.value,
                "recommendations": report.recommendations,
                "affected_services": report.affected_services,
                "report": report.to_dict(),
                "investigation_steps": [s.to_dict() for s in report.investigation_steps],
            }
            
    except Exception as e:
        logger.error(f"Investigation failed: {e}")
        import traceback
        traceback.print_exc()
        
        return {
            "incident_id": incident_id or "unknown",
            "status": "failed",
            "root_cause": f"Investigation failed: {str(e)}",
            "severity": "unknown",
            "recommendations": ["Re-run investigation after fixing errors"],
            "affected_services": [],
            "report": None,
            "investigation_steps": [],
            "error": str(e),
        }
    
    finally:
        # Cleanup
        redis_memory.close()


def print_investigation_report(result: dict[str, Any]) -> None:
    """
    Print a formatted investigation report to console.
    
    Args:
        result: Investigation result dictionary.
    """
    print("\n" + "=" * 80)
    print("                    INCIDENT INVESTIGATION REPORT")
    print("=" * 80)
    
    print(f"\nIncident ID: {result.get('incident_id', 'N/A')}")
    print(f"Status: {result.get('status', 'N/A')}")
    print(f"Severity: {result.get('severity', 'N/A').upper()}")
    
    print("\n" + "-" * 40)
    print("ROOT CAUSE")
    print("-" * 40)
    print(f"\n{result.get('root_cause', 'Unable to determine')}")
    
    if result.get('affected_services'):
        print("\n" + "-" * 40)
        print("AFFECTED SERVICES")
        print("-" * 40)
        for service in result['affected_services']:
            print(f"  - {service}")
    
    if result.get('recommendations'):
        print("\n" + "-" * 40)
        print("RECOMMENDATIONS")
        print("-" * 40)
        for i, rec in enumerate(result['recommendations'], 1):
            print(f"  {i}. {rec}")
    
    if result.get('investigation_steps'):
        print("\n" + "-" * 40)
        print("INVESTIGATION TIMELINE")
        print("-" * 40)
        for step in result['investigation_steps']:
            agent = step.get('agent_type', 'unknown')
            action = step.get('action', step.get('observation', 'N/A'))
            print(f"  [{agent}] {action[:100]}...")
    
    print("\n" + "=" * 80)
    print("                         END OF REPORT")
    print("=" * 80 + "\n")


async def main():
    """
    Main entry point for CLI execution.
    
    Runs the investigation on the sample CloudWatch log data
    that simulates the Sprint 3 scenario (checkout service latency spike).
    """
    print("\n" + "=" * 80)
    print("       MULTI-AGENT AUTONOMOUS INCIDENT COMMANDER")
    print("                    Sprint 3 Demo")
    print("=" * 80)
    
    print("\nScenario: Checkout Service Latency Spike to 2000ms")
    print("Expected Investigation Flow:")
    print("  1. Orchestrator receives latency anomaly alert")
    print("  2. Routes to Metrics Agent -> detects DB connection timeouts")
    print("  3. Routes to Deploy Intelligence Agent -> finds config deployment 15min prior")
    print("  4. Routes to Log Agent -> extracts relevant error traces")
    print("  5. Orchestrator synthesizes findings into investigation report")
    print("  6. Recommends immediate rollback")
    
    print("\n" + "-" * 40)
    print("Starting Investigation...")
    print("-" * 40 + "\n")
    
    # Run investigation with sample data
    result = await run_investigation(
        cloudwatch_log=SAMPLE_CLOUDWATCH_LOG,
        incident_id=f"INC-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        use_langgraph=True,
    )
    
    # Print formatted report
    print_investigation_report(result)
    
    # Also save JSON result
    output_file = "investigation_result.json"
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\nFull results saved to: {output_file}")
    
    return result


if __name__ == "__main__":
    # Run the async main function
    result = asyncio.run(main())
    
    # Exit with appropriate code
    if result.get("status") == "failed":
        sys.exit(1)
    sys.exit(0)
