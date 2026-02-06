import json
import re
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter

# OpenTelemetry setup
trace.set_tracer_provider(TracerProvider())
tracer = trace.get_tracer(__name__)
trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

def extract_error_location(traceback_msg):
    """
    Extracts the file, line number, and function from a traceback string.
    Returns a list of tuples (file, line, function)
    """
    pattern = r"File ['\"](.+?)['\"], line (\d+), in (\S+)"
    matches = re.findall(pattern, traceback_msg)
    return [(file, int(line), func) for file, line, func in matches]

def clean_tracebacks(logs_json):
    """
    Returns a clean list of tracebacks with only essential info:
    timestamp, error type, file, line, function
    """
    clean_results = []
    
    with tracer.start_as_current_span("process_logs"):
        for log in logs_json:
            message = log.get("message", "")
            if "traceback" in message.lower() or "error" in message.lower():
                locations = extract_error_location(message)
                # Extract error type from last line
                error_type_match = re.search(r"(\w+Error|Exception):", message)
                error_type = error_type_match.group(1) if error_type_match else "Error"
                
                for file, line, func in locations:
                    clean_results.append({
                        "timestamp": log.get("timestamp"),
                        "error_type": error_type,
                        "file": file,
                        "line": line,
                        "function": func
                    })
    return clean_results

if __name__ == "__main__":
    # Example logs
    sample_logs = [
        {"timestamp": "2026-02-06T10:00:00", "level": "INFO", "message": "Starting process"},
        {"timestamp": "2026-02-06T10:01:00", "level": "ERROR",
         "message": "Traceback (most recent call last):\n  File 'app.py', line 10, in <module>\n    x = 1/0\nZeroDivisionError: division by zero"},
        {"timestamp": "2026-02-06T10:02:00", "level": "INFO", "message": "Process completed successfully"},
        {"timestamp": "2026-02-06T10:03:00", "level": "ERROR",
         "message": "Traceback (most recent call last):\n  File 'server.py', line 25, in start_server\n    open('file.txt')\nFileNotFoundError: [Errno 2] No such file or directory"}
    ]

    clean_logs = clean_tracebacks(sample_logs)
    print("\n=== Clean Traceback Logs ===")
    print(json.dumps(clean_logs, indent=4))




















# import json
# import re
# from opentelemetry import trace
# from opentelemetry.sdk.trace import TracerProvider
# from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter

# # Set up OpenTelemetry
# trace.set_tracer_provider(TracerProvider())
# tracer = trace.get_tracer(__name__)
# trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
# def extract_error_location(traceback_msg):
#     """
#     Extracts the file and line number from a traceback string.
#     Returns a list of (file, line_number, function) tuples.
#     """
#     pattern = r"File ['\"](.+?)['\"], line (\d+), in (\S+)"
#     matches = re.findall(pattern, traceback_msg)
#     return [(file, int(line), func) for file, line, func in matches]

# def find_tracebacks(logs_json):
#     """
#     Finds logs with traceback errors and extracts exact location.
#     """
#     results = []
    
#     with tracer.start_as_current_span("process_logs"):
#         for log_entry in logs_json:
#             message = log_entry.get("message", "")
#             if "traceback" in message.lower() or "error" in message.lower():
#                 locations = extract_error_location(message)
#                 results.append({
#                     "timestamp": log_entry.get("timestamp"),
#                     "level": log_entry.get("level"),
#                     "message": message,
#                     "locations": locations
#                 })
#     return results

# if __name__ == "__main__":
#     # Example logs
#     sample_logs = [
#         {"timestamp": "2026-02-06T10:00:00", "level": "INFO", "message": "Starting process"},
#         {"timestamp": "2026-02-06T10:01:00", "level": "ERROR",
#          "message": "Traceback (most recent call last):\n  File 'app.py', line 10, in <module>\n    x = 1/0\nZeroDivisionError: division by zero"},
#         {"timestamp": "2026-02-06T10:02:00", "level": "INFO", "message": "Process completed successfully"},
#         {"timestamp": "2026-02-06T10:03:00", "level": "ERROR",
#          "message": "Traceback (most recent call last):\n  File 'server.py', line 25, in start_server\n    open('file.txt')\nFileNotFoundError: [Errno 2] No such file or directory"}
#     ]

#     tracebacks = find_tracebacks(sample_logs)
#     print("\n=== Traceback/Error Logs with Locations ===")
#     print(json.dumps(tracebacks, indent=4))
