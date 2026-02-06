"""
LangGraph Workflow Definition for Incident Investigation

This module defines the LangGraph workflow that orchestrates the
multi-agent incident investigation system.

Workflow Architecture:
1. Input Node: Receives CloudWatch log JSON
2. Planning Node: Creates investigation plan
3. Router Node: Decides which agent to invoke
4. Agent Nodes: Execute specialized analysis
5. Synthesis Node: Creates final report
6. Output Node: Returns investigation results

The workflow uses conditional edges based on orchestrator decisions
and supports checkpointing for resumable investigations.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime
from typing import Any, Literal, TypedDict, Annotated, Sequence, Optional
import operator

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from app.deployment.incident_commander.shared.state import (
    InvestigationState,
    InvestigationStatus,
    InvestigationReport,
    AgentType,
    CloudWatchLog,
    LogFinding,
    MetricAnomaly,
    DeploymentEvent,
    AgentResult,
)
from app.deployment.incident_commander.shared.bedrock_client import BedrockClient
from app.deployment.incident_commander.shared.redis_client import RedisMemory
from app.deployment.incident_commander.orchestrator.agent import OrchestratorAgent
from app.deployment.incident_commander.orchestrator.tools import (
    ToolRegistry,
    ToolInput,
)
from app.deployment.incident_commander.orchestrator.prompts import (
    OrchestratorPrompts,
    SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)


# =============================================================================
# LangGraph State Definition
# =============================================================================

class GraphState(TypedDict):
    """
    LangGraph state schema for the investigation workflow.
    
    This TypedDict defines the shape of state that flows through
    the graph. Each node can read and update these fields.
    
    Note: LangGraph requires specific patterns for state updates:
    - Simple fields are overwritten
    - Sequence fields use operator.add for appending
    """
    # Investigation metadata
    incident_id: str
    status: str
    created_at: str
    
    # Input data
    raw_input: dict[str, Any]
    log_group: str
    log_stream: str
    log_events: list[dict[str, Any]]
    
    # Orchestrator state
    current_thought: str
    current_plan: list[str]
    next_agent: str
    iteration_count: int
    max_iterations: int
    
    # Agent results (stored as dicts for serialization)
    log_agent_result: Optional[dict[str, Any]]
    metrics_agent_result: Optional[dict[str, Any]]
    deploy_agent_result: Optional[dict[str, Any]]
    
    # Findings (accumulated with reducer)
    log_findings: Annotated[Sequence[dict[str, Any]], operator.add]
    metric_anomalies: Annotated[Sequence[dict[str, Any]], operator.add]
    deployment_events: Annotated[Sequence[dict[str, Any]], operator.add]
    
    # Investigation history
    investigation_steps: Annotated[Sequence[dict[str, Any]], operator.add]
    messages: Annotated[Sequence[dict[str, str]], operator.add]
    
    # Output
    final_report: Optional[dict[str, Any]]
    error: Optional[str]


def create_initial_state(log_data: CloudWatchLog, incident_id: Optional[str] = None) -> GraphState:
    """
    Create initial graph state from CloudWatch log input.
    
    Args:
        log_data: Parsed CloudWatch log JSON.
        incident_id: Optional incident ID (auto-generated if not provided).
        
    Returns:
        Initial GraphState for the workflow.
    """
    import uuid
    
    # Cast TypedDict fields to dict[str, Any] for GraphState compatibility
    log_events: list[dict[str, Any]] = list(log_data.get("logEvents", []))  # type: ignore[arg-type]
    
    return GraphState(
        incident_id=incident_id or str(uuid.uuid4()),
        status=InvestigationStatus.PENDING.value,
        created_at=datetime.utcnow().isoformat(),
        raw_input=dict(log_data),
        log_group=log_data.get("logGroup", ""),
        log_stream=log_data.get("logStream", ""),
        log_events=log_events,
        current_thought="",
        current_plan=[],
        next_agent="",
        iteration_count=0,
        max_iterations=10,
        log_agent_result=None,
        metrics_agent_result=None,
        deploy_agent_result=None,
        log_findings=[],
        metric_anomalies=[],
        deployment_events=[],
        investigation_steps=[],
        messages=[],
        final_report=None,
        error=None,
    )


# =============================================================================
# Graph Node Functions
# =============================================================================

def create_investigation_graph(
    bedrock_client: BedrockClient,
    redis_memory: RedisMemory,
    enable_checkpointing: bool = True,
) -> StateGraph:
    """
    Create the LangGraph workflow for incident investigation.
    
    This function builds a state graph with:
    - Input processing node
    - Planning node
    - Agent routing node
    - Individual agent nodes
    - Synthesis node
    - Conditional edges for routing
    
    Args:
        bedrock_client: AWS Bedrock client for LLM calls.
        redis_memory: Redis client for state storage.
        enable_checkpointing: Whether to enable state checkpointing.
        
    Returns:
        Compiled LangGraph StateGraph.
    """
    # Initialize tools and orchestrator
    tools = ToolRegistry(bedrock_client, redis_memory)
    orchestrator = OrchestratorAgent(bedrock_client, redis_memory)
    
    # Create the graph with state schema
    graph = StateGraph(GraphState)
    
    # =================================================================
    # Node: Input Processing
    # =================================================================
    def input_node(state: GraphState) -> dict[str, Any]:
        """
        Process and validate input data.
        
        Updates:
        - status: Sets to IN_PROGRESS
        - messages: Adds start message
        """
        logger.info(f"Starting investigation {state['incident_id']}")
        
        return {
            "status": InvestigationStatus.IN_PROGRESS.value,
            "messages": [
                {
                    "role": "system",
                    "content": f"Investigation started for incident {state['incident_id']}"
                }
            ],
        }
    
    # =================================================================
    # Node: Planning
    # =================================================================
    def planning_node(state: GraphState) -> dict[str, Any]:
        """
        Create the investigation plan using LLM.
        
        Updates:
        - current_thought: Initial hypothesis
        - current_plan: Ordered list of investigation steps
        - messages: Adds planning message
        """
        logger.info("Creating investigation plan")
        
        # Build planning prompt
        samples = state["log_events"][:10]
        sample_text = "\n".join([
            f"[{e.get('timestamp', 'N/A')}] {e.get('message', '')[:200]}"
            for e in samples
        ])
        
        try:
            result = bedrock_client.invoke_with_json_output(
                prompt=f"""Analyze these CloudWatch log events and create an investigation plan.

Log Group: {state['log_group']}
Event Count: {len(state['log_events'])}

Sample Events:
{sample_text}

Create a JSON plan with investigation_steps (array of agent names in order: log_agent, metrics_agent, deploy_agent), 
initial_hypothesis, and severity_assessment.""",
                system_prompt=SYSTEM_PROMPT,
                max_tokens=1000,
            )
            
            steps = result.get("investigation_steps", [])
            plan = [s.get("agent", "log_agent") if isinstance(s, dict) else s for s in steps]
            
            return {
                "current_thought": result.get("initial_hypothesis", "Analyzing incident"),
                "current_plan": plan or ["log_agent", "metrics_agent", "deploy_agent"],
                "messages": [
                    {
                        "role": "assistant",
                        "content": f"Plan created: {result.get('initial_hypothesis', 'Starting analysis')}"
                    }
                ],
            }
            
        except Exception as e:
            logger.warning(f"Planning failed, using defaults: {e}")
            return {
                "current_thought": "Using default investigation plan",
                "current_plan": ["log_agent", "metrics_agent", "deploy_agent"],
                "messages": [
                    {
                        "role": "assistant",
                        "content": "Using default investigation plan"
                    }
                ],
            }
    
    # =================================================================
    # Node: Router (ReAct Decision)
    # =================================================================
    def router_node(state: GraphState) -> dict[str, Any]:
        """
        Decide which agent to invoke next (ReAct reasoning).
        
        Updates:
        - next_agent: The selected agent or "synthesize"
        - current_thought: Reasoning for the decision
        - iteration_count: Incremented
        - messages: Adds routing message
        """
        iteration = state["iteration_count"] + 1
        
        if iteration > state["max_iterations"]:
            logger.info("Max iterations reached, forcing synthesis")
            return {
                "next_agent": "synthesize",
                "iteration_count": iteration,
                "messages": [
                    {
                        "role": "assistant",
                        "content": "Max iterations reached, synthesizing findings"
                    }
                ],
            }
        
        # Check what's been done
        has_log = state["log_agent_result"] is not None
        has_metrics = state["metrics_agent_result"] is not None
        has_deploy = state["deploy_agent_result"] is not None
        
        # Use LLM for intelligent routing
        try:
            result = bedrock_client.invoke_with_json_output(
                prompt=OrchestratorPrompts.format_routing_prompt(
                    _graph_state_to_investigation_state(state)
                ),
                system_prompt=SYSTEM_PROMPT,
                max_tokens=500,
            )
            
            next_agent = result.get("action", "synthesize")
            thought = result.get("thought", "Continuing investigation")
            
            return {
                "next_agent": next_agent,
                "current_thought": thought,
                "iteration_count": iteration,
                "messages": [
                    {
                        "role": "assistant",
                        "content": f"Thought: {thought}. Action: {next_agent}"
                    }
                ],
            }
            
        except Exception as e:
            logger.warning(f"Routing failed, using fallback: {e}")
            
            # Simple fallback logic
            if not has_log:
                next_agent = "log_agent"
            elif not has_metrics:
                next_agent = "metrics_agent"
            elif not has_deploy:
                next_agent = "deploy_agent"
            else:
                next_agent = "synthesize"
            
            return {
                "next_agent": next_agent,
                "current_thought": "Fallback routing",
                "iteration_count": iteration,
                "messages": [
                    {
                        "role": "assistant",
                        "content": f"Fallback: routing to {next_agent}"
                    }
                ],
            }
    
    # =================================================================
    # Node: Log Agent
    # =================================================================
    async def log_agent_node(state: GraphState) -> dict[str, Any]:
        """
        Invoke the Log Analysis Agent.
        
        Updates:
        - log_agent_result: Agent results
        - log_findings: Extracted log findings
        - investigation_steps: Adds step record
        """
        logger.info("Invoking Log Agent")
        
        tool_input = ToolInput(
            log_events=state["log_events"],
            incident_id=state["incident_id"],
        )
        
        output = await tools.invoke("log_agent", tool_input)
        
        if output and output.result.success:
            return {
                "log_agent_result": output.result.to_dict(),
                "log_findings": [f.to_dict() for f in output.result.findings],
                "investigation_steps": [
                    {
                        "step_number": len(state["investigation_steps"]) + 1,
                        "agent_type": "log_agent",
                        "thought": state["current_thought"],
                        "action": "Analyzed log events for errors",
                        "observation": output.result.summary,
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                ],
            }
        else:
            return {
                "log_agent_result": {
                    "agent_type": "log_agent",
                    "success": False,
                    "error": output.result.error if output else "Tool not found",
                },
                "investigation_steps": [
                    {
                        "step_number": len(state["investigation_steps"]) + 1,
                        "agent_type": "log_agent",
                        "thought": state["current_thought"],
                        "action": "Attempted log analysis",
                        "observation": f"Failed: {output.result.error if output else 'Tool not found'}",
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                ],
            }
    
    # =================================================================
    # Node: Metrics Agent
    # =================================================================
    async def metrics_agent_node(state: GraphState) -> dict[str, Any]:
        """
        Invoke the Metrics Analysis Agent.
        
        Updates:
        - metrics_agent_result: Agent results
        - metric_anomalies: Detected anomalies
        - investigation_steps: Adds step record
        """
        logger.info("Invoking Metrics Agent")
        
        tool_input = ToolInput(
            log_events=state["log_events"],
            incident_id=state["incident_id"],
        )
        
        output = await tools.invoke("metrics_agent", tool_input)
        
        if output and output.result.success:
            return {
                "metrics_agent_result": output.result.to_dict(),
                "metric_anomalies": [a.to_dict() for a in output.result.findings],
                "investigation_steps": [
                    {
                        "step_number": len(state["investigation_steps"]) + 1,
                        "agent_type": "metrics_agent",
                        "thought": state["current_thought"],
                        "action": "Analyzed performance metrics",
                        "observation": output.result.summary,
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                ],
            }
        else:
            return {
                "metrics_agent_result": {
                    "agent_type": "metrics_agent",
                    "success": False,
                    "error": output.result.error if output else "Tool not found",
                },
                "investigation_steps": [
                    {
                        "step_number": len(state["investigation_steps"]) + 1,
                        "agent_type": "metrics_agent",
                        "thought": state["current_thought"],
                        "action": "Attempted metrics analysis",
                        "observation": f"Failed: {output.result.error if output else 'Tool not found'}",
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                ],
            }
    
    # =================================================================
    # Node: Deploy Agent
    # =================================================================
    async def deploy_agent_node(state: GraphState) -> dict[str, Any]:
        """
        Invoke the Deploy Intelligence Agent.
        
        Updates:
        - deploy_agent_result: Agent results
        - deployment_events: Discovered deployments
        - investigation_steps: Adds step record
        """
        logger.info("Invoking Deploy Agent")
        
        tool_input = ToolInput(
            log_events=state["log_events"],
            incident_id=state["incident_id"],
        )
        
        output = await tools.invoke("deploy_agent", tool_input)
        
        if output and output.result.success:
            return {
                "deploy_agent_result": output.result.to_dict(),
                "deployment_events": [e.to_dict() for e in output.result.findings],
                "investigation_steps": [
                    {
                        "step_number": len(state["investigation_steps"]) + 1,
                        "agent_type": "deploy_agent",
                        "thought": state["current_thought"],
                        "action": "Analyzed deployment timeline",
                        "observation": output.result.summary,
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                ],
            }
        else:
            return {
                "deploy_agent_result": {
                    "agent_type": "deploy_agent",
                    "success": False,
                    "error": output.result.error if output else "Tool not found",
                },
                "investigation_steps": [
                    {
                        "step_number": len(state["investigation_steps"]) + 1,
                        "agent_type": "deploy_agent",
                        "thought": state["current_thought"],
                        "action": "Attempted deployment analysis",
                        "observation": f"Failed: {output.result.error if output else 'Tool not found'}",
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                ],
            }
    
    # =================================================================
    # Node: Synthesis
    # =================================================================
    def synthesis_node(state: GraphState) -> dict[str, Any]:
        """
        Synthesize all findings into final report.
        
        Updates:
        - final_report: Complete investigation report
        - status: Sets to COMPLETED
        - messages: Adds completion message
        """
        logger.info("Synthesizing investigation findings")
        
        investigation_state = _graph_state_to_investigation_state(state)
        
        try:
            prompt = OrchestratorPrompts.format_synthesis_prompt(
                investigation_state,
                0,  # Duration will be calculated externally
            )
            
            result = bedrock_client.invoke_with_json_output(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                max_tokens=2000,
            )
            
            # Parse recommendations
            recommendations = []
            for rec in result.get("recommendations", []):
                if isinstance(rec, dict):
                    recommendations.append(
                        f"[{rec.get('urgency', 'medium').upper()}] "
                        f"{rec.get('action', 'No action specified')}"
                    )
                else:
                    recommendations.append(str(rec))
            
            # Add rollback if recommended
            if result.get("rollback_recommended"):
                rollback_target = result.get("rollback_target", "recent deployment")
                recommendations.insert(0, f"[IMMEDIATE] Rollback {rollback_target}")
            
            report = {
                "incident_id": state["incident_id"],
                "title": result.get("title", "Incident Investigation Report"),
                "status": InvestigationStatus.COMPLETED.value,
                "root_cause": result.get("root_cause", "Unable to determine"),
                "severity": result.get("severity", "medium"),
                "timeline": result.get("timeline", []),
                "recommendations": recommendations,
                "affected_services": result.get("affected_services", []),
                "investigation_steps": list(state["investigation_steps"]),
                "log_findings": list(state["log_findings"]),
                "metric_anomalies": list(state["metric_anomalies"]),
                "deployment_events": list(state["deployment_events"]),
                "created_at": datetime.utcnow().isoformat(),
            }
            
            return {
                "final_report": report,
                "status": InvestigationStatus.COMPLETED.value,
                "messages": [
                    {
                        "role": "assistant",
                        "content": f"Investigation complete. Root cause: {report['root_cause']}"
                    }
                ],
            }
            
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            
            # Fallback report
            report = {
                "incident_id": state["incident_id"],
                "title": "Incident Investigation (Fallback)",
                "status": InvestigationStatus.COMPLETED.value,
                "root_cause": "See investigation steps for details",
                "severity": "medium",
                "timeline": [],
                "recommendations": ["Review investigation steps manually"],
                "affected_services": [],
                "investigation_steps": list(state["investigation_steps"]),
                "log_findings": list(state["log_findings"]),
                "metric_anomalies": list(state["metric_anomalies"]),
                "deployment_events": list(state["deployment_events"]),
                "created_at": datetime.utcnow().isoformat(),
            }
            
            return {
                "final_report": report,
                "status": InvestigationStatus.COMPLETED.value,
                "error": str(e),
            }
    
    # =================================================================
    # Conditional Edge: Route to Agent
    # =================================================================
    def route_to_agent(state: GraphState) -> Literal[
        "log_agent", "metrics_agent", "deploy_agent", "synthesis"
    ]:
        """
        Determine which node to route to based on next_agent.
        
        Returns:
            Name of the next node to execute.
        """
        next_agent = state["next_agent"]
        
        if next_agent == "log_agent":
            return "log_agent"
        elif next_agent == "metrics_agent":
            return "metrics_agent"
        elif next_agent == "deploy_agent":
            return "deploy_agent"
        else:
            return "synthesis"
    
    # =================================================================
    # Build Graph Structure
    # =================================================================
    
    # Add nodes
    graph.add_node("input", input_node)
    graph.add_node("planning", planning_node)
    graph.add_node("router", router_node)
    graph.add_node("log_agent", log_agent_node)
    graph.add_node("metrics_agent", metrics_agent_node)
    graph.add_node("deploy_agent", deploy_agent_node)
    graph.add_node("synthesis", synthesis_node)
    
    # Add edges
    graph.set_entry_point("input")
    graph.add_edge("input", "planning")
    graph.add_edge("planning", "router")
    
    # Conditional routing from router
    graph.add_conditional_edges(
        "router",
        route_to_agent,
        {
            "log_agent": "log_agent",
            "metrics_agent": "metrics_agent",
            "deploy_agent": "deploy_agent",
            "synthesis": "synthesis",
        },
    )
    
    # All agents route back to router for next decision
    graph.add_edge("log_agent", "router")
    graph.add_edge("metrics_agent", "router")
    graph.add_edge("deploy_agent", "router")
    
    # Synthesis ends the graph
    graph.add_edge("synthesis", END)
    
    # Compile with optional checkpointing
    if enable_checkpointing:
        memory = MemorySaver()
        compiled = graph.compile(checkpointer=memory)
    else:
        compiled = graph.compile()
    
    logger.info("Investigation graph compiled successfully")
    
    return compiled


def _graph_state_to_investigation_state(state: GraphState) -> InvestigationState:
    """
    Convert GraphState to InvestigationState for prompt formatting.
    
    Args:
        state: LangGraph state dictionary.
        
    Returns:
        InvestigationState object.
    """
    from app.deployment.incident_commander.shared.state import (
        InvestigationState,
        InvestigationStatus,
        AgentResult,
        AgentType,
        LogFinding,
        MetricAnomaly,
        DeploymentEvent,
        InvestigationStep,
        Severity,
    )
    from datetime import datetime
    
    # Parse log findings
    log_findings = []
    for f in state.get("log_findings", []):
        log_findings.append(LogFinding(
            error_type=f.get("error_type", ""),
            message=f.get("message", ""),
            file_path=f.get("file_path"),
            line_number=f.get("line_number"),
            traceback=f.get("traceback"),
            request_id=f.get("request_id"),
            severity=Severity(f.get("severity", "medium")),
            patterns=f.get("patterns", []),
        ))
    
    # Parse metric anomalies
    metric_anomalies = []
    for a in state.get("metric_anomalies", []):
        metric_anomalies.append(MetricAnomaly(
            metric_name=a.get("metric_name", ""),
            current_value=a.get("current_value", 0),
            baseline_value=a.get("baseline_value", 0),
            threshold=a.get("threshold", 0),
            deviation_percent=a.get("deviation_percent", 0),
            component=a.get("component"),
            severity=Severity(a.get("severity", "medium")),
            related_metrics=a.get("related_metrics", []),
        ))
    
    # Parse deployment events
    deployment_events = []
    for e in state.get("deployment_events", []):
        ts = e.get("timestamp")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        deployment_events.append(DeploymentEvent(
            deployment_id=e.get("deployment_id", ""),
            timestamp=ts or datetime.utcnow(),
            service=e.get("service", ""),
            change_type=e.get("change_type", ""),
            version=e.get("version"),
            commit_sha=e.get("commit_sha"),
            author=e.get("author"),
            changes_summary=e.get("changes_summary"),
            is_suspect=e.get("is_suspect", False),
        ))
    
    # Parse investigation steps
    investigation_steps = []
    for s in state.get("investigation_steps", []):
        investigation_steps.append(InvestigationStep(
            step_number=s.get("step_number", 0),
            agent_type=AgentType(s.get("agent_type", "log_agent")),
            thought=s.get("thought", ""),
            action=s.get("action", ""),
            observation=s.get("observation", ""),
        ))
    
    # Parse agent results
    log_agent_result = None
    log_result = state.get("log_agent_result")
    if log_result is not None:
        log_agent_result = AgentResult(
            agent_type=AgentType.LOG_AGENT,
            success=log_result.get("success", False),
            execution_time_ms=log_result.get("execution_time_ms", 0),
            findings=log_findings,
            summary=log_result.get("summary", ""),
            error=log_result.get("error"),
        )
    
    metrics_agent_result = None
    metrics_result = state.get("metrics_agent_result")
    if metrics_result is not None:
        metrics_agent_result = AgentResult(
            agent_type=AgentType.METRICS_AGENT,
            success=metrics_result.get("success", False),
            execution_time_ms=metrics_result.get("execution_time_ms", 0),
            findings=metric_anomalies,
            summary=metrics_result.get("summary", ""),
            error=metrics_result.get("error"),
        )
    
    deploy_agent_result = None
    deploy_result = state.get("deploy_agent_result")
    if deploy_result is not None:
        deploy_agent_result = AgentResult(
            agent_type=AgentType.DEPLOY_AGENT,
            success=deploy_result.get("success", False),
            execution_time_ms=deploy_result.get("execution_time_ms", 0),
            findings=deployment_events,
            summary=deploy_result.get("summary", ""),
            error=deploy_result.get("error"),
        )
    
    return InvestigationState(
        incident_id=state.get("incident_id", ""),
        status=InvestigationStatus(state.get("status", "pending")),
        log_group=state.get("log_group"),
        log_stream=state.get("log_stream"),
        log_events=state.get("log_events", []),
        current_thought=state.get("current_thought", ""),
        current_plan=state.get("current_plan", []),
        iteration_count=state.get("iteration_count", 0),
        max_iterations=state.get("max_iterations", 10),
        log_agent_result=log_agent_result,
        metrics_agent_result=metrics_agent_result,
        deploy_agent_result=deploy_agent_result,
        log_findings=log_findings,
        metric_anomalies=metric_anomalies,
        deployment_events=deployment_events,
        investigation_steps=investigation_steps,
    )


async def run_investigation_workflow(
    log_data: CloudWatchLog,
    bedrock_client: BedrockClient,
    redis_memory: RedisMemory,
    incident_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Run the complete investigation workflow.
    
    This is a convenience function that creates the graph, initializes
    state, and executes the workflow to completion.
    
    Args:
        log_data: CloudWatch log JSON input.
        bedrock_client: Bedrock client for LLM calls.
        redis_memory: Redis client for storage.
        incident_id: Optional incident ID.
        
    Returns:
        Final state dictionary with investigation results.
    """
    # Create the graph
    graph = create_investigation_graph(
        bedrock_client=bedrock_client,
        redis_memory=redis_memory,
        enable_checkpointing=True,
    )
    
    # Create initial state
    initial_state = create_initial_state(log_data, incident_id)
    
    # Run the workflow
    config = {"configurable": {"thread_id": initial_state["incident_id"]}}
    
    final_state = None
    async for state in graph.astream(initial_state, config):
        final_state = state
        # Log progress
        for node_name, node_state in state.items():
            if isinstance(node_state, dict):
                logger.debug(f"Node {node_name} completed")
    
    # Extract final state from the last event
    if final_state:
        # Get the actual state dict
        for node_name, node_state in final_state.items():
            if isinstance(node_state, dict) and "final_report" in node_state:
                return node_state
    
    return final_state or {}
