from fastmcp import FastMCP

from tools import (
    generate_quiz,
    submit_quiz_answers,
    summarize_document,
    create_schedule,
    generate_flashcards,
)

mcp = FastMCP("academic-assistant")

mcp.tool()(generate_quiz)
mcp.tool()(submit_quiz_answers)
mcp.tool()(summarize_document)
mcp.tool()(create_schedule)
mcp.tool()(generate_flashcards)

if __name__ == "__main__":
    mcp.run()