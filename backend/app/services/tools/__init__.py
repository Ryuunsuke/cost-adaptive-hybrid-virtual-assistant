from .base_tool import BaseTool
from .calculator import CalculatorTool
from typing import Dict

# Tool registry - add imported tools here as they're created
AVAILABLE_TOOLS: Dict[str, BaseTool] = {
    "calculator": CalculatorTool(),
    # "search": SearchTool(),
    # "weather": WeatherTool(),
}

def get_tool(name: str) -> BaseTool | None:
    """Retrieve a tool by name"""
    return AVAILABLE_TOOLS.get(name.lower())

def list_tools() -> Dict[str, str]:
    """Return list of available tools with descriptions"""
    return {name: tool.description for name, tool in AVAILABLE_TOOLS.items()}
