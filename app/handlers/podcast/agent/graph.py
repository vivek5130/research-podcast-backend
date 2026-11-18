"""Research -> script agent, wired with LangGraph.

    plan_queries -> search -> curate -> write_script -> check_length --done--> END
                                             ^                |
                                             +---- revise ----+   (max 1 rewrite, keeps the best draft)

The graph's shape is fixed (predictable cost and latency); the only decision the
agent makes is whether the script needs one rewrite to hit the requested length.
"""
from datetime import datetime, timezone

from langgraph.graph import END, StateGraph

from app.handlers.podcast.agent import nodes
from app.handlers.podcast.agent.state import ResearchState

_compiled_graph = None


def build_graph():
    """Wire the nodes and edges and compile the graph."""
    graph = StateGraph(ResearchState)
    graph.add_node("plan_queries", nodes.plan_queries)
    graph.add_node("search", nodes.search)
    graph.add_node("curate", nodes.curate)
    graph.add_node("write_script", nodes.write_script)
    graph.add_node("check_length", nodes.check_length)

    graph.set_entry_point("plan_queries")
    graph.add_edge("plan_queries", "search")
    graph.add_edge("search", "curate")
    graph.add_edge("curate", "write_script")
    graph.add_edge("write_script", "check_length")
    graph.add_conditional_edges("check_length", nodes.route_after_check, {"revise": "write_script", "done": END})
    return graph.compile()


async def run_research_agent(topic: str, duration_seconds: int = 300, language: str = "en-IN") -> ResearchState:
    """Research ``topic`` from current sources and return the final state (title, script, sources...)."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    today = datetime.now(timezone.utc).strftime("%A, %B %d, %Y")
    return await _compiled_graph.ainvoke(
        {"topic": topic, "duration_seconds": duration_seconds, "language": language, "today": today, "revisions": 0}
    )
