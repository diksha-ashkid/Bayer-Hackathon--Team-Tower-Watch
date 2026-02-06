"""
Orchestrator Module for Incident Investigation

This module contains the ReAct-based orchestrator agent that:
- Creates investigation plans from CloudWatch logs
- Coordinates sub-agents based on analysis needs
- Makes intelligent routing decisions
- Synthesizes findings into investigation reports
"""

from app.deployment.incident_commander.orchestrator.agent import OrchestratorAgent
from app.deployment.incident_commander.orchestrator.prompts import (
    OrchestratorPrompts,
    SYSTEM_PROMPT,
    PLANNING_PROMPT,
    ROUTING_PROMPT,
    SYNTHESIS_PROMPT,
)
from app.deployment.incident_commander.orchestrator.tools import (
    AgentTool,
    LogAgentTool,
    MetricsAgentTool,
    DeployAgentTool,
)

__all__ = [
    "OrchestratorAgent",
    "OrchestratorPrompts",
    "SYSTEM_PROMPT",
    "PLANNING_PROMPT",
    "ROUTING_PROMPT",
    "SYNTHESIS_PROMPT",
    "AgentTool",
    "LogAgentTool",
    "MetricsAgentTool",
    "DeployAgentTool",
]
