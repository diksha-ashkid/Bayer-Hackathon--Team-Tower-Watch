"""
Tool Definitions for Orchestrator Sub-Agents

This module defines tool wrappers for each sub-agent that the orchestrator
can invoke. Each tool provides a consistent interface with:
- Input schema validation
- Async execution
- Structured output
- Error handling

These tools are registered with LangGraph and can be invoked by the
orchestrator based on its routing decisions.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from app.deployment.incident_commander.shared.state import (
    AgentResult,
    AgentType,
    InvestigationState,
)
from app.deployment.incident_commander.agents.log_agent import LogAgent
from app.deployment.incident_commander.agents.metrics_agent import MetricsAgent
from app.deployment.incident_commander.agents.deploy_agent import DeployAgent
from app.deployment.incident_commander.shared.bedrock_client import BedrockClient
from app.deployment.incident_commander.shared.redis_client import RedisMemory

logger = logging.getLogger(__name__)


@dataclass
class ToolInput:
    """
    Standard input for all agent tools.
    
    Attributes:
        log_events: List of log event dictionaries.
        incident_id: Unique incident identifier.
        context: Optional additional context for the agent.
    """
    log_events: list[dict[str, Any]]
    incident_id: str
    context: Optional[dict[str, Any]] = None


@dataclass
class ToolOutput:
    """
    Standard output from all agent tools.
    
    Attributes:
        result: The AgentResult from the agent.
        state_updates: Dictionary of state fields to update.
    """
    result: AgentResult
    state_updates: dict[str, Any]


class AgentTool(ABC):
    """
    Abstract base class for agent tools.
    
    Each tool wraps a specialized agent and provides:
    - Consistent interface for the orchestrator
    - Input validation
    - Error handling
    - State update formatting
    
    Tools are registered with LangGraph and can be invoked
    dynamically based on orchestrator decisions.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name for registration and invocation."""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description for LLM understanding."""
        pass
    
    @property
    @abstractmethod
    def agent_type(self) -> AgentType:
        """Type of agent this tool wraps."""
        pass
    
    @abstractmethod
    async def invoke(self, input: ToolInput) -> ToolOutput:
        """
        Invoke the wrapped agent.
        
        Args:
            input: Standard tool input.
            
        Returns:
            Tool output with results and state updates.
        """
        pass
    
    def to_langchain_tool_schema(self) -> dict[str, Any]:
        """
        Convert to LangChain/LangGraph tool schema format.
        
        Returns:
            Tool schema dictionary for registration.
        """
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "incident_id": {
                        "type": "string",
                        "description": "Unique identifier for the incident",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for invoking this agent",
                    },
                },
                "required": ["incident_id", "reason"],
            },
        }


class LogAgentTool(AgentTool):
    """
    Tool wrapper for the Log Analysis Agent.
    
    Invokes the log agent to analyze log events for:
    - Error tracebacks and stack traces
    - Error patterns and categories
    - File locations and line numbers
    - Request ID correlation
    
    Use this tool when you observe:
    - ERROR or WARN log levels
    - Exception messages or tracebacks
    - Application failures or crashes
    """
    
    def __init__(
        self,
        bedrock_client: BedrockClient,
        redis_memory: RedisMemory,
    ):
        """
        Initialize the log agent tool.
        
        Args:
            bedrock_client: Bedrock client for LLM calls.
            redis_memory: Redis client for storage.
        """
        self._agent = LogAgent(bedrock_client, redis_memory)
    
    @property
    def name(self) -> str:
        return "log_agent"
    
    @property
    def description(self) -> str:
        return (
            "Analyzes log events for errors, tracebacks, and patterns. "
            "Use when you see ERROR/WARN log levels, exceptions, or need "
            "to understand application failures. Returns error types, "
            "file locations, and error patterns."
        )
    
    @property
    def agent_type(self) -> AgentType:
        return AgentType.LOG_AGENT
    
    async def invoke(self, input: ToolInput) -> ToolOutput:
        """
        Invoke the log agent for analysis.
        
        Args:
            input: Tool input with log events and incident ID.
            
        Returns:
            Tool output with log findings.
        """
        logger.info(f"LogAgentTool invoked for incident {input.incident_id}")
        
        try:
            result = await self._agent.analyze(
                log_events=input.log_events,
                incident_id=input.incident_id,
            )
            
            return ToolOutput(
                result=result,
                state_updates={
                    "log_agent_result": result,
                    "log_findings": result.findings,
                },
            )
            
        except Exception as e:
            logger.error(f"LogAgentTool failed: {e}")
            error_result = AgentResult(
                agent_type=AgentType.LOG_AGENT,
                success=False,
                execution_time_ms=0,
                findings=[],
                summary="Log analysis failed",
                error=str(e),
            )
            return ToolOutput(
                result=error_result,
                state_updates={"log_agent_result": error_result},
            )


class MetricsAgentTool(AgentTool):
    """
    Tool wrapper for the Metrics Analysis Agent.
    
    Invokes the metrics agent to analyze log events for:
    - Latency anomalies and spikes
    - CPU and memory utilization
    - Error rates and throughput
    - Statistical outliers
    
    Use this tool when you observe:
    - Performance degradation symptoms
    - Timeout errors or slow responses
    - Resource exhaustion indicators
    """
    
    def __init__(
        self,
        bedrock_client: BedrockClient,
        redis_memory: RedisMemory,
    ):
        """
        Initialize the metrics agent tool.
        
        Args:
            bedrock_client: Bedrock client for LLM calls.
            redis_memory: Redis client for storage.
        """
        self._agent = MetricsAgent(bedrock_client, redis_memory)
    
    @property
    def name(self) -> str:
        return "metrics_agent"
    
    @property
    def description(self) -> str:
        return (
            "Analyzes performance metrics for anomalies. Use when you "
            "suspect latency issues, resource exhaustion, or performance "
            "degradation. Returns metric anomalies, threshold breaches, "
            "and statistical outliers."
        )
    
    @property
    def agent_type(self) -> AgentType:
        return AgentType.METRICS_AGENT
    
    async def invoke(self, input: ToolInput) -> ToolOutput:
        """
        Invoke the metrics agent for analysis.
        
        Args:
            input: Tool input with log events and incident ID.
            
        Returns:
            Tool output with metric anomalies.
        """
        logger.info(f"MetricsAgentTool invoked for incident {input.incident_id}")
        
        try:
            result = await self._agent.analyze(
                log_events=input.log_events,
                incident_id=input.incident_id,
            )
            
            return ToolOutput(
                result=result,
                state_updates={
                    "metrics_agent_result": result,
                    "metric_anomalies": result.findings,
                },
            )
            
        except Exception as e:
            logger.error(f"MetricsAgentTool failed: {e}")
            error_result = AgentResult(
                agent_type=AgentType.METRICS_AGENT,
                success=False,
                execution_time_ms=0,
                findings=[],
                summary="Metrics analysis failed",
                error=str(e),
            )
            return ToolOutput(
                result=error_result,
                state_updates={"metrics_agent_result": error_result},
            )


class DeployAgentTool(AgentTool):
    """
    Tool wrapper for the Deploy Intelligence Agent.
    
    Invokes the deploy agent to analyze:
    - Recent deployment timeline
    - Configuration changes
    - Correlation with incident timing
    - Rollback recommendations
    
    Use this tool when you need to:
    - Check for recent deployments before the incident
    - Identify configuration changes that may be the cause
    - Determine if rollback is recommended
    """
    
    def __init__(
        self,
        bedrock_client: BedrockClient,
        redis_memory: RedisMemory,
    ):
        """
        Initialize the deploy agent tool.
        
        Args:
            bedrock_client: Bedrock client for LLM calls.
            redis_memory: Redis client for storage.
        """
        self._agent = DeployAgent(bedrock_client, redis_memory)
    
    @property
    def name(self) -> str:
        return "deploy_agent"
    
    @property
    def description(self) -> str:
        return (
            "Correlates incidents with recent deployments. Use when you "
            "need to check if recent code or config deployments may have "
            "caused the incident. Returns deployment timeline, suspect "
            "deployments, and rollback recommendations."
        )
    
    @property
    def agent_type(self) -> AgentType:
        return AgentType.DEPLOY_AGENT
    
    async def invoke(self, input: ToolInput) -> ToolOutput:
        """
        Invoke the deploy agent for analysis.
        
        Args:
            input: Tool input with log events and incident ID.
            
        Returns:
            Tool output with deployment events.
        """
        logger.info(f"DeployAgentTool invoked for incident {input.incident_id}")
        
        try:
            result = await self._agent.analyze(
                log_events=input.log_events,
                incident_id=input.incident_id,
            )
            
            return ToolOutput(
                result=result,
                state_updates={
                    "deploy_agent_result": result,
                    "deployment_events": result.findings,
                },
            )
            
        except Exception as e:
            logger.error(f"DeployAgentTool failed: {e}")
            error_result = AgentResult(
                agent_type=AgentType.DEPLOY_AGENT,
                success=False,
                execution_time_ms=0,
                findings=[],
                summary="Deployment analysis failed",
                error=str(e),
            )
            return ToolOutput(
                result=error_result,
                state_updates={"deploy_agent_result": error_result},
            )


class ToolRegistry:
    """
    Registry for managing available agent tools.
    
    Provides a centralized way to access and invoke tools by name,
    making it easy for the orchestrator to route to the appropriate
    agent based on its decisions.
    """
    
    def __init__(
        self,
        bedrock_client: BedrockClient,
        redis_memory: RedisMemory,
    ):
        """
        Initialize the tool registry with all available tools.
        
        Args:
            bedrock_client: Bedrock client for LLM calls.
            redis_memory: Redis client for storage.
        """
        self._tools: dict[str, AgentTool] = {
            "log_agent": LogAgentTool(bedrock_client, redis_memory),
            "metrics_agent": MetricsAgentTool(bedrock_client, redis_memory),
            "deploy_agent": DeployAgentTool(bedrock_client, redis_memory),
        }
    
    def get_tool(self, name: str) -> Optional[AgentTool]:
        """
        Get a tool by name.
        
        Args:
            name: Tool name.
            
        Returns:
            Tool instance or None if not found.
        """
        return self._tools.get(name)
    
    def list_tools(self) -> list[str]:
        """Get list of available tool names."""
        return list(self._tools.keys())
    
    def get_all_schemas(self) -> list[dict[str, Any]]:
        """Get schemas for all registered tools."""
        return [tool.to_langchain_tool_schema() for tool in self._tools.values()]
    
    async def invoke(
        self,
        tool_name: str,
        input: ToolInput,
    ) -> Optional[ToolOutput]:
        """
        Invoke a tool by name.
        
        Args:
            tool_name: Name of the tool to invoke.
            input: Tool input.
            
        Returns:
            Tool output or None if tool not found.
        """
        tool = self.get_tool(tool_name)
        if tool:
            return await tool.invoke(input)
        logger.warning(f"Tool not found: {tool_name}")
        return None
