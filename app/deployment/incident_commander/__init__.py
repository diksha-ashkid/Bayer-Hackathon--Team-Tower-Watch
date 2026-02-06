"""
Multi-Agent Autonomous Incident Commander

A sophisticated incident investigation system that uses LangGraph and Amazon Bedrock
(Nova Pro v1) to coordinate multiple specialized agents for CloudWatch log analysis.

Components:
- Orchestrator Agent: ReAct-based coordinator that routes investigations
- Log Agent: Extracts tracebacks, error patterns, and stores in Redis
- Metrics Agent: Analyzes performance metrics with OpenTelemetry
- Deploy Agent: Correlates incidents with CI/CD deployments

Usage:
    from app.deployment.incident_commander import run_investigation
    
    result = await run_investigation(cloudwatch_log_json)
"""

from app.deployment.incident_commander.main import run_investigation
from app.deployment.incident_commander.graph import create_investigation_graph

__all__ = ["run_investigation", "create_investigation_graph"]
