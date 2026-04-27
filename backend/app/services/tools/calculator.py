from .base_tool import BaseTool
import re

class CalculatorTool(BaseTool):
    """Simple calculator tool for arithmetic operations"""
    
    name = "calculator"
    description = "Performs basic arithmetic calculations (add, subtract, multiply, divide)"
    
    async def execute(self, user_input: str = "", **kwargs) -> str:
        """
        Execute calculator operations found in user input
        Looks for patterns like "5 + 3" or "20 * 4"
        """
        # Simple regex to find math expressions
        expressions = re.findall(r'(\d+\s*[\+\-\*/]\s*\d+)', user_input)
        
        if not expressions:
            return "No math expressions found in input"
        
        results = []
        for expr in expressions:
            try:
                result = eval(expr)  # In production, use safer eval
                results.append(f"{expr} = {result}")
            except Exception as e:
                results.append(f"{expr} error: {str(e)}")
        
        return "; ".join(results)
