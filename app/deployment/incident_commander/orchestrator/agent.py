"""
ReAct Orchestrator Agent

This module implements the main orchestrator agent using the ReAct
(Reasoning + Acting) pattern for intelligent incident investigation.

The orchestrator:
1. Receives CloudWatch log incidents
2. Creates an investigation plan
3. Iteratively invokes sub-agents based on reasoning
4. Synthesizes findings into a final report
5. Provides actionable recommendations

ReAct Pattern Implementation:
- THOUGHT: The agent reasons about the current investigation state
- ACTION: The agent selects a sub-agent to invoke
- OBSERVATION: The agent processes the sub-agent's results
- Repeat until investigation is complete or max iterations reached
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Optional

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from app.deployment.incident_commander.shared.state import (
    InvestigationState,
    InvestigationStatus,
    InvestigationReport,
    InvestigationStep,
    AgentType,
    Severity,
)
from app.deployment.incident_commander.shared.bedrock_client import BedrockClient, BedrockClientError
from app.deployment.incident_commander.shared.redis_client import RedisMemory
from app.deployment.incident_commander.orchestrator.prompts import (
    OrchestratorPrompts,
    SYSTEM_PROMPT,
)
from app.deployment.incident_commander.orchestrator.tools import (
    ToolRegistry,
    ToolInput,
)

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


@dataclass
class ReActStep:
    """
    Represents a single step in the ReAct loop.
    
    Attributes:
        thought: The reasoning behind the action.
        action: The selected action (agent name or "synthesize").
        reason: Explanation for the action choice.
        observation: Result of the action (filled after execution).
    """
    thought: str
    action: str
    reason: str
    observation: str = ""


class OrchestratorAgent:
    """
    ReAct-based Orchestrator for multi-agent incident investigation.
    
    This agent implements the ReAct pattern to coordinate sub-agents:
    
    1. Initial Planning Phase:
       - Analyzes CloudWatch log input
       - Creates investigation hypothesis
       - Plans initial investigation steps
    
    2. Investigation Loop (ReAct):
       - THOUGHT: Reasons about what information is needed
       - ACTION: Selects appropriate sub-agent to invoke
       - OBSERVATION: Processes and stores agent results
       - Updates investigation state
       - Repeats until sufficient evidence or max iterations
    
    3. Synthesis Phase:
       - Combines all agent findings
       - Identifies root cause
       - Generates recommendations
       - Creates final report
    
    Example:
        orchestrator = OrchestratorAgent(bedrock_client, redis_memory)
        
        state = InvestigationState.from_cloudwatch_log(log_data)
        final_state, report = await orchestrator.investigate(state)
        
        print(report.root_cause)
        print(report.recommendations)
    """
    
    def __init__(
        self,
        bedrock_client: BedrockClient,
        redis_memory: RedisMemory,
        max_iterations: int = 10,
        timeout_seconds: int = 300,
    ):
        """
        Initialize the orchestrator agent.
        
        Args:
            bedrock_client: Client for LLM reasoning.
            redis_memory: Redis client for state storage.
            max_iterations: Maximum ReAct loop iterations.
            timeout_seconds: Overall investigation timeout.
        """
        self.bedrock = bedrock_client
        self.memory = redis_memory
        self.max_iterations = max_iterations
        self.timeout = timeout_seconds
        
        # Initialize tool registry
        self.tools = ToolRegistry(bedrock_client, redis_memory)
        
        logger.info(
            f"OrchestratorAgent initialized with max_iterations={max_iterations}"
        )
    
    async def investigate(
        self,
        state: InvestigationState,
    ) -> tuple[InvestigationState, InvestigationReport]:
        """
        Run the full investigation workflow.
        
        This is the main entry point for incident investigation.
        It orchestrates the entire ReAct loop and returns the
        final state and report.
        
        Args:
            state: Initial investigation state with log events.
            
        Returns:
            Tuple of (final state, investigation report).
        """
        start_time = time.time()
        
        with tracer.start_as_current_span("orchestrator.investigate") as span:
            span.set_attribute("incident_id", state.incident_id)
            span.set_attribute("event_count", len(state.log_events))
            
            try:
                # Update state to in-progress
                state = replace(
                    state,
                    status=InvestigationStatus.IN_PROGRESS,
                    max_iterations=self.max_iterations,
                )
                
                # Store initial state
                self.memory.store_investigation_state(
                    state.incident_id,
                    state.to_dict(),
                )
                
                # Phase 1: Create investigation plan
                logger.info(f"Creating investigation plan for {state.incident_id}")
                state = await self._create_plan(state)
                
                # Phase 2: Execute ReAct loop
                logger.info(f"Starting ReAct investigation loop")
                state = await self._react_loop(state)
                
                # Phase 3: Synthesize findings
                logger.info(f"Synthesizing investigation findings")
                duration_ms = (time.time() - start_time) * 1000
                state, report = await self._synthesize(state, duration_ms)
                
                # Store final state
                self.memory.store_investigation_state(
                    state.incident_id,
                    state.to_dict(),
                )
                
                span.set_attribute("status", state.status.value)
                span.set_attribute("step_count", len(state.investigation_steps))
                span.set_attribute("duration_ms", duration_ms)
                span.set_status(Status(StatusCode.OK))
                
                return state, report
                
            except Exception as e:
                logger.error(f"Investigation failed: {e}")
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                
                # Create error state and report
                state = replace(
                    state,
                    status=InvestigationStatus.FAILED,
                    error=str(e),
                )
                
                report = InvestigationReport(
                    incident_id=state.incident_id,
                    title="Investigation Failed",
                    status=InvestigationStatus.FAILED,
                    root_cause=f"Investigation failed: {e}",
                    severity=Severity.HIGH,
                    timeline=[],
                    recommendations=["Re-run investigation after fixing errors"],
                    affected_services=[],
                    investigation_steps=state.investigation_steps,
                    log_findings=state.log_findings,
                    metric_anomalies=state.metric_anomalies,
                    deployment_events=state.deployment_events,
                )
                
                return state, report
    
    async def _create_plan(self, state: InvestigationState) -> InvestigationState:
        """
        Create the initial investigation plan.
        
        Uses the LLM to analyze the log events and create a structured
        plan for which agents to invoke and in what order.
        
        Args:
            state: Initial investigation state.
            
        Returns:
            State updated with investigation plan.
        """
        with tracer.start_as_current_span("orchestrator.create_plan") as span:
            prompt = OrchestratorPrompts.format_planning_prompt(state)
            
            try:
                result = self.bedrock.invoke_with_json_output(
                    prompt=prompt,
                    system_prompt=SYSTEM_PROMPT,
                    max_tokens=1000,
                )
                
                # Extract plan from LLM response
                plan_steps = []
                for step in result.get("investigation_steps", []):
                    agent = step.get("agent", "log_agent")
                    reason = step.get("reason", "Initial analysis")
                    plan_steps.append(f"{agent}: {reason}")
                
                state = replace(
                    state,
                    current_thought=result.get("initial_hypothesis", ""),
                    current_plan=plan_steps,
                )
                
                span.set_attribute("plan_steps", len(plan_steps))
                span.set_attribute(
                    "severity",
                    result.get("severity_assessment", "unknown")
                )
                
                logger.info(f"Investigation plan created with {len(plan_steps)} steps")
                
            except BedrockClientError as e:
                logger.warning(f"Plan creation failed, using default: {e}")
                # Default plan if LLM fails
                state = replace(
                    state,
                    current_plan=[
                        "log_agent: Analyze logs for errors",
                        "metrics_agent: Check for performance anomalies",
                        "deploy_agent: Check recent deployments",
                    ],
                )
            
            return state
    
    async def _react_loop(self, state: InvestigationState) -> InvestigationState:
        """
        Execute the main ReAct (Reasoning + Acting) loop.
        
        Iteratively:
        1. Reason about what information is needed
        2. Select and invoke appropriate agent
        3. Process results and update state
        4. Decide if investigation is complete
        
        Args:
            state: Current investigation state.
            
        Returns:
            State after all ReAct iterations.
        """
        with tracer.start_as_current_span("orchestrator.react_loop") as span:
            
            while state.iteration_count < state.max_iterations:
                iteration = state.iteration_count + 1
                
                with tracer.start_span(f"orchestrator.react_step_{iteration}") as step_span:
                    step_span.set_attribute("iteration", iteration)
                    
                    logger.info(f"ReAct iteration {iteration}/{state.max_iterations}")
                    
                    # Get next action from LLM
                    react_step = await self._get_next_action(state)
                    step_span.set_attribute("action", react_step.action)
                    step_span.set_attribute("thought", react_step.thought[:100])
                    
                    # Check if synthesis is requested
                    if react_step.action == "synthesize":
                        logger.info("Orchestrator decided to synthesize findings")
                        state = replace(
                            state,
                            iteration_count=iteration,
                            status=InvestigationStatus.SYNTHESIZING,
                        )
                        break
                    
                    # Execute the selected agent
                    state = await self._execute_action(state, react_step)
                    
                    # Record investigation step
                    agent_type = self._get_agent_type(react_step.action)
                    state = state.add_investigation_step(
                        agent_type=agent_type,
                        thought=react_step.thought,
                        action=react_step.action,
                        observation=react_step.observation,
                    )
                    
                    # Store step in Redis
                    self.memory.store_investigation_step(
                        state.incident_id,
                        state.investigation_steps[-1].to_dict(),
                    )
                    
                    # Update iteration count
                    state = replace(state, iteration_count=iteration)
                    
                    # Store intermediate state
                    self.memory.store_investigation_state(
                        state.incident_id,
                        state.to_dict(),
                    )
            
            span.set_attribute("total_iterations", state.iteration_count)
            
            return state
    
    async def _get_next_action(self, state: InvestigationState) -> ReActStep:
        """
        Use LLM to determine the next action in the ReAct loop.
        
        Args:
            state: Current investigation state.
            
        Returns:
            ReActStep with thought, action, and reason.
        """
        prompt = OrchestratorPrompts.format_routing_prompt(state)
        
        try:
            result = self.bedrock.invoke_with_json_output(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                max_tokens=500,
            )
            
            return ReActStep(
                thought=result.get("thought", "Analyzing situation"),
                action=result.get("action", "log_agent"),
                reason=result.get("reason", "Default action"),
            )
            
        except BedrockClientError as e:
            logger.warning(f"Routing decision failed: {e}")
            
            # Default routing based on what hasn't been done
            if not state.log_agent_result:
                return ReActStep(
                    thought="LLM unavailable, using default routing",
                    action="log_agent",
                    reason="Log analysis not yet performed",
                )
            elif not state.metrics_agent_result:
                return ReActStep(
                    thought="LLM unavailable, using default routing",
                    action="metrics_agent",
                    reason="Metrics analysis not yet performed",
                )
            elif not state.deploy_agent_result:
                return ReActStep(
                    thought="LLM unavailable, using default routing",
                    action="deploy_agent",
                    reason="Deployment analysis not yet performed",
                )
            else:
                return ReActStep(
                    thought="All agents have been consulted",
                    action="synthesize",
                    reason="Ready to create final report",
                )
    
    async def _execute_action(
        self,
        state: InvestigationState,
        react_step: ReActStep,
    ) -> InvestigationState:
        """
        Execute the selected agent action.
        
        Args:
            state: Current investigation state.
            react_step: The ReAct step with action to execute.
            
        Returns:
            State updated with agent results.
        """
        action = react_step.action
        
        with tracer.start_as_current_span(f"orchestrator.execute_{action}") as span:
            
            # Create tool input
            tool_input = ToolInput(
                log_events=state.log_events,
                incident_id=state.incident_id,
            )
            
            # Invoke the tool
            output = await self.tools.invoke(action, tool_input)
            
            if output:
                # Update state with results
                if output.result.success:
                    react_step.observation = output.result.summary
                else:
                    react_step.observation = f"Agent failed: {output.result.error}"
                
                span.set_attribute("success", output.result.success)
                span.set_attribute("finding_count", len(output.result.findings))
                
                # Apply state updates
                updates = {
                    "log_agent_result": state.log_agent_result,
                    "metrics_agent_result": state.metrics_agent_result,
                    "deploy_agent_result": state.deploy_agent_result,
                    "log_findings": state.log_findings,
                    "metric_anomalies": state.metric_anomalies,
                    "deployment_events": state.deployment_events,
                }
                updates.update(output.state_updates)
                
                state = replace(
                    state,
                    log_agent_result=updates.get("log_agent_result"),
                    metrics_agent_result=updates.get("metrics_agent_result"),
                    deploy_agent_result=updates.get("deploy_agent_result"),
                    log_findings=updates.get("log_findings", []),
                    metric_anomalies=updates.get("metric_anomalies", []),
                    deployment_events=updates.get("deployment_events", []),
                )
                
            else:
                react_step.observation = f"Tool {action} not found"
                span.set_attribute("error", "Tool not found")
            
            return state
    
    async def _synthesize(
        self,
        state: InvestigationState,
        duration_ms: float,
    ) -> tuple[InvestigationState, InvestigationReport]:
        """
        Synthesize all findings into a final investigation report.
        
        Uses the LLM to analyze all agent findings and create a
        comprehensive report with root cause and recommendations.
        
        Args:
            state: Investigation state with all findings.
            duration_ms: Total investigation duration.
            
        Returns:
            Tuple of (final state, investigation report).
        """
        with tracer.start_as_current_span("orchestrator.synthesize") as span:
            
            prompt = OrchestratorPrompts.format_synthesis_prompt(state, duration_ms)
            
            try:
                result = self.bedrock.invoke_with_json_output(
                    prompt=prompt,
                    system_prompt=SYSTEM_PROMPT,
                    max_tokens=2000,
                )
                
                # Parse LLM recommendations
                recommendations = []
                for rec in result.get("recommendations", []):
                    if isinstance(rec, dict):
                        recommendations.append(
                            f"[{rec.get('urgency', 'medium').upper()}] "
                            f"{rec.get('action', 'No action specified')}"
                        )
                    else:
                        recommendations.append(str(rec))
                
                # Build timeline
                timeline = result.get("timeline", [])
                
                # Determine severity
                severity_str = result.get("severity", "medium").lower()
                severity_map = {
                    "critical": Severity.CRITICAL,
                    "high": Severity.HIGH,
                    "medium": Severity.MEDIUM,
                    "low": Severity.LOW,
                }
                severity = severity_map.get(severity_str, Severity.MEDIUM)
                
                report = InvestigationReport(
                    incident_id=state.incident_id,
                    title=result.get("title", "Incident Investigation Report"),
                    status=InvestigationStatus.COMPLETED,
                    root_cause=result.get("root_cause", "Unable to determine root cause"),
                    severity=severity,
                    timeline=timeline,
                    recommendations=recommendations,
                    affected_services=result.get("affected_services", []),
                    investigation_steps=state.investigation_steps,
                    log_findings=state.log_findings,
                    metric_anomalies=state.metric_anomalies,
                    deployment_events=state.deployment_events,
                )
                
                # Check for rollback recommendation
                if result.get("rollback_recommended"):
                    rollback_target = result.get("rollback_target", "recent deployment")
                    report.recommendations.insert(
                        0,
                        f"[IMMEDIATE] Rollback {rollback_target}"
                    )
                
                span.set_attribute("root_cause", report.root_cause[:100])
                span.set_attribute("severity", severity.value)
                span.set_attribute("recommendation_count", len(recommendations))
                
            except BedrockClientError as e:
                logger.warning(f"Synthesis failed, using fallback: {e}")
                report = self._fallback_synthesis(state, duration_ms)
            
            # Update final state
            state = replace(
                state,
                status=InvestigationStatus.COMPLETED,
                final_report=report,
            )
            
            logger.info(f"Investigation completed: {report.root_cause}")
            
            return state, report
    
    def _fallback_synthesis(
        self,
        state: InvestigationState,
        duration_ms: float,
    ) -> InvestigationReport:
        """
        Create a fallback report when LLM synthesis fails.
        
        Args:
            state: Investigation state.
            duration_ms: Investigation duration.
            
        Returns:
            Basic investigation report.
        """
        # Determine severity based on findings
        severity = Severity.MEDIUM
        if any(f.severity == Severity.CRITICAL for f in state.log_findings):
            severity = Severity.CRITICAL
        elif any(f.severity == Severity.HIGH for f in state.log_findings):
            severity = Severity.HIGH
        elif any(a.severity == Severity.CRITICAL for a in state.metric_anomalies):
            severity = Severity.CRITICAL
        
        # Build root cause from findings
        root_cause_parts = []
        
        if state.log_findings:
            top_error = state.log_findings[0]
            root_cause_parts.append(f"Error: {top_error.error_type}")
        
        if state.metric_anomalies:
            top_anomaly = state.metric_anomalies[0]
            root_cause_parts.append(f"Anomaly: {top_anomaly.metric_name}")
        
        suspect_deploys = [e for e in state.deployment_events if e.is_suspect]
        if suspect_deploys:
            root_cause_parts.append(
                f"Suspect deployment: {suspect_deploys[0].service} "
                f"({suspect_deploys[0].change_type})"
            )
        
        root_cause = " | ".join(root_cause_parts) or "Unable to determine"
        
        # Build recommendations
        recommendations = []
        if suspect_deploys:
            recommendations.append(
                f"[IMMEDIATE] Consider rollback of {suspect_deploys[0].service}"
            )
        if state.log_findings:
            recommendations.append(
                "[SHORT_TERM] Investigate and fix the root cause error"
            )
        recommendations.append("[LONG_TERM] Add monitoring for early detection")
        
        # Build affected services
        affected = set()
        for e in state.deployment_events:
            affected.add(e.service)
        
        return InvestigationReport(
            incident_id=state.incident_id,
            title="Incident Investigation (Fallback)",
            status=InvestigationStatus.COMPLETED,
            root_cause=root_cause,
            severity=severity,
            timeline=[],
            recommendations=recommendations,
            affected_services=list(affected),
            investigation_steps=state.investigation_steps,
            log_findings=state.log_findings,
            metric_anomalies=state.metric_anomalies,
            deployment_events=state.deployment_events,
        )
    
    def _get_agent_type(self, action: str) -> AgentType:
        """Map action name to AgentType."""
        mapping = {
            "log_agent": AgentType.LOG_AGENT,
            "metrics_agent": AgentType.METRICS_AGENT,
            "deploy_agent": AgentType.DEPLOY_AGENT,
        }
        return mapping.get(action, AgentType.LOG_AGENT)
