from typing import Any
# pyrefly: ignore [missing-import]
from pydantic import BaseModel
# pyrefly: ignore [missing-import]
from google.adk.agents import Agent
# pyrefly: ignore [missing-import]
from google.adk.apps import App
# pyrefly: ignore [missing-import]
from google.adk.models import Gemini
# pyrefly: ignore [missing-import]
from google.adk.tools.agent_tool import AgentTool
# pyrefly: ignore [missing-import]
from google.adk.workflow import Workflow, START, DEFAULT_ROUTE, node, Edge
# pyrefly: ignore [missing-import]
from google.adk.events.event import Event
# pyrefly: ignore [missing-import]
from google.adk.events.request_input import RequestInput
# pyrefly: ignore [missing-import]
from google.adk.agents.context import Context
# pyrefly: ignore [missing-import]
from google.adk.workflow._base_node import BaseNode
from app.config import config

# Monkeypatch BaseNode to add route property/attribute to avoid AttributeError in root contexts
BaseNode.route = None


# Define session state schema
class CommuteState(BaseModel):
    user_query: str = ""
    proposed_route: str = ""
    toll_cost: float = 0.0
    needs_approval: bool = False
    approved: bool = False
    final_response: str = ""

@node
def security_checkpoint(ctx: Context, node_input: str) -> str:
    """Security Checkpoint: Checks user query for security violations and PII (Phase 4)."""
    ctx.state["user_query"] = node_input
    ctx.route = DEFAULT_ROUTE
    return node_input

@node
def security_violation_handler(ctx: Context, node_input: Any) -> str:
    """Handles flagged query violations."""
    ctx.state["final_response"] = "Security Checkpoint Flagged: The query was blocked due to a security violation."
    return "blocked"

# Specialized LlmAgents (Sub-agents)
traffic_analyzer = Agent(
    name="traffic_analyzer",
    model=Gemini(model=config.model),
    instruction=(
        "You are a Traffic Specialist. Analyze traffic conditions, estimate travel times, "
        "and suggest optimal driving routes. Note if any routes include tolls."
    ),
)

transit_advisor = Agent(
    name="transit_advisor",
    model=Gemini(model=config.model),
    instruction=(
        "You are a Transit Specialist. Analyze public transportation schedules, delays, routes, "
        "and provide alternative public transit advice."
    ),
)

# Parent Orchestrator Agent
orchestrator = Agent(
    name="orchestrator",
    model=Gemini(model=config.model),
    instruction=(
        "You are the Commute Wizard Orchestrator. Coordinate with the traffic_analyzer and "
        "transit_advisor sub-agents to provide a comprehensive commute recommendation.\n"
        "Provide a summary of both driving and public transit routes.\n"
        "IMPORTANT: If the suggested driving route has tolls or the estimated delays are greater than 30 minutes, "
        "you MUST end your final response with the keyword 'NEEDS_APPROVAL'. Otherwise, end with 'AUTO_APPROVED'."
    ),
    tools=[AgentTool(agent=traffic_analyzer), AgentTool(agent=transit_advisor)],
)

@node
def route_router(ctx: Context, node_input: Any) -> str:
    """Routes the orchestrator output based on approval needs."""
    output_text = str(node_input)
    if "NEEDS_APPROVAL" in output_text:
        ctx.state["needs_approval"] = True
        ctx.route = "needs_approval"
        ctx.state["proposed_route"] = output_text
        return "needs_approval"
    else:
        ctx.state["needs_approval"] = False
        ctx.route = "auto_approved"
        ctx.state["final_response"] = output_text
        return "auto_approved"

@node(rerun_on_resume=True)
async def route_verification(ctx: Context, node_input: Any):
    """Human-in-the-Loop approval for toll roads or high traffic delays."""
    user_approval = ctx.resume_inputs.get("user_commute_approval")
    if user_approval is None:
        yield RequestInput(
            interrupt_id="user_commute_approval",
            message="The proposed driving route has tolls or significant transit delays. Do you approve this route? (type 'yes' or 'no')",
            response_schema=str
        )
        return
    
    if str(user_approval).lower() in ["yes", "y", "approve", "approved"]:
        ctx.state["approved"] = True
        ctx.state["final_response"] = f"Route Approved!\n{ctx.state.get('proposed_route', '')}"
    else:
        ctx.state["approved"] = False
        ctx.state["final_response"] = "Route Rejected by user. Commute canceled."

@node
def final_output(ctx: Context, node_input: Any) -> str:
    """Terminal node displaying the final commute result."""
    return ctx.state.get("final_response", "No response generated.")

# Define the ADK 2.0 Workflow graph
workflow = Workflow(
    name="commute_workflow",
    state_schema=CommuteState,
    edges=[
        Edge(from_node=START, to_node=security_checkpoint),
        Edge(from_node=security_checkpoint, to_node=security_violation_handler, route="SECURITY_EVENT"),
        Edge(from_node=security_checkpoint, to_node=orchestrator, route=DEFAULT_ROUTE),
        Edge(from_node=orchestrator, to_node=route_router),
        Edge(from_node=route_router, to_node=route_verification, route="needs_approval"),
        Edge(from_node=route_router, to_node=final_output, route="auto_approved"),
        Edge(from_node=route_verification, to_node=final_output),
        Edge(from_node=security_violation_handler, to_node=final_output),
    ]
)

# Instantiate the App with the workflow as the root agent
app = App(
    root_agent=workflow,
    name="app",
)

# Export root_agent alias for integration test compatibility
root_agent = workflow

