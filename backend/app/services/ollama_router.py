from ollama import AsyncClient
import re
#IP from tailscale tunnel, not the actual IP of the server, but it works for local access from the same network
OLLAMA_URL = "http://100.111.146.123:11434"

client = AsyncClient(host=OLLAMA_URL)

def clean_response(text: str) -> str:
    """Remove reasoning tags and artifacts from model output"""
    # Remove <think> tags and everything inside them
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    
    # Remove markdown code blocks if they contain think-like content
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    
    # Remove common reasoning artifacts
    patterns = [
        r"(Please wait|Alright|Let's get started|Could you clarify|specify what)",
        r"\[Please use any\]",
        r"😊",  # Remove emojis from reasoning output
    ]
    
    for pattern in patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    # Clean up extra whitespace
    text = re.sub(r'\n\s*\n', '\n', text)  # Remove multiple newlines
    text = text.strip()
    
    return text

async def local_response(prompt: str, system_prompt: str | None = None):
    """Communicates with Ollama using a specific model"""
    try:
        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': prompt})

        response = await client.chat(
            # model="deepseek-r1:32b",
            model="phi3:mini",
            messages=messages
        )
        # Auto-clean all responses to remove reasoning artifacts
        return clean_response(response['message']['content'])
    except Exception as e:
        print(f"Ollama Error: {e}")
        return "Error: Local model unreachable."