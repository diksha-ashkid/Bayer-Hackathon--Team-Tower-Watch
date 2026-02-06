"""
Metrics Analysis Agent

This agent analyzes performance metrics from CloudWatch logs to:
- Extract request counts, error rates, and latencies
- Detect anomalies in performance metrics
- Monitor confidence scores for ML services
- Export metrics via OpenTelemetry/OTLP

The agent parses structured log entries to extract metric values
and applies statistical analysis for anomaly detection.

Usage:
    agent = MetricsAgent(bedrock_client, redis_memory)
    result = await agent.analyze(log_events, incident_id)
"""

from __future__ import annotations

import logging
import re
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from opentelemetry import trace, metrics
from opentelemetry.trace import Status, StatusCode
from opentelemetry.metrics import Counter, Histogram

from app.deployment.incident_commander.shared.state import (
    AgentResult,
    AgentType,
    MetricAnomaly,
    Severity,
)
from app.deployment.incident_commander.shared.bedrock_client import BedrockClient
from app.deployment.incident_commander.shared.redis_client import RedisMemory

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# Initialize OpenTelemetry metrics
meter = metrics.get_meter(__name__)

# Counter for processed events
events_processed_counter = meter.create_counter(
    name="incident_commander.metrics_agent.events_processed",
    description="Number of log events processed by metrics agent",
    unit="1",
)

# Histogram for detected latencies
latency_histogram = meter.create_histogram(
    name="incident_commander.metrics_agent.detected_latency",
    description="Latency values detected in logs",
    unit="ms",
)

# Counter for detected anomalies
anomalies_counter = meter.create_counter(
    name="incident_commander.metrics_agent.anomalies_detected",
    description="Number of metric anomalies detected",
    unit="1",
)


# Regex patterns for metric extraction
LATENCY_PATTERNS = [
    re.compile(r'latency[=:\s]+(\d+(?:\.\d+)?)\s*(?:ms|milliseconds)?', re.IGNORECASE),
    re.compile(r'duration[=:\s]+(\d+(?:\.\d+)?)\s*(?:ms|milliseconds)?', re.IGNORECASE),
    re.compile(r'response_time[=:\s]+(\d+(?:\.\d+)?)\s*(?:ms|milliseconds)?', re.IGNORECASE),
    re.compile(r'took[=:\s]+(\d+(?:\.\d+)?)\s*(?:ms|milliseconds)?', re.IGNORECASE),
    re.compile(r'elapsed[=:\s]+(\d+(?:\.\d+)?)\s*(?:ms|milliseconds)?', re.IGNORECASE),
]

MEMORY_PATTERNS = [
    re.compile(r'memory[=:\s]+(\d+(?:\.\d+)?)\s*(?:MB|mb|GB|gb)?', re.IGNORECASE),
    re.compile(r'heap[=:\s]+(\d+(?:\.\d+)?)\s*(?:MB|mb)?', re.IGNORECASE),
    re.compile(r'rss[=:\s]+(\d+(?:\.\d+)?)', re.IGNORECASE),
]

CPU_PATTERNS = [
    re.compile(r'cpu[=:\s]+(\d+(?:\.\d+)?)\s*%?', re.IGNORECASE),
    re.compile(r'cpu_usage[=:\s]+(\d+(?:\.\d+)?)', re.IGNORECASE),
]

ERROR_RATE_PATTERNS = [
    re.compile(r'error_rate[=:\s]+(\d+(?:\.\d+)?)', re.IGNORECASE),
    re.compile(r'failure_rate[=:\s]+(\d+(?:\.\d+)?)', re.IGNORECASE),
]

CONFIDENCE_PATTERNS = [
    re.compile(r'confidence[=:\s]+(\d+(?:\.\d+)?)', re.IGNORECASE),
    re.compile(r'score[=:\s]+(\d+(?:\.\d+)?)', re.IGNORECASE),
    re.compile(r'accuracy[=:\s]+(\d+(?:\.\d+)?)', re.IGNORECASE),
]

DB_PATTERNS = [
    re.compile(r'db_latency[=:\s]+(\d+(?:\.\d+)?)', re.IGNORECASE),
    re.compile(r'query_time[=:\s]+(\d+(?:\.\d+)?)', re.IGNORECASE),
    re.compile(r'connection_pool[=:\s]+(\d+)/(\d+)', re.IGNORECASE),
    re.compile(r'db.*timeout', re.IGNORECASE),
    re.compile(r'connection.*timeout', re.IGNORECASE),
]

REQUEST_COUNT_PATTERN = re.compile(
    r'(?:requests?|rps)[=:\s]+(\d+)',
    re.IGNORECASE
)

ENDPOINT_PATTERN = re.compile(
    r'endpoint[=:\s]+([^\s]+)',
    re.IGNORECASE
)


@dataclass
class MetricSeries:
    """Time series data for a single metric."""
    name: str
    values: list[float] = field(default_factory=list)
    timestamps: list[datetime] = field(default_factory=list)
    component: Optional[str] = None
    
    @property
    def mean(self) -> float:
        """Calculate mean of values."""
        return statistics.mean(self.values) if self.values else 0.0
    
    @property
    def std_dev(self) -> float:
        """Calculate standard deviation."""
        return statistics.stdev(self.values) if len(self.values) > 1 else 0.0
    
    @property
    def max_value(self) -> float:
        """Get maximum value."""
        return max(self.values) if self.values else 0.0
    
    @property
    def min_value(self) -> float:
        """Get minimum value."""
        return min(self.values) if self.values else 0.0


@dataclass
class AnomalyThresholds:
    """Thresholds for anomaly detection."""
    latency_ms: float = 500.0  # Latency above 500ms is concerning
    latency_critical_ms: float = 2000.0  # Latency above 2s is critical
    error_rate_percent: float = 5.0  # Error rate above 5% is concerning
    cpu_percent: float = 80.0  # CPU above 80% is concerning
    memory_percent: float = 85.0  # Memory above 85% is concerning
    std_dev_multiplier: float = 2.5  # Values > 2.5 std devs from mean


class MetricsAgent:
    """
    Metrics Analysis Agent for performance anomaly detection.
    
    This agent analyzes CloudWatch log events to:
    1. Extract performance metrics (latency, CPU, memory)
    2. Build time series for statistical analysis
    3. Detect anomalies using threshold and statistical methods
    4. Export metrics via OpenTelemetry for observability
    5. Correlate metrics with service components
    
    Anomaly Detection Methods:
    - Threshold-based: Absolute limits for known metrics
    - Statistical: Z-score analysis for deviation from baseline
    - Pattern-based: Detecting concerning patterns (spikes, trends)
    
    Example:
        agent = MetricsAgent(bedrock_client, redis_memory)
        result = await agent.analyze(
            log_events=[{"id": "evt1", "message": "latency=2000ms"}],
            incident_id="inc-123"
        )
        print(result.findings)  # List[MetricAnomaly]
    """
    
    def __init__(
        self,
        bedrock_client: BedrockClient,
        redis_memory: RedisMemory,
        thresholds: Optional[AnomalyThresholds] = None,
        timeout_seconds: int = 30,
    ):
        """
        Initialize the Metrics Agent.
        
        Args:
            bedrock_client: Client for LLM-based analysis.
            redis_memory: Redis client for storing metrics.
            thresholds: Custom anomaly thresholds.
            timeout_seconds: Maximum execution time.
        """
        self.bedrock = bedrock_client
        self.memory = redis_memory
        self.thresholds = thresholds or AnomalyThresholds()
        self.timeout = timeout_seconds
        
        logger.info("MetricsAgent initialized")
    
    async def analyze(
        self,
        log_events: list[dict[str, Any]],
        incident_id: str,
    ) -> AgentResult:
        """
        Analyze log events for metric anomalies.
        
        This method:
        1. Extracts metrics from each log event
        2. Builds time series for each metric
        3. Applies anomaly detection algorithms
        4. Exports metrics via OpenTelemetry
        5. Stores findings in Redis
        
        Args:
            log_events: List of CloudWatch log event dicts.
            incident_id: ID for storing findings.
            
        Returns:
            AgentResult with anomalies and summary.
        """
        start_time = time.time()
        
        with tracer.start_as_current_span("metrics_agent.analyze") as span:
            span.set_attribute("incident_id", incident_id)
            span.set_attribute("event_count", len(log_events))
            
            try:
                # Extract metrics from all events
                metric_series = self._extract_all_metrics(log_events)
                span.set_attribute("metric_count", len(metric_series))
                
                # Record OpenTelemetry metrics
                events_processed_counter.add(len(log_events))
                
                # Detect anomalies
                anomalies = self._detect_anomalies(metric_series)
                
                # Record anomaly count
                anomalies_counter.add(len(anomalies))
                
                # Store metrics summary in Redis
                summary_dict = self._build_metrics_summary(metric_series, anomalies)
                self.memory.store_metrics_summary(incident_id, summary_dict)
                
                # Generate summary
                summary = await self._generate_summary(metric_series, anomalies)
                
                execution_time = (time.time() - start_time) * 1000
                span.set_attribute("execution_time_ms", execution_time)
                span.set_attribute("anomaly_count", len(anomalies))
                span.set_status(Status(StatusCode.OK))
                
                return AgentResult(
                    agent_type=AgentType.METRICS_AGENT,
                    success=True,
                    execution_time_ms=execution_time,
                    findings=anomalies,
                    summary=summary,
                    raw_output={
                        "metrics_summary": summary_dict,
                        "series_count": len(metric_series),
                    },
                )
                
            except Exception as e:
                logger.error(f"Metrics analysis failed: {e}")
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                
                return AgentResult(
                    agent_type=AgentType.METRICS_AGENT,
                    success=False,
                    execution_time_ms=(time.time() - start_time) * 1000,
                    findings=[],
                    summary="Metrics analysis failed",
                    error=str(e),
                )
    
    def _extract_all_metrics(
        self,
        log_events: list[dict[str, Any]],
    ) -> dict[str, MetricSeries]:
        """
        Extract all metrics from log events.
        
        Args:
            log_events: Log events to process.
            
        Returns:
            Dictionary of metric name to series data.
        """
        series: dict[str, MetricSeries] = defaultdict(
            lambda: MetricSeries(name="")
        )
        
        for event in log_events:
            message = event.get("message", "")
            timestamp = self._parse_timestamp(event.get("timestamp"))
            
            # Extract endpoint for component tagging
            endpoint = None
            endpoint_match = ENDPOINT_PATTERN.search(message)
            if endpoint_match:
                endpoint = endpoint_match.group(1)
            
            # Extract latency metrics
            for pattern in LATENCY_PATTERNS:
                match = pattern.search(message)
                if match:
                    value = float(match.group(1))
                    key = f"latency:{endpoint or 'unknown'}"
                    if series[key].name == "":
                        series[key] = MetricSeries(
                            name="latency",
                            component=endpoint,
                        )
                    series[key].values.append(value)
                    if timestamp:
                        series[key].timestamps.append(timestamp)
                    
                    # Record in OpenTelemetry histogram
                    latency_histogram.record(value, {"endpoint": endpoint or "unknown"})
                    break
            
            # Extract memory metrics
            for pattern in MEMORY_PATTERNS:
                match = pattern.search(message)
                if match:
                    value = float(match.group(1))
                    key = "memory"
                    if series[key].name == "":
                        series[key] = MetricSeries(name="memory")
                    series[key].values.append(value)
                    if timestamp:
                        series[key].timestamps.append(timestamp)
                    break
            
            # Extract CPU metrics
            for pattern in CPU_PATTERNS:
                match = pattern.search(message)
                if match:
                    value = float(match.group(1))
                    key = "cpu"
                    if series[key].name == "":
                        series[key] = MetricSeries(name="cpu")
                    series[key].values.append(value)
                    if timestamp:
                        series[key].timestamps.append(timestamp)
                    break
            
            # Extract error rate
            for pattern in ERROR_RATE_PATTERNS:
                match = pattern.search(message)
                if match:
                    value = float(match.group(1))
                    key = "error_rate"
                    if series[key].name == "":
                        series[key] = MetricSeries(name="error_rate")
                    series[key].values.append(value)
                    if timestamp:
                        series[key].timestamps.append(timestamp)
                    break
            
            # Extract confidence/score metrics
            for pattern in CONFIDENCE_PATTERNS:
                match = pattern.search(message)
                if match:
                    value = float(match.group(1))
                    key = "confidence"
                    if series[key].name == "":
                        series[key] = MetricSeries(name="confidence")
                    series[key].values.append(value)
                    if timestamp:
                        series[key].timestamps.append(timestamp)
                    break
            
            # Check for DB timeouts (pattern-based)
            for pattern in DB_PATTERNS:
                if pattern.search(message):
                    key = "db_issues"
                    if series[key].name == "":
                        series[key] = MetricSeries(name="db_issues")
                    series[key].values.append(1)  # Count of occurrences
                    if timestamp:
                        series[key].timestamps.append(timestamp)
                    break
        
        return dict(series)
    
    def _detect_anomalies(
        self,
        metric_series: dict[str, MetricSeries],
    ) -> list[MetricAnomaly]:
        """
        Detect anomalies in metric series.
        
        Applies multiple detection methods:
        1. Threshold-based: Check against absolute limits
        2. Statistical: Z-score analysis
        3. Pattern-based: Spike detection
        
        Args:
            metric_series: Extracted metrics data.
            
        Returns:
            List of detected anomalies.
        """
        anomalies = []
        
        for key, series in metric_series.items():
            if not series.values:
                continue
            
            # Latency anomaly detection
            if series.name == "latency":
                latency_anomalies = self._detect_latency_anomalies(series)
                anomalies.extend(latency_anomalies)
            
            # CPU anomaly detection
            elif series.name == "cpu":
                cpu_anomalies = self._detect_threshold_anomalies(
                    series,
                    threshold=self.thresholds.cpu_percent,
                    metric_name="CPU Usage",
                    unit="%",
                )
                anomalies.extend(cpu_anomalies)
            
            # Memory anomaly detection
            elif series.name == "memory":
                memory_anomalies = self._detect_threshold_anomalies(
                    series,
                    threshold=self.thresholds.memory_percent,
                    metric_name="Memory Usage",
                    unit="%",
                )
                anomalies.extend(memory_anomalies)
            
            # Error rate anomaly detection
            elif series.name == "error_rate":
                error_anomalies = self._detect_threshold_anomalies(
                    series,
                    threshold=self.thresholds.error_rate_percent,
                    metric_name="Error Rate",
                    unit="%",
                )
                anomalies.extend(error_anomalies)
            
            # DB issues detection
            elif series.name == "db_issues":
                if len(series.values) > 0:
                    anomalies.append(MetricAnomaly(
                        metric_name="Database Connection Issues",
                        current_value=float(len(series.values)),
                        baseline_value=0.0,
                        threshold=1.0,
                        deviation_percent=100.0,
                        timestamp=series.timestamps[0] if series.timestamps else None,
                        component="database",
                        severity=Severity.HIGH,
                        related_metrics=["latency", "error_rate"],
                    ))
        
        return anomalies
    
    def _detect_latency_anomalies(
        self,
        series: MetricSeries,
    ) -> list[MetricAnomaly]:
        """
        Detect latency anomalies using threshold and statistical methods.
        
        Args:
            series: Latency metric series.
            
        Returns:
            List of latency anomalies.
        """
        anomalies = []
        
        max_latency = series.max_value
        mean_latency = series.mean
        std_dev = series.std_dev
        
        # Check for critical threshold breach
        if max_latency >= self.thresholds.latency_critical_ms:
            anomalies.append(MetricAnomaly(
                metric_name="Latency Spike (Critical)",
                current_value=max_latency,
                baseline_value=mean_latency,
                threshold=self.thresholds.latency_critical_ms,
                deviation_percent=(
                    ((max_latency - mean_latency) / mean_latency * 100)
                    if mean_latency > 0 else 100.0
                ),
                timestamp=series.timestamps[-1] if series.timestamps else None,
                component=series.component,
                severity=Severity.CRITICAL,
                related_metrics=["request_count", "error_rate", "db_latency"],
            ))
        
        # Check for high threshold breach
        elif max_latency >= self.thresholds.latency_ms:
            anomalies.append(MetricAnomaly(
                metric_name="Latency Spike (High)",
                current_value=max_latency,
                baseline_value=mean_latency,
                threshold=self.thresholds.latency_ms,
                deviation_percent=(
                    ((max_latency - mean_latency) / mean_latency * 100)
                    if mean_latency > 0 else 100.0
                ),
                timestamp=series.timestamps[-1] if series.timestamps else None,
                component=series.component,
                severity=Severity.HIGH,
                related_metrics=["request_count", "db_latency"],
            ))
        
        # Statistical anomaly detection
        if std_dev > 0 and len(series.values) > 3:
            for i, value in enumerate(series.values):
                z_score = abs(value - mean_latency) / std_dev
                if z_score > self.thresholds.std_dev_multiplier:
                    anomalies.append(MetricAnomaly(
                        metric_name="Latency Statistical Anomaly",
                        current_value=value,
                        baseline_value=mean_latency,
                        threshold=mean_latency + (std_dev * self.thresholds.std_dev_multiplier),
                        deviation_percent=((value - mean_latency) / mean_latency * 100),
                        timestamp=series.timestamps[i] if i < len(series.timestamps) else None,
                        component=series.component,
                        severity=Severity.MEDIUM,
                        related_metrics=["request_count"],
                    ))
        
        return anomalies
    
    def _detect_threshold_anomalies(
        self,
        series: MetricSeries,
        threshold: float,
        metric_name: str,
        unit: str = "",
    ) -> list[MetricAnomaly]:
        """
        Detect anomalies based on threshold breach.
        
        Args:
            series: Metric series to analyze.
            threshold: Threshold value.
            metric_name: Human-readable metric name.
            unit: Unit of measurement.
            
        Returns:
            List of threshold anomalies.
        """
        anomalies = []
        
        max_value = series.max_value
        mean_value = series.mean
        
        if max_value >= threshold:
            severity = Severity.CRITICAL if max_value >= threshold * 1.25 else Severity.HIGH
            
            anomalies.append(MetricAnomaly(
                metric_name=f"{metric_name} Threshold Breach",
                current_value=max_value,
                baseline_value=mean_value,
                threshold=threshold,
                deviation_percent=(
                    ((max_value - threshold) / threshold * 100)
                    if threshold > 0 else 100.0
                ),
                timestamp=series.timestamps[-1] if series.timestamps else None,
                component=series.component,
                severity=severity,
                related_metrics=[],
            ))
        
        return anomalies
    
    def _build_metrics_summary(
        self,
        metric_series: dict[str, MetricSeries],
        anomalies: list[MetricAnomaly],
    ) -> dict[str, Any]:
        """
        Build summary dictionary for Redis storage.
        
        Args:
            metric_series: All extracted metrics.
            anomalies: Detected anomalies.
            
        Returns:
            Summary dictionary.
        """
        summary = {
            "metrics_analyzed": list(metric_series.keys()),
            "total_data_points": sum(len(s.values) for s in metric_series.values()),
            "anomaly_count": len(anomalies),
            "critical_count": len([a for a in anomalies if a.severity == Severity.CRITICAL]),
            "high_count": len([a for a in anomalies if a.severity == Severity.HIGH]),
            "statistics": {},
        }
        
        for key, series in metric_series.items():
            if series.values:
                summary["statistics"][key] = {
                    "mean": series.mean,
                    "std_dev": series.std_dev,
                    "min": series.min_value,
                    "max": series.max_value,
                    "count": len(series.values),
                }
        
        return summary
    
    async def _generate_summary(
        self,
        metric_series: dict[str, MetricSeries],
        anomalies: list[MetricAnomaly],
    ) -> str:
        """
        Generate a natural language summary of metrics analysis.
        
        Args:
            metric_series: Extracted metrics.
            anomalies: Detected anomalies.
            
        Returns:
            Summary string.
        """
        if not metric_series:
            return "No metrics found in the log events."
        
        if not anomalies:
            return (
                f"Analyzed {len(metric_series)} metric types. "
                "No anomalies detected. All metrics within normal ranges."
            )
        
        # Try LLM summary
        try:
            prompt = self._build_summary_prompt(metric_series, anomalies)
            result = await self.bedrock.invoke_async(
                prompt=prompt,
                system_prompt=(
                    "You are a performance metrics analyst. Provide a concise "
                    "summary of the metric anomalies, focusing on impact and "
                    "potential root causes. Be specific and actionable."
                ),
                max_tokens=500,
            )
            return result.content
        except Exception as e:
            logger.warning(f"LLM summary failed, using template: {e}")
        
        return self._template_summary(anomalies)
    
    def _build_summary_prompt(
        self,
        metric_series: dict[str, MetricSeries],
        anomalies: list[MetricAnomaly],
    ) -> str:
        """Build prompt for LLM summary generation."""
        anomaly_list = []
        for a in anomalies[:5]:  # Top 5
            anomaly_list.append(
                f"- {a.metric_name}: {a.current_value:.2f} "
                f"(threshold: {a.threshold:.2f}, severity: {a.severity.value})"
            )
        
        return f"""Summarize these metrics analysis findings:

Metrics Analyzed: {len(metric_series)}
Anomalies Detected: {len(anomalies)}
Critical Anomalies: {len([a for a in anomalies if a.severity == Severity.CRITICAL])}

Top Anomalies:
{chr(10).join(anomaly_list)}

Provide a concise 2-3 sentence summary focusing on:
1. The most critical anomaly and its impact
2. Likely root cause based on metric patterns
3. Recommended next investigation step"""
    
    def _template_summary(self, anomalies: list[MetricAnomaly]) -> str:
        """Generate template-based summary as fallback."""
        critical = [a for a in anomalies if a.severity == Severity.CRITICAL]
        high = [a for a in anomalies if a.severity == Severity.HIGH]
        
        parts = [f"Detected {len(anomalies)} metric anomalie(s)."]
        
        if critical:
            parts.append(f"CRITICAL: {critical[0].metric_name} at {critical[0].current_value:.2f}.")
        
        if high:
            parts.append(f"HIGH: {high[0].metric_name} at {high[0].current_value:.2f}.")
        
        return " ".join(parts)
    
    def _parse_timestamp(self, ts: Any) -> Optional[datetime]:
        """Parse timestamp from various formats."""
        if ts is None:
            return None
        if isinstance(ts, datetime):
            return ts
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts / 1000)
        if isinstance(ts, str):
            try:
                return datetime.fromisoformat(ts)
            except ValueError:
                return None
        return None
