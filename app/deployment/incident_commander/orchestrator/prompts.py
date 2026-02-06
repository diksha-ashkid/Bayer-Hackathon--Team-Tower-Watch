"""
LLM Prompts for the Orchestrator Agent

This module contains all prompt templates used by the orchestrator
for reasoning, planning, routing, and synthesis.

Prompt Design Principles:
1. Clear role definition for the LLM
2. Structured output formats (JSON when needed)
3. Chain-of-thought reasoning encouragement
4. Context-aware decision making
5. Explicit action formats for tool use
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.deployment.incident_commander.shared.state import (
    InvestigationState,
    AgentType,
    LogFinding,
    MetricAnomaly,
    DeploymentEvent,
)


# =============================================================================
# System Prompt - Defines the orchestrator's role and capabilities
# =============================================================================

SYSTEM_PROMPT = """You are an Expert Incident Investigation Orchestrator for cloud applications.

Your role is to coordinate a multi-agent investigation team to analyze CloudWatch log incidents 
and identify root causes. You follow the ReAct (Reasoning + Acting) pattern to systematically 
investigate issues.

## Available Tools/Agents

You can invoke these specialized agents to gather information:

1. **log_agent** - Analyzes log events for errors, tracebacks, and patterns
   - Use when: You see ERROR or WARN log levels, exceptions, or need to understand failures
   - Returns: Error types, file locations, tracebacks, request IDs, patterns

2. **metrics_agent** - Analyzes performance metrics for anomalies
   - Use when: You suspect performance issues (latency, CPU, memory, error rates)
   - Returns: Metric anomalies, threshold breaches, statistical outliers

3. **deploy_agent** - Correlates incidents with recent deployments
   - Use when: You need to check if recent deployments may have caused the issue
   - Returns: Recent deployments, config changes, suspect deployments in time window

## Investigation Protocol

1. **Initial Analysis**: Examine the log events to understand the symptoms
2. **Hypothesis Formation**: Form theories about potential root causes
3. **Evidence Gathering**: Use agents to collect supporting evidence
4. **Correlation**: Connect findings across agents
5. **Root Cause Identification**: Determine the most likely root cause
6. **Recommendation**: Provide actionable remediation steps

## Response Format

For each step, provide:
- THOUGHT: Your reasoning about the current situation
- ACTION: The agent to invoke (log_agent, metrics_agent, deploy_agent) or "synthesize" to complete
- OBSERVATION: What you learned from the action (after receiving results)

## Important Notes

- Prioritize based on severity (CRITICAL > HIGH > MEDIUM > LOW)
- Always check deployment timeline when you find configuration-related errors
- Consider cascading failures (one issue may cause others)
- Be concise but thorough in your analysis
- Maximum 10 investigation steps to prevent infinite loops
"""


# =============================================================================
# Planning Prompt - Creates the initial investigation plan
# =============================================================================

PLANNING_PROMPT = """Analyze these CloudWatch log events and create an investigation plan.

## Log Context
Log Group: {log_group}
Log Stream: {log_stream}
Event Count: {event_count}

## Sample Log Events (first 10)
{log_samples}

## Your Task

Create an investigation plan by:
1. Identifying the key symptoms in the logs
2. Prioritizing which agents to invoke
3. Anticipating potential root causes

Respond with a JSON object:
```json
{{
    "symptoms": ["list of observed symptoms"],
    "severity_assessment": "critical|high|medium|low",
    "initial_hypothesis": "your initial theory about the root cause",
    "investigation_steps": [
        {{
            "step": 1,
            "agent": "log_agent|metrics_agent|deploy_agent",
            "reason": "why this agent should be consulted"
        }}
    ],
    "expected_root_causes": ["list of possible root causes to investigate"]
}}
```

Focus on creating a logical investigation sequence. Start with the agent most 
likely to reveal the primary issue based on the log content."""


# =============================================================================
# Routing Prompt - Decides which agent to invoke next
# =============================================================================

ROUTING_PROMPT = """Based on the current investigation state, decide the next action.

## Investigation Progress
Incident ID: {incident_id}
Iteration: {iteration}/{max_iterations}
Current Status: {status}

## Log Events Summary
Log Group: {log_group}
Total Events: {event_count}
First 5 Events:
{log_samples}

## Findings So Far

### Log Agent Results
{log_findings}

### Metrics Agent Results
{metrics_findings}

### Deploy Agent Results
{deploy_findings}

## Investigation History
{investigation_steps}

## Your Task

Decide the next step using the ReAct pattern:

THOUGHT: [Your reasoning about what information is missing or what needs investigation]
ACTION: [One of: log_agent, metrics_agent, deploy_agent, synthesize]
REASON: [Brief explanation for choosing this action]

Guidelines:
- If you haven't checked logs and see errors → invoke log_agent
- If you see latency/performance issues → invoke metrics_agent  
- If you found errors and need deployment context → invoke deploy_agent
- If you have enough information to determine root cause → synthesize

Respond with JSON:
```json
{{
    "thought": "your reasoning",
    "action": "log_agent|metrics_agent|deploy_agent|synthesize",
    "reason": "why this action"
}}
```"""


# =============================================================================
# Synthesis Prompt - Creates the final investigation report
# =============================================================================

SYNTHESIS_PROMPT = """Synthesize the investigation findings into a final report.

## Incident Overview
Incident ID: {incident_id}
Log Group: {log_group}
Investigation Duration: {duration_ms}ms
Total Steps: {step_count}

## Agent Findings

### Log Analysis Results
{log_findings}

### Metrics Analysis Results  
{metrics_findings}

### Deployment Correlation Results
{deploy_findings}

## Investigation Timeline
{investigation_steps}

## Your Task

Create a comprehensive investigation report that:
1. Identifies the root cause
2. Explains the incident timeline
3. Provides actionable recommendations
4. Assesses overall severity

Respond with JSON:
```json
{{
    "title": "Brief incident title",
    "root_cause": "Clear statement of the root cause",
    "severity": "critical|high|medium|low",
    "timeline": [
        {{
            "time": "relative or absolute time",
            "event": "what happened"
        }}
    ],
    "evidence": [
        "Key evidence points supporting the root cause"
    ],
    "affected_services": ["list of impacted services"],
    "recommendations": [
        {{
            "action": "what to do",
            "urgency": "immediate|short_term|long_term",
            "reason": "why this helps"
        }}
    ],
    "rollback_recommended": true|false,
    "rollback_target": "deployment ID if rollback recommended"
}}
```

Be specific and actionable. If a config change caused the issue, recommend rollback.
If multiple factors contributed, explain the relationship between them."""


# =============================================================================
# Observation Prompt - Processes agent results
# =============================================================================

OBSERVATION_PROMPT = """The {agent_name} has returned results. Update your understanding.

## Agent: {agent_name}
Execution Time: {execution_time}ms
Success: {success}

## Findings
{findings_summary}

## Raw Results
{raw_output}

Based on these findings, update your investigation hypothesis.

Respond with JSON:
```json
{{
    "observation": "what you learned from this agent's results",
    "hypothesis_update": "how this changes your understanding",
    "next_question": "what you still need to investigate",
    "confidence": "low|medium|high"
}}
```"""


# =============================================================================
# Helper class for prompt formatting
# =============================================================================

@dataclass
class OrchestratorPrompts:
    """
    Helper class for formatting orchestrator prompts with context.
    
    Provides methods to format each prompt template with the appropriate
    investigation state and findings.
    """
    
    @staticmethod
    def format_planning_prompt(state: InvestigationState) -> str:
        """
        Format the planning prompt with log event context.
        
        Args:
            state: Current investigation state.
            
        Returns:
            Formatted planning prompt.
        """
        # Get sample log events
        samples = state.log_events[:10]
        sample_text = "\n".join([
            f"[{e.get('timestamp', 'N/A')}] {e.get('message', '')[:200]}"
            for e in samples
        ])
        
        return PLANNING_PROMPT.format(
            log_group=state.log_group or "Unknown",
            log_stream=state.log_stream or "Unknown",
            event_count=len(state.log_events),
            log_samples=sample_text or "No log events available",
        )
    
    @staticmethod
    def format_routing_prompt(state: InvestigationState) -> str:
        """
        Format the routing prompt with current investigation state.
        
        Args:
            state: Current investigation state.
            
        Returns:
            Formatted routing prompt.
        """
        # Format log findings
        log_findings = "Not yet analyzed"
        if state.log_agent_result:
            if state.log_agent_result.success:
                log_findings = state.log_agent_result.summary
                if state.log_findings:
                    log_findings += f"\nFound {len(state.log_findings)} error(s):"
                    for f in state.log_findings[:3]:
                        log_findings += f"\n- {f.error_type}: {f.message[:100]}"
            else:
                log_findings = f"Analysis failed: {state.log_agent_result.error}"
        
        # Format metrics findings
        metrics_findings = "Not yet analyzed"
        if state.metrics_agent_result:
            if state.metrics_agent_result.success:
                metrics_findings = state.metrics_agent_result.summary
                if state.metric_anomalies:
                    metrics_findings += f"\nFound {len(state.metric_anomalies)} anomaly(ies):"
                    for a in state.metric_anomalies[:3]:
                        metrics_findings += f"\n- {a.metric_name}: {a.current_value} (threshold: {a.threshold})"
            else:
                metrics_findings = f"Analysis failed: {state.metrics_agent_result.error}"
        
        # Format deploy findings
        deploy_findings = "Not yet analyzed"
        if state.deploy_agent_result:
            if state.deploy_agent_result.success:
                deploy_findings = state.deploy_agent_result.summary
                suspect = [e for e in state.deployment_events if e.is_suspect]
                if suspect:
                    deploy_findings += f"\n{len(suspect)} suspect deployment(s):"
                    for e in suspect[:3]:
                        changes = e.changes_summary or ""
                        deploy_findings += f"\n- {e.service}: {e.change_type} ({changes[:50]}...)"
            else:
                deploy_findings = f"Analysis failed: {state.deploy_agent_result.error}"
        
        # Format investigation steps
        steps_text = "None yet"
        if state.investigation_steps:
            steps_text = "\n".join([
                f"Step {s.step_number}: {s.agent_type.value} - {s.observation[:100]}"
                for s in state.investigation_steps
            ])
        
        # Format log samples
        samples = state.log_events[:5]
        sample_text = "\n".join([
            f"[{e.get('timestamp', 'N/A')}] {e.get('message', '')[:150]}"
            for e in samples
        ])
        
        return ROUTING_PROMPT.format(
            incident_id=state.incident_id,
            iteration=state.iteration_count,
            max_iterations=state.max_iterations,
            status=state.status.value,
            log_group=state.log_group or "Unknown",
            event_count=len(state.log_events),
            log_samples=sample_text or "No log events",
            log_findings=log_findings,
            metrics_findings=metrics_findings,
            deploy_findings=deploy_findings,
            investigation_steps=steps_text,
        )
    
    @staticmethod
    def format_synthesis_prompt(
        state: InvestigationState,
        duration_ms: float,
    ) -> str:
        """
        Format the synthesis prompt for final report generation.
        
        Args:
            state: Current investigation state with all findings.
            duration_ms: Total investigation duration.
            
        Returns:
            Formatted synthesis prompt.
        """
        # Format log findings
        log_findings = "No log analysis performed"
        if state.log_agent_result and state.log_agent_result.success:
            log_findings = state.log_agent_result.summary
            if state.log_findings:
                log_findings += "\n\nDetailed Findings:"
                for f in state.log_findings:
                    log_findings += f"\n- Error: {f.error_type}"
                    log_findings += f"\n  Message: {f.message[:200]}"
                    if f.file_path:
                        log_findings += f"\n  Location: {f.file_path}:{f.line_number}"
                    if f.patterns:
                        log_findings += f"\n  Patterns: {', '.join(f.patterns)}"
        
        # Format metrics findings
        metrics_findings = "No metrics analysis performed"
        if state.metrics_agent_result and state.metrics_agent_result.success:
            metrics_findings = state.metrics_agent_result.summary
            if state.metric_anomalies:
                metrics_findings += "\n\nDetailed Anomalies:"
                for a in state.metric_anomalies:
                    metrics_findings += f"\n- {a.metric_name}: {a.current_value:.2f}"
                    metrics_findings += f"\n  Threshold: {a.threshold:.2f}, Severity: {a.severity.value}"
                    if a.component:
                        metrics_findings += f"\n  Component: {a.component}"
        
        # Format deploy findings
        deploy_findings = "No deployment analysis performed"
        if state.deploy_agent_result and state.deploy_agent_result.success:
            deploy_findings = state.deploy_agent_result.summary
            if state.deployment_events:
                deploy_findings += "\n\nDeployment Timeline:"
                for e in state.deployment_events:
                    suspect_marker = " [SUSPECT]" if e.is_suspect else ""
                    deploy_findings += f"\n- {e.timestamp}: {e.service} {e.change_type}{suspect_marker}"
                    changes = e.changes_summary or ""
                    deploy_findings += f"\n  Changes: {changes[:100]}"
        
        # Format investigation steps
        steps_text = "\n".join([
            f"{s.step_number}. [{s.agent_type.value}] {s.thought}\n   → {s.observation[:150]}"
            for s in state.investigation_steps
        ]) or "No investigation steps recorded"
        
        return SYNTHESIS_PROMPT.format(
            incident_id=state.incident_id,
            log_group=state.log_group or "Unknown",
            duration_ms=f"{duration_ms:.2f}",
            step_count=len(state.investigation_steps),
            log_findings=log_findings,
            metrics_findings=metrics_findings,
            deploy_findings=deploy_findings,
            investigation_steps=steps_text,
        )
    
    @staticmethod
    def format_observation_prompt(
        agent_name: str,
        execution_time: float,
        success: bool,
        summary: str,
        raw_output: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Format the observation prompt after an agent call.
        
        Args:
            agent_name: Name of the agent that was called.
            execution_time: Execution time in milliseconds.
            success: Whether the agent call succeeded.
            summary: Agent's summary of findings.
            raw_output: Optional raw output for details.
            
        Returns:
            Formatted observation prompt.
        """
        import json
        
        raw_text = "N/A"
        if raw_output:
            try:
                raw_text = json.dumps(raw_output, indent=2, default=str)[:1000]
            except Exception:
                raw_text = str(raw_output)[:1000]
        
        return OBSERVATION_PROMPT.format(
            agent_name=agent_name,
            execution_time=f"{execution_time:.2f}",
            success="Yes" if success else "No",
            findings_summary=summary,
            raw_output=raw_text,
        )
