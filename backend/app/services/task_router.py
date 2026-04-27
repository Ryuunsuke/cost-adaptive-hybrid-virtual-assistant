from typing import TypedDict, Literal
from langgraph.graph import StateGraph, END # type: ignore
from services.ollama_router import local_response
from services.tools import get_tool, list_tools
# from app.services.cloud_client import call_cloud_llm

import json
import re


def extract_keyword_response(text: str, valid_keywords: list[str]) -> str:
    if not text:
        return ""

    cleaned = text.strip().lower()
    for keyword in valid_keywords:
        if re.search(rf'\b{re.escape(keyword)}\b', cleaned):
            return keyword

    tokens = re.findall(r'\w+', cleaned)
    return tokens[0] if tokens else ""


#Define the State (What the graph "remembers")
class AgentState(TypedDict):
    user_input: str
    classification: str
    tool_calls: list  # Track which tools were invoked
    tool_results: dict  # Store tool outputs
    reasoning_steps: list  # Track reasoning for transparency
    response: str

#Node definitions (The workhorses)
async def triage_node(state: AgentState):
    """Local Ollama decides the path"""
    print("[LANGGRAPH]: Triaging...")

    prompt = f"""
    Classify this as SIMPLE or COMPLEX user input.
    Answer exactly with one word: SIMPLE or COMPLEX.
    Do not provide any explanation, punctuation, or extra text.
    User input: "{state['user_input']}"
    """
    system_prompt = "You are a strict assistant. Reply with exactly one word: SIMPLE or COMPLEX, nothing else."

    decision = await local_response(prompt, system_prompt=system_prompt)
    decision = extract_keyword_response(decision, ["simple", "complex"])
    print("decision:", decision)
    return {"classification": "complex", "reasoning_steps": ["triaged"]} if decision == "complex" else {"classification": "simple", "reasoning_steps": ["triaged"]}

async def tool_executor_node(state: AgentState):
    """Determines which tools to use and executes them"""
    print("[LANGGRAPH]: Tool Executor - Analyzing available tools...")
    
    # Get list of available tools
    available_tools = list_tools()
    tools_description = "\n".join([f"- {name}: {desc}" for name, desc in available_tools.items()])
    tool_calls = []
    tool_results = {}
    
    # Prompt whether tools are needed
    tool_prompt = f"""
    Determine whether this user input requires a tool to answer.
    Answer exactly YES or NO.
    Do not provide any explanation, punctuation, or extra text.
    User input: "{state['user_input']}"
    """
    system_prompt = "You are a strict assistant. Reply with exactly one word: YES or NO, nothing else."
    tool_decision_text = await local_response(tool_prompt, system_prompt=system_prompt)
    tool_decision = extract_keyword_response(tool_decision_text, ["yes", "no"])
    print(f"[LANGGRAPH]: Tool decision response: {tool_decision_text}")
    if tool_decision == "no":
        return {
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "reasoning_steps": ["tools_not_needed"]
        }

    tool_prompt += f"\n\nAvailable tools:\n{tools_description}\n\nWhich tools should be used? Respond with a JSON array of tool names (e.g. [\"web_search\", \"calculator\"]) or an empty array if none:"
    tool_decision_text = await local_response(tool_prompt)
    
    try:
        # Extract JSON array from response
        tool_calls = json.loads(tool_decision_text)
    except json.JSONDecodeError:
        # Fallback: try to extract tool names with regex
        if matches := re.search(r'\[.*\]', tool_decision_text, re.DOTALL):
            try:
                tool_calls = json.loads(matches[0])
            except json.JSONDecodeError:
                tool_calls = []
        else:
            tool_calls = []
    
    print(f"[LANGGRAPH]: Tools selected: {tool_calls}")
    
    # Execute selected tools
    for tool_name in tool_calls:
        if tool := get_tool(tool_name):
            try:
                result = await tool.execute(user_input=state['user_input'])
                tool_results[tool_name] = result
                print(f"[LANGGRAPH]: Tool '{tool_name}' executed successfully")
            except Exception as e:
                tool_results[tool_name] = f"Error: {str(e)}"
                print(f"[LANGGRAPH]: Tool '{tool_name}' failed: {e}")
        else:
            print(f"[LANGGRAPH]: Tool '{tool_name}' not found")
    
    return {
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "reasoning_steps": [f"tools_considered: {tool_calls}"]
    }

async def synthesizer_node(state: AgentState):
    """Synthesize final response using Ollama with tool results"""
    print("[LANGGRAPH]: Synthesizer - Generating response...")
    
    # Build context with tool results
    tool_context = ""
    if state['tool_results']:
        tool_context = "\n\nTool Results:\n"
        for tool_name, result in state['tool_results'].items():
            tool_context += f"- {tool_name}: {result}\n"
    
    synthesis_prompt = f"""You are a helpful and friendly AI assistant.
    Answer the user's request directly and naturally.
    Do not provide examples, hypothetical response templates, or any meta commentary.
    If tools were used, incorporate only the tool results provided below.
    If no tools were used, answer based solely on the user input.
    User Request: {state['user_input']}{tool_context}
    """
    system_prompt = "You are a strict assistant. Reply with a single direct response only. Do not include examples, hypothetical wording, or descriptions of how the answer was generated."

    res = await local_response(synthesis_prompt, system_prompt=system_prompt) or "I've processed your request. How can I help further?"
    
    reasoning_steps = state.get('reasoning_steps', [])
    if isinstance(reasoning_steps, str):
        reasoning_steps = [reasoning_steps]

    return {
        "response": res,
        "reasoning_steps": reasoning_steps + ["response_synthesized"]
    }

async def cloud_node(state: AgentState):
    """Execute via Cloud LLM"""
    print("[LANGGRAPH]: Executing Cloud Path")
    # res = await call_cloud_llm(state['user_input'])
    return {
        "response": "Processed via Cloud reasoning.",
        "reasoning_steps": ["cloud_executed"]
    }

#Routing logic
def route_decision(state: AgentState) -> Literal["local", "cloud"]:
    return "local" if state["classification"] == "simple" else "cloud"

#Graph building
workflow = StateGraph(AgentState)

workflow.add_node("triage", triage_node)
workflow.add_node("tool_executor", tool_executor_node)
workflow.add_node("synthesizer", synthesizer_node)
workflow.add_node("cloud_engine", cloud_node)

workflow.set_entry_point("triage")

#Logic branch
workflow.add_conditional_edges(
    "triage",
    route_decision,
    {
        "local": "tool_executor",
        "cloud": "cloud_engine"
    }
)

workflow.add_edge("tool_executor", "synthesizer")
workflow.add_edge("synthesizer", END)
workflow.add_edge("cloud_engine", END)

#Compile the graph
app_instance = workflow.compile()

async def routing_logic(text: str):
    inputs = {"user_input": text}
    final_state = await app_instance.ainvoke(inputs)
    return final_state["response"]