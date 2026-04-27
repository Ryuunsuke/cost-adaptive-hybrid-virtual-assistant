# Tools System

The modular tools system allows the local Ollama model to select and execute tools based on user requests. Tools are automatically detected by the model and executed in parallel.

## Creating a New Tool

1. **Create a tool file** in `services/tools/` (e.g., `services/tools/weather.py`)

2. **Inherit from `BaseTool`**:
```python
from .base_tool import BaseTool

class WeatherTool(BaseTool):
    name = "weather"
    description = "Fetches current weather information for a given location"
    
    async def execute(self, user_input: str = "", **kwargs) -> str:
        """Extract location from user_input and fetch weather"""
        # Implementation here
        return "Weather info..."
```

3. **Register the tool** in `services/tools/__init__.py`:
```python
from .weather import WeatherTool

AVAILABLE_TOOLS: Dict[str, BaseTool] = {
    "calculator": CalculatorTool(),
    "weather": WeatherTool(),  # Add here
}
```

## How It Works

### Workflow:
1. **Triage** - Classifies request as SIMPLE or COMPLEX
2. **Tool Executor** (SIMPLE path only):
   - Lists all available tools
   - Prompts Ollama to decide which tools to use
   - Executes selected tools in parallel
3. **Synthesizer** - Combines tool results with final response
4. **Cloud Engine** (COMPLEX path) - Direct cloud LLM call

### Tool Design Guidelines:
- ✅ Keep tools focused and single-purpose
- ✅ Handle errors gracefully (return error string instead of raising)
- ✅ Tools should be async-compatible
- ✅ Return results as strings or JSON-serializable types
- ✅ Tool descriptions must be clear for LLM selection

## Example Tools to Implement:

- **SearchTool**: Web/vector database search
- **WeatherTool**: Weather API integration
- **CalculatorTool**: Math operations (already included)
- **DatabaseTool**: Query application databases
- **APITool**: Call external APIs
- **FileToolListen**: Read/process files

## Testing Tools

Tools work best when requests clearly indicate what they need:
- "What's 15 + 25?" → Uses calculator
- "Weather in Seattle" → Uses weather tool
- "Search for Python best practices" → Uses search tool
