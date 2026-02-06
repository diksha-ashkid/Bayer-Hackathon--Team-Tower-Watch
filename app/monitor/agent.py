"""
Monitor Agent
=============

OpenTelemetry-based monitoring agent that collects metrics from log events
and exports them to an OTLP endpoint.

Features:
- Request counting and error tracking
- Latency histograms
- GPU memory and queue monitoring
- Confidence score tracking
- Status code and prediction class breakdowns
"""

import json
import re
import os
import logging
from typing import Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

# OpenTelemetry imports (optional)
try:
    from opentelemetry import metrics
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    metrics = None
    MeterProvider = None  # type: ignore
    PeriodicExportingMetricReader = None  # type: ignore
    OTLPMetricExporter = None  # type: ignore

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

OTLP_ENDPOINT = os.environ.get("OTLP_ENDPOINT", "http://127.0.0.1:4317")
OTLP_INSECURE = os.environ.get("OTLP_INSECURE", "true").lower() == "true"
METER_NAME = os.environ.get("METER_NAME", "incident-monitor")

# =============================================================================
# REGEX PATTERNS
# =============================================================================

@dataclass
class MetricPatterns:
    """Compiled regex patterns for metric extraction."""
    request_size: re.Pattern = field(default_factory=lambda: re.compile(r"bytes=(\d+)"))
    latency: re.Pattern = field(default_factory=lambda: re.compile(r"latency_ms=(\d+)|latency=(\d+)ms"))
    confidence: re.Pattern = field(default_factory=lambda: re.compile(r"confidence=(\d+\.\d+)"))
    gpu_depth: re.Pattern = field(default_factory=lambda: re.compile(r"gpu_queue_depth=(\d+)"))
    network: re.Pattern = field(default_factory=lambda: re.compile(r"slow_network_ms=(\d+)"))
    status: re.Pattern = field(default_factory=lambda: re.compile(r"status=(\d+)"))
    prediction: re.Pattern = field(default_factory=lambda: re.compile(r"prediction=(\w+)"))


# =============================================================================
# METRICS COLLECTOR
# =============================================================================

class MetricsCollector:
    """
    Collects metrics from log events and tracks them in memory.
    
    Can be used standalone or with OpenTelemetry for export.
    """
    
    def __init__(self):
        self.patterns = MetricPatterns()
        self._metrics = {
            "request_count": 0,
            "error_count": 0,
            "fatal_count": 0,
            "timeout_count": 0,
            "gpu_memory_error_count": 0,
            "decode_error_count": 0,
            "resize_count": 0,
            "latencies": [],
            "request_sizes": [],
            "confidence_scores": [],
            "gpu_queue_depths": [],
            "network_latencies": [],
            "status_codes": {},
            "predictions": {},
        }
        self._last_update = None
    
    def reset(self) -> None:
        """Reset all metrics to zero."""
        self._metrics = {
            "request_count": 0,
            "error_count": 0,
            "fatal_count": 0,
            "timeout_count": 0,
            "gpu_memory_error_count": 0,
            "decode_error_count": 0,
            "resize_count": 0,
            "latencies": [],
            "request_sizes": [],
            "confidence_scores": [],
            "gpu_queue_depths": [],
            "network_latencies": [],
            "status_codes": {},
            "predictions": {},
        }
        self._last_update = None
    
    def process_event(self, message: str) -> dict[str, Any]:
        """
        Process a single log event message and extract metrics.
        
        Args:
            message: Log message string
            
        Returns:
            Dictionary of extracted metrics from this event
        """
        extracted = {}
        
        # Request count
        if "request_id=" in message:
            self._metrics["request_count"] += 1
            extracted["request"] = True
        
        # Error tracking
        if "ERROR" in message:
            self._metrics["error_count"] += 1
            extracted["error"] = True
        
        if "FATAL" in message:
            self._metrics["fatal_count"] += 1
            extracted["fatal"] = True
        
        if "TimeoutError" in message:
            self._metrics["timeout_count"] += 1
            extracted["timeout"] = True
        
        if "CUDA out of memory" in message:
            self._metrics["gpu_memory_error_count"] += 1
            extracted["gpu_memory_error"] = True
        
        if "failed to decode image" in message:
            self._metrics["decode_error_count"] += 1
            extracted["decode_error"] = True
        
        if "image too large" in message:
            self._metrics["resize_count"] += 1
            extracted["resize"] = True
        
        # Request size
        size_match = self.patterns.request_size.search(message)
        if size_match:
            size = int(size_match.group(1))
            self._metrics["request_sizes"].append(size)
            extracted["request_size"] = size
        
        # Latency
        latency_match = self.patterns.latency.search(message)
        if latency_match:
            latency = int(latency_match.group(1) or latency_match.group(2))
            self._metrics["latencies"].append(latency)
            extracted["latency_ms"] = latency
        
        # Confidence
        confidence_match = self.patterns.confidence.search(message)
        if confidence_match:
            conf = float(confidence_match.group(1))
            self._metrics["confidence_scores"].append(conf)
            extracted["confidence"] = conf
        
        # GPU queue depth
        gpu_match = self.patterns.gpu_depth.search(message)
        if gpu_match:
            depth = int(gpu_match.group(1))
            self._metrics["gpu_queue_depths"].append(depth)
            extracted["gpu_queue_depth"] = depth
        
        # Network latency
        network_match = self.patterns.network.search(message)
        if network_match:
            net_latency = int(network_match.group(1))
            self._metrics["network_latencies"].append(net_latency)
            extracted["network_latency_ms"] = net_latency
        
        # Status code
        status_match = self.patterns.status.search(message)
        if status_match:
            code = status_match.group(1)
            self._metrics["status_codes"][code] = self._metrics["status_codes"].get(code, 0) + 1
            extracted["status_code"] = code
        
        # Prediction class
        prediction_match = self.patterns.prediction.search(message)
        if prediction_match:
            cls = prediction_match.group(1)
            self._metrics["predictions"][cls] = self._metrics["predictions"].get(cls, 0) + 1
            extracted["prediction"] = cls
        
        self._last_update = datetime.utcnow()
        return extracted
    
    def process_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Process multiple log events.
        
        Args:
            events: List of log event dictionaries with 'message' field
            
        Returns:
            Summary of all extracted metrics
        """
        for event in events:
            message = event.get("message", "")
            self.process_event(message)
        
        return self.get_summary()
    
    def get_summary(self) -> dict[str, Any]:
        """Get current metrics summary."""
        summary = {
            "counters": {
                "request_count": self._metrics["request_count"],
                "error_count": self._metrics["error_count"],
                "fatal_count": self._metrics["fatal_count"],
                "timeout_count": self._metrics["timeout_count"],
                "gpu_memory_error_count": self._metrics["gpu_memory_error_count"],
                "decode_error_count": self._metrics["decode_error_count"],
                "resize_count": self._metrics["resize_count"],
            },
            "status_codes": self._metrics["status_codes"],
            "predictions": self._metrics["predictions"],
            "last_update": self._last_update.isoformat() if self._last_update else None,
        }
        
        # Calculate histogram stats
        if self._metrics["latencies"]:
            latencies = self._metrics["latencies"]
            summary["latency_stats"] = {
                "count": len(latencies),
                "min": min(latencies),
                "max": max(latencies),
                "avg": sum(latencies) / len(latencies),
            }
        
        if self._metrics["request_sizes"]:
            sizes = self._metrics["request_sizes"]
            summary["request_size_stats"] = {
                "count": len(sizes),
                "min": min(sizes),
                "max": max(sizes),
                "avg": sum(sizes) / len(sizes),
            }
        
        if self._metrics["confidence_scores"]:
            scores = self._metrics["confidence_scores"]
            summary["confidence_stats"] = {
                "count": len(scores),
                "min": min(scores),
                "max": max(scores),
                "avg": sum(scores) / len(scores),
            }
        
        return summary


# =============================================================================
# MONITOR AGENT
# =============================================================================

class MonitorAgent:
    """
    OpenTelemetry-based monitoring agent.
    
    Collects metrics from log events and exports them to an OTLP endpoint.
    Falls back to in-memory collection if OpenTelemetry is not available.
    """
    
    def __init__(
        self,
        endpoint: str = OTLP_ENDPOINT,
        insecure: bool = OTLP_INSECURE,
        meter_name: str = METER_NAME,
    ):
        self.endpoint = endpoint
        self.insecure = insecure
        self.meter_name = meter_name
        self.collector = MetricsCollector()
        self._otel_initialized = False
        self._meter = None
        self._counters = {}
        self._histograms = {}
        
        if OTEL_AVAILABLE:
            self._init_otel()
    
    def _init_otel(self) -> None:
        """Initialize OpenTelemetry metrics."""
        try:
            exporter = OTLPMetricExporter(
                endpoint=self.endpoint,
                insecure=self.insecure
            )
            
            reader = PeriodicExportingMetricReader(exporter)
            provider = MeterProvider(metric_readers=[reader])
            metrics.set_meter_provider(provider)
            
            self._meter = metrics.get_meter(self.meter_name)
            
            # Create counters
            self._counters = {
                "request_count": self._meter.create_counter("request_count"),
                "error_count": self._meter.create_counter("error_count"),
                "fatal_count": self._meter.create_counter("fatal_count"),
                "timeout_count": self._meter.create_counter("timeout_count"),
                "gpu_memory_error_count": self._meter.create_counter("gpu_memory_error_count"),
                "decode_error_count": self._meter.create_counter("decode_error_count"),
                "resize_count": self._meter.create_counter("image_resize_count"),
                "status_code_count": self._meter.create_counter("status_code_count"),
                "prediction_count": self._meter.create_counter("prediction_count"),
            }
            
            # Create histograms
            self._histograms = {
                "request_size": self._meter.create_histogram("request_size_bytes"),
                "latency": self._meter.create_histogram("inference_latency_ms"),
                "confidence": self._meter.create_histogram("confidence_score"),
                "gpu_queue_depth": self._meter.create_histogram("gpu_queue_depth"),
                "network_latency": self._meter.create_histogram("network_latency_ms"),
            }
            
            self._otel_initialized = True
            logger.info(f"OpenTelemetry initialized with endpoint: {self.endpoint}")
            
        except Exception as e:
            logger.warning(f"Failed to initialize OpenTelemetry: {e}")
            self._otel_initialized = False
    
    def process_event(self, message: str) -> dict[str, Any]:
        """
        Process a single log event and record metrics.
        
        Args:
            message: Log message string
            
        Returns:
            Dictionary of extracted metrics
        """
        extracted = self.collector.process_event(message)
        
        # Export to OpenTelemetry if available
        if self._otel_initialized:
            self._export_to_otel(extracted)
        
        return extracted
    
    def process_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Process multiple log events.
        
        Args:
            events: List of log event dictionaries
            
        Returns:
            Summary of all metrics
        """
        for event in events:
            message = event.get("message", "")
            self.process_event(message)
        
        return self.collector.get_summary()
    
    def process_cloudwatch_log(self, log_data: dict[str, Any]) -> dict[str, Any]:
        """
        Process a CloudWatch log payload.
        
        Args:
            log_data: CloudWatch log JSON with 'logEvents' field
            
        Returns:
            Metrics summary
        """
        events = log_data.get("logEvents", [])
        return self.process_events(events)
    
    def _export_to_otel(self, extracted: dict[str, Any]) -> None:
        """Export extracted metrics to OpenTelemetry."""
        # Counters
        if extracted.get("request"):
            self._counters["request_count"].add(1)
        if extracted.get("error"):
            self._counters["error_count"].add(1)
        if extracted.get("fatal"):
            self._counters["fatal_count"].add(1)
        if extracted.get("timeout"):
            self._counters["timeout_count"].add(1)
        if extracted.get("gpu_memory_error"):
            self._counters["gpu_memory_error_count"].add(1)
        if extracted.get("decode_error"):
            self._counters["decode_error_count"].add(1)
        if extracted.get("resize"):
            self._counters["resize_count"].add(1)
        
        # Histograms
        if "request_size" in extracted:
            self._histograms["request_size"].record(extracted["request_size"])
        if "latency_ms" in extracted:
            self._histograms["latency"].record(extracted["latency_ms"])
        if "confidence" in extracted:
            self._histograms["confidence"].record(extracted["confidence"])
        if "gpu_queue_depth" in extracted:
            self._histograms["gpu_queue_depth"].record(extracted["gpu_queue_depth"])
        if "network_latency_ms" in extracted:
            self._histograms["network_latency"].record(extracted["network_latency_ms"])
        
        # Tagged counters
        if "status_code" in extracted:
            self._counters["status_code_count"].add(
                1, {"status_code": extracted["status_code"]}
            )
        if "prediction" in extracted:
            self._counters["prediction_count"].add(
                1, {"class": extracted["prediction"]}
            )
    
    def get_summary(self) -> dict[str, Any]:
        """Get current metrics summary."""
        summary = self.collector.get_summary()
        summary["otel_enabled"] = self._otel_initialized
        summary["endpoint"] = self.endpoint if self._otel_initialized else None
        return summary
    
    def reset(self) -> None:
        """Reset all metrics."""
        self.collector.reset()


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================

_monitor_agent: Optional[MonitorAgent] = None


def get_monitor_agent() -> MonitorAgent:
    """Get or create the global MonitorAgent instance."""
    global _monitor_agent
    if _monitor_agent is None:
        _monitor_agent = MonitorAgent()
    return _monitor_agent
