from fastapi import APIRouter
from pydantic import BaseModel
from app.services.router import routing_logic

router = APIRouter()

class ChatRequest(BaseModel):
    message: str

@router.post("/chat")
async def chat_endpoint(request: ChatRequest):
    response_text = await hybrid_routing_logic(request.message)
    return {"reply": response_text}