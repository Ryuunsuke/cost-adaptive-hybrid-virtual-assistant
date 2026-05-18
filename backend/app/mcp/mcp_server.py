"""
mcp_server.py
-------------
FastMCP server for the cost-adaptive academic assistant (thesis §3.5).

This file is intentionally thin.  All tool logic lives in the tools/ package:

    tools/
    ├── __init__.py
    ├── quiz/
    │   ├── __init__.py
    │   ├── generate_quiz.py        (shadow reserve pool)
    │   └── submit_quiz_answers.py  (zero cost)
    ├── summarize_document.py       (visible / bonus pool)
    └── create_schedule.py          (visible / bonus pool)

Budget pool routing
-------------------
Pool checks are applied by tool_executor_node in task_router.py BEFORE
each tool is called.  The tool functions themselves contain no budget logic.

    generate_quiz       → check_and_deduct_shadow()
    submit_quiz_answers → no check  (zero token cost)
    summarize_document  → check_and_deduct_cloud()
    create_schedule     → check_and_deduct_cloud()

Running
-------
    python mcp_server.py

Or start via the FastAPI lifespan handler (see main.py).
"""

from fastmcp import FastMCP

from tools import (
    generate_quiz,
    submit_quiz_answers,
    summarize_document,
    create_schedule,
)

mcp = FastMCP("academic-assistant")

# Register each tool function with the MCP server.
# FastMCP uses the function name and docstring as the tool name and
# description that the LLM sees when deciding which tool to call.
mcp.tool()(generate_quiz)
mcp.tool()(submit_quiz_answers)
mcp.tool()(summarize_document)
mcp.tool()(create_schedule)

if __name__ == "__main__":
    mcp.run()