"""
LangGraph State Schema for Incident Investigation

This module defines the typed state schema used throughout the investigation
workflow. The state is immutable and passed between agent nodes, accumulating
investigation findings at each step.

State Flow:
1. Initial state created from CloudWatch log input
2. Orchestrator decides which agent to invoke
3. Agent processes and returns findings
4. State updated with new findings
5. Orchestrator evaluates if investigation is complete
6. Final report synthesized from accumulated state
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, Any, TypedDict
from dataclasses import dataclass, field


class InvestigationStatus(str, Enum):
    """Status of the investigation workflow."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_AGENT = "awaiting_agent"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    FAILED = "failed"


class Severity(str, Enum):
    """Severity levels for findings."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class AgentType(str, Enum):
    """Types of sub-agents available for investigation."""
    LOG_AGENT = "log_agent"
    METRICS_AGENT = "metrics_agent"
    DEPLOY_AGENT = "deploy_agent"


@dataclass
class LogFinding:
    """
    Represents a finding from the Log Analysis Agent.
    
    Captures error traces, patterns, and contextual information
    extracted from CloudWatch log events.
    """
    error_type: str
    message: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    traceback: Optional[str] = None
    request_id: Optional[str] = None
    timestamp: Optional[datetime] = None
    severity: Severity = Severity.MEDIUM
    patterns: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for Redis storage."""
        return {
            "error_type": self.error_type,
            "message": self.message,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "traceback": self.traceback,
            "request_id": self.request_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "severity": self.severity.value,
            "patterns": self.patterns,
        }


@dataclass
class MetricAnomaly:
    """
    Represents an anomaly detected by the Metrics Agent.
    
    Captures performance metrics, thresholds, and anomaly details
    extracted from CloudWatch log metrics.
    """
    metric_name: str
    current_value: float
    baseline_value: float
    threshold: float
    deviation_percent: float
    timestamp: Optional[datetime] = None
    component: Optional[str] = None
    severity: Severity = Severity.MEDIUM
    related_metrics: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for Redis storage."""
        return {
            "metric_name": self.metric_name,
            "current_value": self.current_value,
            "baseline_value": self.baseline_value,
            "threshold": self.threshold,
            "deviation_percent": self.deviation_percent,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "component": self.component,
            "severity": self.severity.value,
            "related_metrics": self.related_metrics,
        }


@dataclass
class DeploymentEvent:
    """
    Represents a deployment event from the Deploy Intelligence Agent.
    
    Tracks CI/CD deployments and configuration changes that may
    correlate with incident timing.
    """
    deployment_id: str
    timestamp: datetime
    service: str
    change_type: str  # "code_deploy", "config_change", "rollback", etc.
    version: Optional[str] = None
    commit_sha: Optional[str] = None
    author: Optional[str] = None
    changes_summary: Optional[str] = None
    is_suspect: bool = False
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for Redis storage."""
        return {
            "deployment_id": self.deployment_id,
            "timestamp": self.timestamp.isoformat(),
            "service": self.service,
            "change_type": self.change_type,
            "version": self.version,
            "commit_sha": self.commit_sha,
            "author": self.author,
            "changes_summary": self.changes_summary,
            "is_suspect": self.is_suspect,
        }


@dataclass
class AgentResult:
    """
    Wrapper for results returned by any sub-agent.
    
    Provides a consistent interface for the orchestrator to
    process results from different agent types.
    """
    agent_type: AgentType
    success: bool
    execution_time_ms: float
    findings: list[Any]  # LogFinding | MetricAnomaly | DeploymentEvent
    summary: str
    error: Optional[str] = None
    raw_output: Optional[dict[str, Any]] = None
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for state storage."""
        return {
            "agent_type": self.agent_type.value,
            "success": self.success,
            "execution_time_ms": self.execution_time_ms,
            "findings": [f.to_dict() for f in self.findings],
            "summary": self.summary,
            "error": self.error,
        }


class CloudWatchLogEvent(TypedDict):
    """Schema for a single CloudWatch log event."""
    id: str
    timestamp: int
    message: str


class CloudWatchLog(TypedDict):
    """Schema for CloudWatch log input."""
    messageType: str
    owner: str
    logGroup: str
    logStream: str
    subscriptionFilters: list[str]
    logEvents: list[CloudWatchLogEvent]


@dataclass
class InvestigationStep:
    """Records a single step in the investigation process."""
    step_number: int
    agent_type: AgentType
    thought: str  # ReAct thought process
    action: str   # Action taken
    observation: str  # Result observed
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "step_number": self.step_number,
            "agent_type": self.agent_type.value,
            "thought": self.thought,
            "action": self.action,
            "observation": self.observation,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class InvestigationReport:
    """
    Final investigation report synthesized by the orchestrator.
    
    Contains all findings, root cause analysis, and recommendations.
    """
    incident_id: str
    title: str
    status: InvestigationStatus
    root_cause: str
    severity: Severity
    timeline: list[dict[str, Any]]
    recommendations: list[str]
    affected_services: list[str]
    investigation_steps: list[InvestigationStep]
    log_findings: list[LogFinding]
    metric_anomalies: list[MetricAnomaly]
    deployment_events: list[DeploymentEvent]
    created_at: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for output."""
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "status": self.status.value,
            "root_cause": self.root_cause,
            "severity": self.severity.value,
            "timeline": self.timeline,
            "recommendations": self.recommendations,
            "affected_services": self.affected_services,
            "investigation_steps": [s.to_dict() for s in self.investigation_steps],
            "log_findings": [f.to_dict() for f in self.log_findings],
            "metric_anomalies": [m.to_dict() for m in self.metric_anomalies],
            "deployment_events": [d.to_dict() for d in self.deployment_events],
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class InvestigationState:
    """
    Main state object passed through the LangGraph workflow.
    
    This is the central data structure that accumulates findings
    as the investigation progresses through different agents.
    
    LangGraph State Requirements:
    - All fields must be serializable
    - State is immutable; new state created on updates
    - Supports checkpointing for resumable investigations
    """
    # Investigation metadata
    incident_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: InvestigationStatus = InvestigationStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    # Input data
    raw_input: Optional[dict[str, Any]] = None
    log_group: Optional[str] = None
    log_stream: Optional[str] = None
    log_events: list[dict[str, Any]] = field(default_factory=list)
    
    # Orchestrator state (ReAct)
    current_thought: str = ""
    current_plan: list[str] = field(default_factory=list)
    next_agent: Optional[AgentType] = None
    iteration_count: int = 0
    max_iterations: int = 10
    
    # Agent results
    log_agent_result: Optional[AgentResult] = None
    metrics_agent_result: Optional[AgentResult] = None
    deploy_agent_result: Optional[AgentResult] = None
    
    # Accumulated findings
    log_findings: list[LogFinding] = field(default_factory=list)
    metric_anomalies: list[MetricAnomaly] = field(default_factory=list)
    deployment_events: list[DeploymentEvent] = field(default_factory=list)
    
    # Investigation history
    investigation_steps: list[InvestigationStep] = field(default_factory=list)
    messages: list[dict[str, str]] = field(default_factory=list)
    
    # Final output
    final_report: Optional[InvestigationReport] = None
    error: Optional[str] = None
    
    def to_dict(self) -> dict[str, Any]:
        """
        Convert state to dictionary for Redis storage and checkpointing.
        
        Returns:
            Dictionary representation of the full state.
        """
        return {
            "incident_id": self.incident_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "log_group": self.log_group,
            "log_stream": self.log_stream,
            "log_events": self.log_events,
            "current_thought": self.current_thought,
            "current_plan": self.current_plan,
            "next_agent": self.next_agent.value if self.next_agent else None,
            "iteration_count": self.iteration_count,
            "max_iterations": self.max_iterations,
            "log_agent_result": self.log_agent_result.to_dict() if self.log_agent_result else None,
            "metrics_agent_result": self.metrics_agent_result.to_dict() if self.metrics_agent_result else None,
            "deploy_agent_result": self.deploy_agent_result.to_dict() if self.deploy_agent_result else None,
            "log_findings": [f.to_dict() for f in self.log_findings],
            "metric_anomalies": [m.to_dict() for m in self.metric_anomalies],
            "deployment_events": [d.to_dict() for d in self.deployment_events],
            "investigation_steps": [s.to_dict() for s in self.investigation_steps],
            "final_report": self.final_report.to_dict() if self.final_report else None,
            "error": self.error,
        }
    
    @classmethod
    def from_cloudwatch_log(cls, log_data: CloudWatchLog) -> "InvestigationState":
        """
        Create initial state from CloudWatch log input.
        
        Args:
            log_data: Parsed CloudWatch log JSON.
            
        Returns:
            New InvestigationState initialized with log data.
        """
        # Cast TypedDict fields to list[dict[str, Any]] for compatibility
        log_events: list[dict[str, Any]] = list(log_data.get("logEvents", []))  # type: ignore[arg-type]
        
        return cls(
            raw_input=dict(log_data),
            log_group=log_data.get("logGroup", ""),
            log_stream=log_data.get("logStream", ""),
            log_events=log_events,
            status=InvestigationStatus.PENDING,
        )
    
    def add_investigation_step(
        self,
        agent_type: AgentType,
        thought: str,
        action: str,
        observation: str,
    ) -> "InvestigationState":
        """
        Add a new investigation step (immutable update).
        
        Args:
            agent_type: The agent that performed the step.
            thought: The reasoning behind the action.
            action: The action taken.
            observation: The result observed.
            
        Returns:
            New state with the step added.
        """
        step = InvestigationStep(
            step_number=len(self.investigation_steps) + 1,
            agent_type=agent_type,
            thought=thought,
            action=action,
            observation=observation,
        )
        # Create new list with added step
        new_steps = self.investigation_steps + [step]
        # Return new state (immutable pattern)
        return InvestigationState(
            incident_id=self.incident_id,
            status=self.status,
            created_at=self.created_at,
            updated_at=datetime.utcnow(),
            raw_input=self.raw_input,
            log_group=self.log_group,
            log_stream=self.log_stream,
            log_events=self.log_events,
            current_thought=self.current_thought,
            current_plan=self.current_plan,
            next_agent=self.next_agent,
            iteration_count=self.iteration_count,
            max_iterations=self.max_iterations,
            log_agent_result=self.log_agent_result,
            metrics_agent_result=self.metrics_agent_result,
            deploy_agent_result=self.deploy_agent_result,
            log_findings=self.log_findings,
            metric_anomalies=self.metric_anomalies,
            deployment_events=self.deployment_events,
            investigation_steps=new_steps,
            messages=self.messages,
            final_report=self.final_report,
            error=self.error,
        )
