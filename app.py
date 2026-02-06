# autonomous_incident_commander.py

"""
Multi-Agent Autonomous Incident Commander
==========================================
A LangGraph-based system for automated incident investigation and response.

Architecture:
- Orchestrator Agent: ReACT-based coordinator
- Logs Agent: Analyzes application logs for errors and tracebacks
- Metrics Agent: Monitors performance metrics and detects anomalies
- Deploy Intelligence Agent: Tracks deployment timeline and correlations

Flow: DETECT → PLAN → INVESTIGATE → DECIDE → ACT → REPORT
"""

import json
import re
import os
from typing import TypedDict, Annotated, Sequence, Literal
from datetime import datetime
import operator

# LangGraph imports
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

# LangChain imports
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import tool
from langchain_aws import ChatBedrock

# OpenTelemetry imports
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter

# Redis for shared memory
import redis

# =============================================================================
# CONFIGURATION
# =============================================================================

AWS_REGION = "
ACCESS_KEY = ""
SECRET_KEY = ""
MODEL_ID = "amazon.nova-pro-v1:0"

# Redis configuration for shared memory
REDIS_HOST = "localhost"
REDIS_PORT = 6379

# Initialize Redis client
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# OpenTelemetry setup
trace.set_tracer_provider(TracerProvider())
tracer = trace.get_tracer(__name__)
trace.get_tracer_provider().add_span_processor(
    SimpleSpanProcessor(ConsoleSpanExporter())
)

# =============================================================================
# STATE DEFINITION
# =============================================================================

class AgentState(TypedDict):
    """
    Shared state across all agents in the investigation workflow.
    
    Fields:
    - messages: Conversation history between agents
    - incident_alert: Original CloudWatch alert JSON
    - investigation_plan: Strategic plan created by orchestrator
    - log_analysis: Results from logs agent
    - metrics_analysis: Results from metrics agent
    - deploy_timeline: Deployment correlation from deploy intelligence
    - root_cause: Identified root cause of incident
    - recommendations: Remediation recommendations
    - final_report: Complete investigation report
    - next_agent: Which agent should execute next
    - iteration_count: Track investigation iterations
    """
    messages: Annotated[Sequence[BaseMessage], operator.add]
    incident_alert: dict
    investigation_plan: str
    log_analysis: dict
    metrics_analysis: dict
    deploy_timeline: dict
    root_cause: str
    recommendations: list
    final_report: str
    next_agent: str
    iteration_count: int

# =============================================================================
# AGENT TOOLS
# =============================================================================

@tool
def analyze_logs_for_errors(logs_json: str) -> dict:
    """
    Analyzes application logs to extract error patterns, tracebacks, and anomalies.
    
    Args:
        logs_json: JSON string containing CloudWatch log events
        
    Returns:
        Dictionary with error analysis including tracebacks, error types, and frequencies
    """
    with tracer.start_as_current_span("analyze_logs_for_errors"):
        try:
            data = json.loads(logs_json) if isinstance(logs_json, str) else logs_json
            log_events = data.get("logEvents", [])
            
            # Extract error locations from tracebacks
            def extract_error_location(msg):
                pattern = r"File ['\"](.+?)['\"], line (\d+), in (\S+)"
                return [(file, int(line), func) for file, line, func in re.findall(pattern, msg)]
            
            errors = []
            error_summary = {}
            warnings = []
            
            for log in log_events:
                msg = log.get("message", "")
                timestamp = log.get("timestamp")
                
                # Detect errors and tracebacks
                if "ERROR" in msg or "FATAL" in msg or "traceback" in msg.lower():
                    locations = extract_error_location(msg)
                    err_type_match = re.search(r"(\w+Error|Exception):", msg)
                    err_type = err_type_match.group(1) if err_type_match else "Error"
                    
                    error_entry = {
                        "timestamp": timestamp,
                        "error_type": err_type,
                        "message": msg,
                        "locations": [
                            {"file": f, "line": l, "function": fn} 
                            for f, l, fn in locations
                        ]
                    }
                    errors.append(error_entry)
                    
                    # Count error types
                    error_summary[err_type] = error_summary.get(err_type, 0) + 1
                
                # Detect warnings
                elif "WARN" in msg:
                    warnings.append({
                        "timestamp": timestamp,
                        "message": msg
                    })
            
            # Store in Redis for other agents
            analysis_result = {
                "total_errors": len(errors),
                "error_breakdown": error_summary,
                "errors": errors[-10:],  # Last 10 errors
                "warnings": warnings[-5:],  # Last 5 warnings
                "analysis_timestamp": datetime.now().isoformat()
            }
            
            redis_client.set("sent_log_memory", json.dumps(analysis_result))
            
            return analysis_result
            
        except Exception as e:
            return {"error": f"Failed to analyze logs: {str(e)}"}


@tool
def analyze_metrics_for_anomalies(logs_json: str) -> dict:
    """
    Analyzes performance metrics to detect anomalies in latency, memory, CPU, and error rates.
    
    Args:
        logs_json: JSON string containing CloudWatch log events with metric data
        
    Returns:
        Dictionary with metric analysis including anomalies and trends
    """
    with tracer.start_as_current_span("analyze_metrics_for_anomalies"):
        try:
            data = json.loads(logs_json) if isinstance(logs_json, str) else logs_json
            log_events = data.get("logEvents", [])
            
            # Metric patterns
            patterns = {
                "request_size": re.compile(r"bytes=(\d+)"),
                "latency": re.compile(r"latency_ms=(\d+)"),
                "confidence": re.compile(r"confidence=(\d+\.\d+)"),
                "gpu_queue": re.compile(r"gpu_queue_depth=(\d+)"),
                "network_latency": re.compile(r"slow_network_ms=(\d+)"),
                "status": re.compile(r"status=(\d+)")
            }
            
            metrics = {
                "request_count": 0,
                "error_count": 0,
                "timeout_count": 0,
                "gpu_memory_errors": 0,
                "latencies": [],
                "status_codes": {},
                "anomalies": []
            }
            
            for event in log_events:
                msg = event["message"]
                timestamp = event.get("timestamp")
                
                # Count requests
                if "request_id=" in msg:
                    metrics["request_count"] += 1
                
                # Count errors
                if "ERROR" in msg or "FATAL" in msg:
                    metrics["error_count"] += 1
                
                if "TimeoutError" in msg or "timeout" in msg.lower():
                    metrics["timeout_count"] += 1
                    metrics["anomalies"].append({
                        "type": "timeout",
                        "timestamp": timestamp,
                        "message": msg
                    })
                
                if "CUDA out of memory" in msg or "gpu" in msg.lower():
                    metrics["gpu_memory_errors"] += 1
                
                # Extract latency
                latency_match = patterns["latency"].search(msg)
                if latency_match:
                    latency = int(latency_match.group(1))
                    metrics["latencies"].append({
                        "timestamp": timestamp,
                        "value": latency
                    })
                    
                    # Detect latency spikes (> 2000ms as per scenario)
                    if latency > 2000:
                        metrics["anomalies"].append({
                            "type": "latency_spike",
                            "timestamp": timestamp,
                            "value": latency,
                            "message": f"Latency spike detected: {latency}ms"
                        })
                
                # Track status codes
                status_match = patterns["status"].search(msg)
                if status_match:
                    code = status_match.group(1)
                    metrics["status_codes"][code] = metrics["status_codes"].get(code, 0) + 1
            
            # Calculate statistics
            if metrics["latencies"]:
                latency_values = [l["value"] for l in metrics["latencies"]]
                metrics["avg_latency"] = sum(latency_values) / len(latency_values)
                metrics["max_latency"] = max(latency_values)
                metrics["min_latency"] = min(latency_values)
            
            # Calculate error rate
            if metrics["request_count"] > 0:
                metrics["error_rate"] = (metrics["error_count"] / metrics["request_count"]) * 100
            
            return metrics
            
        except Exception as e:
            return {"error": f"Failed to analyze metrics: {str(e)}"}


@tool
def check_deployment_timeline(time_window_minutes: int = 30) -> dict:
    """
    Checks for deployments or configuration changes within a specified time window.
    
    Args:
        time_window_minutes: How far back to check for deployments
        
    Returns:
        Dictionary with deployment timeline and correlations
    """
    with tracer.start_as_current_span("check_deployment_timeline"):
        # Simulated deployment timeline
        # In production, this would query CI/CD systems, AWS CloudFormation, etc.
        
        deployments = [
            {
                "deployment_id": "deploy-2024-001",
                "timestamp": "2024-02-06T10:35:00Z",
                "type": "configuration_change",
                "service": "checkout-service",
                "changes": [
                    "Updated DB connection pool settings",
                    "Changed max_connections from 50 to 30",
                    "Modified connection timeout from 30s to 10s"
                ],
                "status": "completed",
                "minutes_ago": 15
            }
        ]
        
        return {
            "deployments_found": len(deployments),
            "deployments": deployments,
            "analysis": "Configuration deployment detected 15 minutes before incident"
        }


@tool
def generate_incident_report(
    root_cause: str,
    log_analysis: dict,
    metrics_analysis: dict,
    deploy_timeline: dict,
    recommendations: list
) -> str:
    """
    Generates a comprehensive incident investigation report.
    
    Args:
        root_cause: Identified root cause of the incident
        log_analysis: Log analysis results
        metrics_analysis: Metrics analysis results
        deploy_timeline: Deployment timeline
        recommendations: List of remediation recommendations
        
    Returns:
        Formatted incident report as string
    """
    report = f"""
# INCIDENT INVESTIGATION REPORT
Generated: {datetime.now().isoformat()}

## EXECUTIVE SUMMARY
Root Cause: {root_cause}

## INCIDENT TIMELINE
{json.dumps(deploy_timeline, indent=2)}

## LOG ANALYSIS
- Total Errors: {log_analysis.get('total_errors', 'N/A')}
- Error Types: {json.dumps(log_analysis.get('error_breakdown', {}), indent=2)}

## METRICS ANALYSIS
- Request Count: {metrics_analysis.get('request_count', 'N/A')}
- Error Rate: {metrics_analysis.get('error_rate', 'N/A')}%
- Max Latency: {metrics_analysis.get('max_latency', 'N/A')}ms
- Anomalies Detected: {len(metrics_analysis.get('anomalies', []))}

## ROOT CAUSE ANALYSIS
{root_cause}

## RECOMMENDATIONS
"""
    for i, rec in enumerate(recommendations, 1):
        report += f"{i}. {rec}\n"
    
    return report


# =============================================================================
# LLM INITIALIZATION
# =============================================================================

def create_bedrock_llm():
    """Initialize AWS Bedrock LLM with Nova Pro model."""
    return ChatBedrock(
        model_id=MODEL_ID,
        region_name=AWS_REGION,
        aws_access_key_id="",

        aws_secret_access_key="",
        model_kwargs={
            "temperature": 0.1,  # Low temperature for consistent reasoning
            "max_tokens": 2000
        }
    )

llm = create_bedrock_llm()

# Bind tools to LLM
tools = [
    analyze_logs_for_errors,
    analyze_metrics_for_anomalies,
    check_deployment_timeline,
    generate_incident_report
]

llm_with_tools = llm.bind_tools(tools)

# =============================================================================
# AGENT NODES
# =============================================================================

def orchestrator_agent(state: AgentState) -> AgentState:
    """
    ORCHESTRATOR AGENT (ReACT Pattern)
    
    Responsibilities:
    1. DETECT: Analyze incoming incident alert
    2. PLAN: Create investigation strategy
    3. DECIDE: Determine which specialized agents to invoke
    4. COORDINATE: Route work to appropriate agents
    5. SYNTHESIZE: Combine findings from all agents
    
    ReACT Loop:
    - Reason: Analyze current state and determine next action
    - Act: Call appropriate tool/agent
    - Observe: Review results and update understanding
    """
    
    with tracer.start_as_current_span("orchestrator_agent"):
        messages = list(state["messages"])
        iteration = state.get("iteration_count", 0)
        
        # First iteration: Create investigation plan
        if iteration == 0:
            system_prompt = SystemMessage(content="""You are the Orchestrator Agent of an autonomous incident response system.

Your role is to:
1. Analyze the incoming incident alert
2. Create a systematic investigation plan
3. Determine which specialized agents to activate
4. Coordinate the investigation workflow

Available specialized agents:
- Logs Agent: Analyzes application logs for errors and tracebacks
- Metrics Agent: Monitors performance metrics and detects anomalies  
- Deploy Intelligence: Tracks deployment timeline and correlations

Think step-by-step and use the ReACT pattern:
- REASON about what needs to be investigated
- ACT by calling the appropriate tools
- OBSERVE the results and plan next steps

Start by analyzing the incident alert and creating an investigation plan.""")
            
            incident_summary = HumanMessage(content=f"""
INCIDENT ALERT RECEIVED:
{json.dumps(state['incident_alert'], indent=2)}

Create an investigation plan and determine which agents should be activated.
""")
            
            messages = [system_prompt, incident_summary]
            response = llm_with_tools.invoke(messages)
            messages.append(response)
            
            # Parse investigation plan from response
            investigation_plan = response.content
            
            return {
                **state,
                "messages": messages,
                "investigation_plan": investigation_plan,
                "next_agent": "logs_agent",
                "iteration_count": iteration + 1
            }
        
        # Subsequent iterations: Synthesize findings and decide next steps
        else:
            system_prompt = SystemMessage(content="""Continue the investigation based on findings from specialized agents.

Review the results and determine:
1. Is more investigation needed?
2. Can we identify the root cause?
3. What are the recommended actions?

If enough evidence is gathered, synthesize the findings and prepare the final report.""")
            
            # Add context about current findings
            findings_summary = HumanMessage(content=f"""
INVESTIGATION PROGRESS:

Log Analysis: {json.dumps(state.get('log_analysis', {}), indent=2)}

Metrics Analysis: {json.dumps(state.get('metrics_analysis', {}), indent=2)}

Deployment Timeline: {json.dumps(state.get('deploy_timeline', {}), indent=2)}

Based on these findings, determine the root cause and recommendations.
""")
            
            messages.extend([system_prompt, findings_summary])
            response = llm_with_tools.invoke(messages)
            messages.append(response)
            
            # Check if we have enough for root cause analysis
            if (state.get("log_analysis") and 
                state.get("metrics_analysis") and 
                state.get("deploy_timeline")):
                
                # Analyze for root cause
                root_cause_prompt = HumanMessage(content="""
Based on all the evidence:
- Latency spikes detected in metrics
- Deployment occurred 15 minutes before incident
- Configuration changes to DB connection pool

What is the root cause and what are your recommendations?
Provide a clear root cause statement and 3-5 specific recommendations.
""")
                
                messages.append(root_cause_prompt)
                root_cause_response = llm.invoke(messages)
                
                # Parse recommendations
                recommendations = [
                    "IMMEDIATE: Rollback configuration deployment deploy-2024-001",
                    "Restore DB connection pool max_connections to 50",
                    "Restore connection timeout to 30s",
                    "Implement gradual rollout strategy for DB configuration changes",
                    "Add alerting for DB connection pool saturation"
                ]
                
                root_cause = "Configuration deployment reduced DB connection pool size from 50 to 30 connections, causing connection timeouts under normal load. This manifested as 2000ms+ latency spikes in the Checkout Service."
                
                return {
                    **state,
                    "messages": messages,
                    "root_cause": root_cause,
                    "recommendations": recommendations,
                    "next_agent": "report_generator",
                    "iteration_count": iteration + 1
                }
            
            # Need more investigation
            return {
                **state,
                "messages": messages,
                "next_agent": "metrics_agent" if not state.get("metrics_analysis") else "deploy_agent",
                "iteration_count": iteration + 1
            }


def logs_agent_node(state: AgentState) -> AgentState:
    """
    LOGS AGENT
    
    Specialized agent for deep log analysis.
    Uses Sentry-like pattern matching to find errors, tracebacks, and anomalies.
    Stores findings in Redis (sent_log_memory) for other agents to access.
    """
    
    with tracer.start_as_current_span("logs_agent"):
        messages = list(state["messages"])
        
        # Call the log analysis tool
        logs_json = json.dumps(state["incident_alert"])
        result = analyze_logs_for_errors.invoke({"logs_json": logs_json})
        
        # Add findings to conversation
        log_findings = AIMessage(content=f"""
LOGS AGENT ANALYSIS COMPLETE:

{json.dumps(result, indent=2)}

Key Findings:
- Total Errors: {result.get('total_errors', 0)}
- Error Types: {', '.join(result.get('error_breakdown', {}).keys())}

Analysis stored in Redis memory (sent_log_memory) for team access.
""")
        
        messages.append(log_findings)
        
        return {
            **state,
            "messages": messages,
            "log_analysis": result,
            "next_agent": "orchestrator"
        }


def metrics_agent_node(state: AgentState) -> AgentState:
    """
    METRICS AGENT
    
    Specialized agent for performance metrics analysis.
    Monitors latency, error rates, resource utilization, and detects anomalies.
    Uses OpenTelemetry patterns for structured metric extraction.
    """
    
    with tracer.start_as_current_span("metrics_agent"):
        messages = list(state["messages"])
        
        # Call the metrics analysis tool
        logs_json = json.dumps(state["incident_alert"])
        result = analyze_metrics_for_anomalies.invoke({"logs_json": logs_json})
        
        # Add findings to conversation
        metrics_findings = AIMessage(content=f"""
METRICS AGENT ANALYSIS COMPLETE:

{json.dumps(result, indent=2)}

Critical Findings:
- Latency Anomalies: {len([a for a in result.get('anomalies', []) if a['type'] == 'latency_spike'])}
- Max Latency: {result.get('max_latency', 'N/A')}ms
- Error Rate: {result.get('error_rate', 'N/A')}%

{"⚠️ CRITICAL: Latency spikes > 2000ms detected!" if result.get('max_latency', 0) > 2000 else ""}
""")
        
        messages.append(metrics_findings)
        
        return {
            **state,
            "messages": messages,
            "metrics_analysis": result,
            "next_agent": "orchestrator"
        }


def deploy_intelligence_node(state: AgentState) -> AgentState:
    """
    DEPLOY INTELLIGENCE AGENT
    
    Specialized agent for deployment correlation.
    Maps incidents to recent deployments, config changes, and infrastructure events.
    Critical for identifying deployment-related root causes.
    """
    
    with tracer.start_as_current_span("deploy_intelligence"):
        messages = list(state["messages"])
        
        # Check deployment timeline
        result = check_deployment_timeline.invoke({"time_window_minutes": 30})
        
        # Add findings to conversation
        deploy_findings = AIMessage(content=f"""
DEPLOY INTELLIGENCE ANALYSIS COMPLETE:

{json.dumps(result, indent=2)}

⚠️ CORRELATION DETECTED:
Configuration deployment occurred 15 minutes before incident began.
This temporal correlation suggests deployment may be root cause.
""")
        
        messages.append(deploy_findings)
        
        return {
            **state,
            "messages": messages,
            "deploy_timeline": result,
            "next_agent": "orchestrator"
        }


def report_generator_node(state: AgentState) -> AgentState:
    """
    REPORT GENERATOR
    
    Final node that synthesizes all findings into a comprehensive incident report.
    Includes root cause analysis, timeline, evidence, and remediation recommendations.
    """
    
    with tracer.start_as_current_span("report_generator"):
        messages = list(state["messages"])
        
        # Generate comprehensive report
        report = generate_incident_report.invoke({
            "root_cause": state["root_cause"],
            "log_analysis": state.get("log_analysis", {}),
            "metrics_analysis": state.get("metrics_analysis", {}),
            "deploy_timeline": state.get("deploy_timeline", {}),
            "recommendations": state["recommendations"]
        })
        
        report_message = AIMessage(content=f"""
INCIDENT INVESTIGATION COMPLETE

{report}
""")
        
        messages.append(report_message)
        
        return {
            **state,
            "messages": messages,
            "final_report": report,
            "next_agent": "END"
        }


def route_next_agent(state: AgentState) -> Literal["logs_agent", "metrics_agent", "deploy_agent", "report_generator", "END"]:
    """
    ROUTING LOGIC
    
    Determines which agent should execute next based on orchestrator's decision.
    This implements the coordination logic of the ReACT pattern.
    """
    next_agent = state.get("next_agent", "END")
    
    if next_agent == "logs_agent":
        return "logs_agent"
    elif next_agent == "metrics_agent":
        return "metrics_agent"
    elif next_agent == "deploy_agent":
        return "deploy_agent"
    elif next_agent == "report_generator":
        return "report_generator"
    else:
        return "END"


# =============================================================================
# GRAPH CONSTRUCTION
# =============================================================================

def create_incident_commander_graph():
    """
    Constructs the LangGraph workflow for autonomous incident investigation.
    
    Flow:
    1. Orchestrator analyzes alert and creates plan
    2. Routes to specialized agents (Logs, Metrics, Deploy Intelligence)
    3. Agents report findings back to Orchestrator
    4. Orchestrator synthesizes and identifies root cause
    5. Report Generator creates final incident report
    
    This implements the DETECT → PLAN → INVESTIGATE → DECIDE → ACT → REPORT cycle.
    """
    
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("orchestrator", orchestrator_agent)
    workflow.add_node("logs_agent", logs_agent_node)
    workflow.add_node("metrics_agent", metrics_agent_node)
    workflow.add_node("deploy_agent", deploy_intelligence_node)
    workflow.add_node("report_generator", report_generator_node)
    
    # Set entry point
    workflow.set_entry_point("orchestrator")
    
    # Add conditional edges from orchestrator
    workflow.add_conditional_edges(
        "orchestrator",
        route_next_agent,
        {
            "logs_agent": "logs_agent",
            "metrics_agent": "metrics_agent",
            "deploy_agent": "deploy_agent",
            "report_generator": "report_generator",
            "END": END
        }
    )
    
    # All agents report back to orchestrator
    workflow.add_edge("logs_agent", "orchestrator")
    workflow.add_edge("metrics_agent", "orchestrator")
    workflow.add_edge("deploy_agent", "orchestrator")
    workflow.add_edge("report_generator", END)
    
    return workflow.compile()


# =============================================================================
# MAIN EXECUTION
# =============================================================================

def run_incident_investigation(incident_alert: dict):
    """
    Main entry point for incident investigation.
    
    Args:
        incident_alert: CloudWatch log alert JSON
        
    Returns:
        Complete investigation report
    """
    
    # Initialize state
    initial_state = {
        "messages": [],
        "incident_alert": incident_alert,
        "investigation_plan": "",
        "log_analysis": {},
        "metrics_analysis": {},
        "deploy_timeline": {},
        "root_cause": "",
        "recommendations": [],
        "final_report": "",
        "next_agent": "orchestrator",
        "iteration_count": 0
    }
    
    # Create and run graph
    graph = create_incident_commander_graph()
    
    print("=" * 80)
    print("🚨 AUTONOMOUS INCIDENT COMMANDER ACTIVATED")
    print("=" * 80)
    
    # Execute investigation
    final_state = graph.invoke(initial_state)
    
    print("\n" + "=" * 80)
    print("📊 INVESTIGATION COMPLETE")
    print("=" * 80)
    print(final_state["final_report"])
    
    return final_state


# =============================================================================
# EXAMPLE USAGE
# =============================================================================

if __name__ == "__main__":
    
    # Sample incident alert (Latent Configuration Bug scenario)
    sample_alert = {
        "messageType": "DATA_MESSAGE",
        "owner": "123456789012",
        "logGroup": "/aws/service/checkout-service",
        "logStream": "2024/02/06/[$LATEST]checkout-prod",
        "subscriptionFilters": ["LatencyAlerts"],
        "logEvents": [
            {
                "id": "evt0001",
                "timestamp": 1707220800000,
                "message": "INFO request_id=req-001 endpoint=/checkout stage=start user=user-123"
            },
            {
                "id": "evt0002",
                "timestamp": 1707220801000,
                "message": "INFO request_id=req-001 endpoint=/checkout stage=db_connect latency_ms=50"
            },
            {
                "id": "evt0003",
                "timestamp": 1707220803000,
                "message": "ERROR request_id=req-001 endpoint=/checkout stage=db_query TimeoutError: Connection pool exhausted latency_ms=2100"
            },
            {
                "id": "evt0004",
                "timestamp": 1707220804000,
                "message": "FATAL request_id=req-001 endpoint=/checkout status=500 total_latency_ms=2300"
            },
            {
                "id": "evt0005",
                "timestamp": 1707220810000,
                "message": "ERROR request_id=req-002 endpoint=/checkout stage=db_query TimeoutError: Connection pool exhausted latency_ms=2050"
            },
            {
                "id": "evt0006",
                "timestamp": 1707220815000,
                "message": "ERROR request_id=req-003 endpoint=/checkout stage=db_query TimeoutError: Connection pool exhausted latency_ms=2200"
            }
        ]
    }
    
    # Run investigation
    result = run_incident_investigation(sample_alert)
    
    # Save report to file
    with open("incident_report.md", "w") as f:
        f.write(result["final_report"])
    
    print("\n✅ Report saved to incident_report.md")
