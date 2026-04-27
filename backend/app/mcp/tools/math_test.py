
def add_numbers(a: int, b: int) -> int:
    """Adds two numbers together. Use this for simple math."""
    return a + b

def complex_scientific_calc(formula: str) -> str:
    """Performs complex scientific reasoning. Requires heavy lifting."""
    # This is a 'flag' tool that tells us we might need the Cloud LLM
    return f"Logic for {formula} is complex..."

# List of tools to pass to Ollama
ALL_TOOLS = [add_numbers, complex_scientific_calc]