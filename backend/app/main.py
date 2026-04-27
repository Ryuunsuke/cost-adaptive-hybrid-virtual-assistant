from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from services.ollama_router import local_response
from services.task_router import app_instance as assistant_graph
# from mcp.server import mcp

app = FastAPI(title="Cost-Adaptive Hybrid Virtual Assistant")

# Enable CORS so React (port 5173) can talk to FastAPI (port 8000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://100.121.7.58:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    print("Received user input:", request.message)
    #init graph state
    initial_state = {
        "user_input": request.message,
        "classification": "",
        "tool_calls": [],
        "tool_results": {},
        "reasoning_steps": [],
        "response": ""
    }
    
    #Run the graph
    final_result = await assistant_graph.ainvoke(initial_state)
    
    #Return response
    return {"reply": final_result["response"]}

# mcp_app = mcp.http_app(path="/mcp")
# app.mount("/mcp", mcp_app)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)