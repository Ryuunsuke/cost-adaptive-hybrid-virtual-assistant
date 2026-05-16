from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from services.task_router import app_instance as assistant_graph
from services.db_con import (
    init_db_pool, close_db_pool, bootstrap_schema,
    upsert_user, get_user_sessions, create_session, delete_session,
    create_message, get_session_history,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db_pool()
    await bootstrap_schema()
    yield
    await close_db_pool()


app = FastAPI(title="Cost-Adaptive Hybrid Virtual Assistant", lifespan=lifespan)

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


class LoginRequest(BaseModel):
    username: str

class NewSessionRequest(BaseModel):
    user_id: int

class ChatRequest(BaseModel):
    session_id: int
    message: str


@app.post("/api/login")
async def login_endpoint(request: LoginRequest):
    user = await upsert_user(request.username)
    sessions = await get_user_sessions(user["id_user"])
    return {
        "user_id": user["id_user"],
        "username": user["username"],
        "sessions": [
            {
                "session_id": s["id_session"],
                "started_at": s["started_at"].isoformat(),
            }
            for s in sessions
        ],
    }


@app.post("/api/session/new")
async def new_session_endpoint(request: NewSessionRequest):
    session = await create_session(request.user_id)
    return {
        "session_id": session["id_session"],
        "started_at": session["started_at"].isoformat(),
    }


@app.delete("/api/session/{session_id}")
async def delete_session_endpoint(session_id: int):
    await delete_session(session_id)
    return {"ok": True}


@app.get("/api/history")
async def history_endpoint(session_id: int):
    messages = await get_session_history(session_id, limit=500)
    return {"messages": messages}


@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    print(f"[session={request.session_id}] user: {request.message}")

    await create_message(request.session_id, "user", request.message)
    history = await get_session_history(request.session_id, limit=10)

    initial_state = {
        "message":          request.message,
        "user_input":       request.message,
        "session_id":       request.session_id,
        "session_history":  history,
        "category":         "",
        "confidence":       0.0,
        "requires_tool":    False,
        "routing_decision": "",
        "response":         "",
        "budget_pool_used": None,
        "tool_calls":       [],
        "tool_results":     {},
        "reasoning_steps":  [],
    }

    final_result = await assistant_graph.ainvoke(initial_state)
    reply = final_result["response"]
    await create_message(request.session_id, "assistant", reply)

    return {
        "reply":            reply,
        "routing_decision": final_result.get("routing_decision"),
        "budget_pool_used": final_result.get("budget_pool_used"),
    }


if __name__ == "__main__":
    # uvicorn.run(app, host="100.121.7.58", port=8000)
    uvicorn.run(app, host="0.0.0.0", port=8000)
