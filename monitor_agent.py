import json
import time
import re

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

# ==========================
# OpenTelemetry Setup
# ==========================

exporter = OTLPMetricExporter(
    endpoint="http://127.0.0.1:4317",
    insecure=True
)

reader = PeriodicExportingMetricReader(exporter)

provider = MeterProvider(metric_readers=[reader])
metrics.set_meter_provider(provider)

meter = metrics.get_meter("plant-disease-monitor")

# ==========================
# Define Metrics
# ==========================

request_count = meter.create_counter("request_count")
error_count = meter.create_counter("error_count")
fatal_count = meter.create_counter("fatal_count")
timeout_count = meter.create_counter("timeout_count")
gpu_memory_error_count = meter.create_counter("gpu_memory_error_count")
decode_error_count = meter.create_counter("decode_error_count")
resize_count = meter.create_counter("image_resize_count")

request_size = meter.create_histogram("request_size_bytes")
inference_latency = meter.create_histogram("inference_latency_ms")
confidence_score = meter.create_histogram("confidence_score")
gpu_queue_depth = meter.create_histogram("gpu_queue_depth")
network_latency = meter.create_histogram("network_latency_ms")

status_code_counter = meter.create_counter("status_code_count")
prediction_counter = meter.create_counter("prediction_count")

# ==========================
# Regex patterns
# ==========================

request_size_pattern = re.compile(r"bytes=(\d+)")
latency_pattern = re.compile(r"latency_ms=(\d+)")
confidence_pattern = re.compile(r"confidence=(\d+\.\d+)")
gpu_depth_pattern = re.compile(r"gpu_queue_depth=(\d+)")
network_pattern = re.compile(r"slow_network_ms=(\d+)")
status_pattern = re.compile(r"status=(\d+)")
prediction_pattern = re.compile(r"prediction=(\w+)")

# ==========================
# File path
# ==========================

METRICS_FILE = r"C:\Users\VICTUS\Desktop\metric.json"

# ==========================
# Monitor Function
# ==========================

def monitor():

    with open(METRICS_FILE, "r") as f:
        data = json.load(f)

    events = data.get("logEvents", [])

    for event in events:

        msg = event["message"]

        # Request count
        if "request_id=" in msg:
            request_count.add(1)

        # Errors
        if "ERROR" in msg:
            error_count.add(1)

        if "FATAL" in msg:
            fatal_count.add(1)

        if "TimeoutError" in msg:
            timeout_count.add(1)

        if "CUDA out of memory" in msg:
            gpu_memory_error_count.add(1)

        if "failed to decode image" in msg:
            decode_error_count.add(1)

        if "image too large" in msg:
            resize_count.add(1)

        # Request size
        size_match = request_size_pattern.search(msg)
        if size_match:
            request_size.record(int(size_match.group(1)))

        # Latency
        latency_match = latency_pattern.search(msg)
        if latency_match:
            inference_latency.record(int(latency_match.group(1)))

        # Confidence
        confidence_match = confidence_pattern.search(msg)
        if confidence_match:
            confidence_score.record(float(confidence_match.group(1)))

        # GPU queue depth
        gpu_match = gpu_depth_pattern.search(msg)
        if gpu_match:
            gpu_queue_depth.record(int(gpu_match.group(1)))

        # Network latency
        network_match = network_pattern.search(msg)
        if network_match:
            network_latency.record(int(network_match.group(1)))

        # Status code
        status_match = status_pattern.search(msg)
        if status_match:
            code = status_match.group(1)
            status_code_counter.add(1, {"status_code": code})

        # Prediction class
        prediction_match = prediction_pattern.search(msg)
        if prediction_match:
            cls = prediction_match.group(1)
            prediction_counter.add(1, {"class": cls})

# ==========================
# Main Loop
# ==========================

print("Starting Local Monitoring Agent...")

while True:

    try:
        monitor()
        print("Metrics collected from metrics.json")

    except Exception as e:
        print("Error:", e)

    time.sleep(30)
