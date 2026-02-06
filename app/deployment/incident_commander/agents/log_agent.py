"""
Log Analysis Agent

This agent provides Sentry-like analysis of CloudWatch log events to:
- Extract error tracebacks and stack traces
- Identify error types, file locations, and line numbers
- Detect patterns and recurring issues
- Store findings in Redis (sent_log_memory)
- Track analysis with OpenTelemetry spans

The agent uses regex-based parsing for structured extraction and
LLM-based analysis for pattern recognition.

Usage:
    agent = LogAgent(bedrock_client, redis_memory)
    result = await agent.analyze(log_events, incident_id)
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from app.deployment.incident_commander.shared.state import (
    AgentResult,
    AgentType,
    LogFinding,
    Severity,
)
from app.deployment.incident_commander.shared.bedrock_client import BedrockClient
from app.deployment.incident_commander.shared.redis_client import RedisMemory

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


# Regex patterns for extracting stack traces and errors
PYTHON_TRACEBACK_PATTERN = re.compile(
    r'Traceback \(most recent call last\):.*?(?=\n\n|\Z)',
    re.DOTALL | re.MULTILINE
)

PYTHON_ERROR_LINE_PATTERN = re.compile(
    r'File ["\']([^"\']+)["\'], line (\d+), in (\w+)'
)

PYTHON_EXCEPTION_PATTERN = re.compile(
    r'^(\w+(?:Error|Exception|Warning)): (.+)$',
    re.MULTILINE
)

# Common error type patterns
ERROR_TYPE_PATTERNS = {
    "database": re.compile(r'(database|db|sql|connection.*pool|timeout.*connect)', re.IGNORECASE),
    "memory": re.compile(r'(out of memory|memory.*error|heap|gc.*pressure)', re.IGNORECASE),
    "timeout": re.compile(r'(timeout|timed out|deadline.*exceeded)', re.IGNORECASE),
    "authentication": re.compile(r'(auth.*fail|unauthorized|forbidden|invalid.*token)', re.IGNORECASE),
    "network": re.compile(r'(network|connection.*refused|socket|dns|host.*not.*found)', re.IGNORECASE),
    "validation": re.compile(r'(validation|invalid|malformed|schema.*error)', re.IGNORECASE),
    "rate_limit": re.compile(r'(rate.*limit|throttl|too.*many.*requests)', re.IGNORECASE),
}

# Log level patterns
LOG_LEVEL_PATTERN = re.compile(
    r'\b(ERROR|WARN|WARNING|INFO|DEBUG|CRITICAL|FATAL)\b',
    re.IGNORECASE
)

REQUEST_ID_PATTERN = re.compile(
    r'request_id[=:\s]+([a-zA-Z0-9-_]+)',
    re.IGNORECASE
)


@dataclass
class LogContext:
    """Context for log analysis."""
    request_ids: set[str] = field(default_factory=set)
    error_counts: dict[str, int] = field(default_factory=dict)
    first_error_timestamp: Optional[datetime] = None
    last_error_timestamp: Optional[datetime] = None


class LogAgent:
    """
    Log Analysis Agent for error extraction and pattern detection.
    
    This agent performs Sentry-like analysis of log events:
    1. Parses log messages for errors and warnings
    2. Extracts stack traces and error locations
    3. Identifies patterns (recurring errors, error bursts)
    4. Stores findings in Redis for deduplication
    5. Reports with OpenTelemetry spans for observability
    
    The agent can process raw log strings or structured CloudWatch
    log events, adapting its parsing strategy accordingly.
    
    Example:
        agent = LogAgent(bedrock_client, redis_memory)
        result = await agent.analyze(
            log_events=[{"id": "evt1", "message": "ERROR..."}],
            incident_id="inc-123"
        )
        print(result.findings)  # List[LogFinding]
    """
    
    def __init__(
        self,
        bedrock_client: BedrockClient,
        redis_memory: RedisMemory,
        timeout_seconds: int = 30,
    ):
        """
        Initialize the Log Agent.
        
        Args:
            bedrock_client: Client for LLM-based analysis.
            redis_memory: Redis client for storing findings.
            timeout_seconds: Maximum execution time.
        """
        self.bedrock = bedrock_client
        self.memory = redis_memory
        self.timeout = timeout_seconds
        
        logger.info("LogAgent initialized")
    
    async def analyze(
        self,
        log_events: list[dict[str, Any]],
        incident_id: str,
    ) -> AgentResult:
        """
        Analyze log events for errors and patterns.
        
        This is the main entry point for log analysis. It:
        1. Extracts errors from each log event
        2. Parses stack traces for file/line info
        3. Identifies patterns across events
        4. Stores findings in Redis
        5. Returns structured results
        
        Args:
            log_events: List of CloudWatch log event dicts.
            incident_id: ID for storing findings.
            
        Returns:
            AgentResult with findings and summary.
        """
        start_time = time.time()
        
        with tracer.start_as_current_span("log_agent.analyze") as span:
            span.set_attribute("incident_id", incident_id)
            span.set_attribute("event_count", len(log_events))
            
            try:
                findings: list[LogFinding] = []
                context = LogContext()
                
                # Process each log event
                for event in log_events:
                    with tracer.start_span("log_agent.process_event") as event_span:
                        event_findings = self._process_event(event, context)
                        findings.extend(event_findings)
                        event_span.set_attribute("findings_count", len(event_findings))
                
                # Analyze patterns across findings
                pattern_summary = self._analyze_patterns(findings, context)
                
                # Store findings in Redis (sent_log_memory)
                for finding in findings:
                    self.memory.store_log_finding(incident_id, finding.to_dict())
                
                # Generate summary using LLM if we have significant findings
                summary = await self._generate_summary(findings, context)
                
                execution_time = (time.time() - start_time) * 1000
                span.set_attribute("execution_time_ms", execution_time)
                span.set_attribute("total_findings", len(findings))
                span.set_status(Status(StatusCode.OK))
                
                return AgentResult(
                    agent_type=AgentType.LOG_AGENT,
                    success=True,
                    execution_time_ms=execution_time,
                    findings=findings,
                    summary=summary,
                    raw_output={
                        "pattern_summary": pattern_summary,
                        "context": {
                            "request_ids": list(context.request_ids),
                            "error_counts": context.error_counts,
                        },
                    },
                )
                
            except Exception as e:
                logger.error(f"Log analysis failed: {e}")
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                
                return AgentResult(
                    agent_type=AgentType.LOG_AGENT,
                    success=False,
                    execution_time_ms=(time.time() - start_time) * 1000,
                    findings=[],
                    summary="Log analysis failed",
                    error=str(e),
                )
    
    def _process_event(
        self,
        event: dict[str, Any],
        context: LogContext,
    ) -> list[LogFinding]:
        """
        Process a single log event to extract findings.
        
        Args:
            event: CloudWatch log event dict.
            context: Shared context for pattern tracking.
            
        Returns:
            List of findings from this event.
        """
        findings = []
        message = event.get("message", "")
        timestamp = self._parse_timestamp(event.get("timestamp"))
        
        # Extract request ID if present
        request_id_match = REQUEST_ID_PATTERN.search(message)
        request_id = request_id_match.group(1) if request_id_match else None
        if request_id:
            context.request_ids.add(request_id)
        
        # Check for log level
        level_match = LOG_LEVEL_PATTERN.search(message)
        if not level_match:
            return findings  # Skip non-error logs
        
        level = level_match.group(1).upper()
        if level not in ("ERROR", "CRITICAL", "FATAL", "WARN", "WARNING"):
            return findings  # Skip info/debug
        
        # Extract Python tracebacks
        traceback_findings = self._extract_tracebacks(message, request_id, timestamp)
        findings.extend(traceback_findings)
        
        # If no traceback, look for inline errors
        if not traceback_findings:
            inline_findings = self._extract_inline_errors(
                message, request_id, timestamp, level
            )
            findings.extend(inline_findings)
        
        # Update context with error counts
        for finding in findings:
            error_type = finding.error_type
            context.error_counts[error_type] = context.error_counts.get(error_type, 0) + 1
        
        # Track timestamp range
        if timestamp:
            if not context.first_error_timestamp or timestamp < context.first_error_timestamp:
                context.first_error_timestamp = timestamp
            if not context.last_error_timestamp or timestamp > context.last_error_timestamp:
                context.last_error_timestamp = timestamp
        
        return findings
    
    def _extract_tracebacks(
        self,
        message: str,
        request_id: Optional[str],
        timestamp: Optional[datetime],
    ) -> list[LogFinding]:
        """
        Extract Python traceback information from a log message.
        
        Args:
            message: Log message text.
            request_id: Associated request ID.
            timestamp: Event timestamp.
            
        Returns:
            List of findings from tracebacks.
        """
        findings = []
        
        # Find all tracebacks
        traceback_matches = PYTHON_TRACEBACK_PATTERN.findall(message)
        
        for traceback_text in traceback_matches:
            # Extract file locations
            file_matches = PYTHON_ERROR_LINE_PATTERN.findall(traceback_text)
            
            # Extract exception type and message
            exception_match = PYTHON_EXCEPTION_PATTERN.search(traceback_text)
            
            if exception_match:
                error_type = exception_match.group(1)
                error_message = exception_match.group(2)
            else:
                error_type = "UnknownError"
                error_message = traceback_text[:200]
            
            # Get the deepest (most recent) file location
            file_path = None
            line_number = None
            if file_matches:
                last_match = file_matches[-1]
                file_path = last_match[0]
                line_number = int(last_match[1])
            
            # Determine severity based on error type
            severity = self._classify_severity(error_type)
            
            # Detect patterns
            patterns = self._detect_error_patterns(traceback_text)
            
            finding = LogFinding(
                error_type=error_type,
                message=error_message,
                file_path=file_path,
                line_number=line_number,
                traceback=traceback_text,
                request_id=request_id,
                timestamp=timestamp,
                severity=severity,
                patterns=patterns,
            )
            findings.append(finding)
        
        return findings
    
    def _extract_inline_errors(
        self,
        message: str,
        request_id: Optional[str],
        timestamp: Optional[datetime],
        level: str,
    ) -> list[LogFinding]:
        """
        Extract error information from inline log messages (no traceback).
        
        Args:
            message: Log message text.
            request_id: Associated request ID.
            timestamp: Event timestamp.
            level: Log level (ERROR, WARN, etc.).
            
        Returns:
            List of findings from inline errors.
        """
        findings = []
        
        # Look for common error patterns
        error_type = "LogError"
        patterns = []
        
        for pattern_name, pattern in ERROR_TYPE_PATTERNS.items():
            if pattern.search(message):
                error_type = f"{pattern_name.title()}Error"
                patterns.append(pattern_name)
                break
        
        # Extract error message (after log level)
        parts = message.split(level, 1)
        error_message = parts[-1].strip() if len(parts) > 1 else message
        
        # Limit message length
        if len(error_message) > 500:
            error_message = error_message[:497] + "..."
        
        severity = Severity.MEDIUM
        if level in ("CRITICAL", "FATAL"):
            severity = Severity.CRITICAL
        elif level == "ERROR":
            severity = Severity.HIGH
        elif level in ("WARN", "WARNING"):
            severity = Severity.MEDIUM
        
        finding = LogFinding(
            error_type=error_type,
            message=error_message,
            request_id=request_id,
            timestamp=timestamp,
            severity=severity,
            patterns=patterns,
        )
        findings.append(finding)
        
        return findings
    
    def _classify_severity(self, error_type: str) -> Severity:
        """
        Classify error severity based on error type.
        
        Args:
            error_type: Name of the exception type.
            
        Returns:
            Severity level.
        """
        critical_errors = {
            "MemoryError", "SystemExit", "KeyboardInterrupt",
            "OutOfMemoryError", "SegmentationFault",
        }
        high_errors = {
            "ConnectionError", "TimeoutError", "DatabaseError",
            "AuthenticationError", "PermissionError", "IOError",
        }
        
        if error_type in critical_errors:
            return Severity.CRITICAL
        elif error_type in high_errors or "Error" in error_type:
            return Severity.HIGH
        elif "Warning" in error_type:
            return Severity.MEDIUM
        else:
            return Severity.MEDIUM
    
    def _detect_error_patterns(self, text: str) -> list[str]:
        """
        Detect known error patterns in text.
        
        Args:
            text: Text to analyze.
            
        Returns:
            List of detected pattern names.
        """
        patterns = []
        
        for pattern_name, pattern in ERROR_TYPE_PATTERNS.items():
            if pattern.search(text):
                patterns.append(pattern_name)
        
        return patterns
    
    def _analyze_patterns(
        self,
        findings: list[LogFinding],
        context: LogContext,
    ) -> dict[str, Any]:
        """
        Analyze patterns across all findings.
        
        Args:
            findings: All extracted findings.
            context: Analysis context.
            
        Returns:
            Pattern analysis summary.
        """
        summary = {
            "total_errors": len(findings),
            "unique_error_types": len(context.error_counts),
            "error_type_counts": context.error_counts,
            "affected_requests": len(context.request_ids),
            "time_span_seconds": None,
            "is_error_burst": False,
            "dominant_error_type": None,
        }
        
        # Calculate time span
        if context.first_error_timestamp and context.last_error_timestamp:
            delta = context.last_error_timestamp - context.first_error_timestamp
            summary["time_span_seconds"] = delta.total_seconds()
            
            # Detect error burst (many errors in short time)
            if len(findings) > 5 and delta.total_seconds() < 60:
                summary["is_error_burst"] = True
        
        # Find dominant error type
        if context.error_counts:
            dominant = max(context.error_counts.keys(), key=lambda k: context.error_counts[k])
            summary["dominant_error_type"] = dominant
        
        return summary
    
    async def _generate_summary(
        self,
        findings: list[LogFinding],
        context: LogContext,
    ) -> str:
        """
        Generate a natural language summary of findings.
        
        Uses LLM to synthesize findings into actionable summary.
        Falls back to template-based summary if LLM fails.
        
        Args:
            findings: All extracted findings.
            context: Analysis context.
            
        Returns:
            Summary string.
        """
        if not findings:
            return "No errors or warnings found in the log events."
        
        # Try LLM-based summary
        try:
            prompt = self._build_summary_prompt(findings, context)
            result = await self.bedrock.invoke_async(
                prompt=prompt,
                system_prompt=(
                    "You are a log analysis expert. Provide a concise summary "
                    "of the error findings, focusing on actionable insights. "
                    "Be specific about error types, patterns, and potential causes."
                ),
                max_tokens=500,
            )
            return result.content
        except Exception as e:
            logger.warning(f"LLM summary failed, using template: {e}")
        
        # Fallback to template summary
        return self._template_summary(findings, context)
    
    def _build_summary_prompt(
        self,
        findings: list[LogFinding],
        context: LogContext,
    ) -> str:
        """Build prompt for LLM summary generation."""
        error_list = []
        for f in findings[:10]:  # Limit to top 10
            error_list.append(f"- {f.error_type}: {f.message[:100]}")
        
        return f"""Summarize these log analysis findings:

Total Errors: {len(findings)}
Unique Error Types: {len(context.error_counts)}
Error Counts: {context.error_counts}
Affected Requests: {len(context.request_ids)}

Top Errors:
{chr(10).join(error_list)}

Provide a concise 2-3 sentence summary focusing on:
1. The primary error type and its frequency
2. Any patterns (error bursts, cascading failures)
3. Likely root cause or investigation direction"""
    
    def _template_summary(
        self,
        findings: list[LogFinding],
        context: LogContext,
    ) -> str:
        """Generate template-based summary as fallback."""
        error_count = len(findings)
        unique_types = len(context.error_counts)
        
        parts = [f"Found {error_count} error(s) of {unique_types} type(s)."]
        
        if context.error_counts:
            dominant = max(context.error_counts.keys(), key=lambda k: context.error_counts[k])
            count = context.error_counts[dominant]
            parts.append(f"Most frequent: {dominant} ({count} occurrences).")
        
        # Check for patterns
        all_patterns = set()
        for f in findings:
            all_patterns.update(f.patterns)
        
        if all_patterns:
            parts.append(f"Detected patterns: {', '.join(all_patterns)}.")
        
        return " ".join(parts)
    
    def _parse_timestamp(self, ts: Any) -> Optional[datetime]:
        """Parse timestamp from various formats."""
        if ts is None:
            return None
        if isinstance(ts, datetime):
            return ts
        if isinstance(ts, (int, float)):
            # Assume milliseconds
            return datetime.fromtimestamp(ts / 1000)
        if isinstance(ts, str):
            try:
                return datetime.fromisoformat(ts)
            except ValueError:
                return None
        return None
