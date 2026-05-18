import asyncio
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

import io

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pypdf import PdfReader
import uvicorn

from services.task_router import app_instance as assistant_graph
from services.tools import submit_quiz_answers
from services.db_con import (
    init_db_pool, close_db_pool, bootstrap_schema,
    upsert_user, get_user_sessions, create_session, delete_session,
    create_message, get_session_history,
    get_session, get_session_cost_summary,
    save_file, get_session_file, get_session_files,
    create_calendar_event, get_calendar_events,
    update_calendar_event, delete_calendar_event,
    get_schedule_entries, create_schedule_entry,
    update_schedule_entry, delete_schedule_entry,
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
    force_tool: str = ""   # tool name to invoke directly, e.g. "summarize_document"

class QuizSubmitRequest(BaseModel):
    session_id: int
    tool_output_id: int
    answers: dict  # {"0": "A", "1": "C", …}

class CalendarEventCreate(BaseModel):
    user_id: int
    title: str
    description: str = ""
    start_date: str   # ISO-8601 string
    end_date: str = None

class CalendarEventUpdate(BaseModel):
    user_id: int
    title: str
    description: str = ""
    start_date: str
    end_date: str = None

class ScheduleEntryCreate(BaseModel):
    session_id: int
    date: str          # YYYY-MM-DD
    topics: list[str]
    duration_hours: float = 2.0
    note: str = ""

class ScheduleEntryUpdate(BaseModel):
    session_id: int
    date: str
    topics: list[str]
    duration_hours: float = 2.0
    note: str = ""


@app.post("/api/login")
async def login_endpoint(request: LoginRequest):
    user = await upsert_user(request.username)
    sessions = await get_user_sessions(user["id_user"])
    return {
        "user_id": user["id_user"],
        "username": user["username"],
        "sessions": [
            {
                "session_id":          s["id_session"],
                "started_at":          s["started_at"].isoformat(),
                "daily_visible_limit": float(s["daily_visible_limit"]),
                "visible_used":        float(s["visible_used"]),
                "quiz_bonus":          float(s["quiz_bonus"]),
            }
            for s in sessions
        ],
    }


@app.get("/api/sessions")
async def sessions_list_endpoint(user_id: int):
    sessions = await get_user_sessions(user_id)
    return {
        "sessions": [
            {
                "session_id":          s["id_session"],
                "started_at":          s["started_at"].isoformat(),
                "daily_visible_limit": float(s["daily_visible_limit"]),
                "visible_used":        float(s["visible_used"]),
                "quiz_bonus":          float(s["quiz_bonus"]),
            }
            for s in sessions
        ]
    }


@app.post("/api/session/new")
async def new_session_endpoint(request: NewSessionRequest):
    session = await create_session(request.user_id)
    return {
        "session_id":          session["id_session"],
        "started_at":          session["started_at"].isoformat(),
        "daily_visible_limit": float(session["daily_visible_limit"]),
        "visible_used":        float(session["visible_used"]),
        "quiz_bonus":          float(session["quiz_bonus"]),
    }


@app.post("/api/upload")
async def upload_endpoint(
    session_id: int = Form(...),
    file: UploadFile = File(...),
):
    if not file.filename.lower().endswith(".pdf"):
        return {"ok": False, "error": "Only PDF files are supported."}

    content = await file.read()
    reader = PdfReader(io.BytesIO(content))
    extracted = "\n".join(
        page.extract_text() or "" for page in reader.pages
    ).strip()

    row = await save_file(session_id, file.filename, extracted or None)
    return {"ok": True, "filename": file.filename, "char_count": len(extracted), "id_file": row["id_file"]}


@app.get("/api/file")
async def file_endpoint(session_id: int):
    row = await get_session_file(session_id)
    if not row:
        return {"file": None}
    return {
        "file": {
            "id_file":    row["id_file"],
            "filename":   row["filename"],
            "char_count": len(row.get("extracted_text") or ""),
        }
    }


@app.get("/api/files")
async def files_endpoint(session_id: int):
    rows = await get_session_files(session_id)
    return {
        "files": [
            {
                "id_file":    r["id_file"],
                "filename":   r["filename"],
                "char_count": len(r.get("extracted_text") or ""),
                "uploaded_at": r["uploaded_at"].isoformat(),
            }
            for r in rows
        ]
    }


@app.get("/api/calendar")
async def calendar_get_endpoint(user_id: int):
    events = await get_calendar_events(user_id)
    return {
        "events": [
            {
                "id_event":    e["id_event"],
                "title":       e["title"],
                "description": e["description"] or "",
                "start_date":  e["start_date"].isoformat(),
                "end_date":    e["end_date"].isoformat() if e["end_date"] else None,
            }
            for e in events
        ]
    }


@app.post("/api/calendar")
async def calendar_create_endpoint(request: CalendarEventCreate):
    event = await create_calendar_event(
        request.user_id,
        request.title,
        request.description or None,
        request.start_date,
        request.end_date or None,
    )
    return {
        "id_event":    event["id_event"],
        "title":       event["title"],
        "description": event["description"] or "",
        "start_date":  event["start_date"].isoformat(),
        "end_date":    event["end_date"].isoformat() if event["end_date"] else None,
    }


@app.put("/api/calendar/{event_id}")
async def calendar_update_endpoint(event_id: int, request: CalendarEventUpdate):
    event = await update_calendar_event(
        event_id,
        request.user_id,
        request.title,
        request.description or None,
        request.start_date,
        request.end_date or None,
    )
    if not event:
        return {"error": "Event not found"}
    return {
        "id_event":    event["id_event"],
        "title":       event["title"],
        "description": event["description"] or "",
        "start_date":  event["start_date"].isoformat(),
        "end_date":    event["end_date"].isoformat() if event["end_date"] else None,
    }


@app.delete("/api/calendar/{event_id}")
async def calendar_delete_endpoint(event_id: int, user_id: int):
    ok = await delete_calendar_event(event_id, user_id)
    return {"ok": ok}


@app.get("/api/schedule")
async def schedule_list_endpoint(session_id: int):
    entries = await get_schedule_entries(session_id)
    return {"entries": entries}


@app.post("/api/schedule")
async def schedule_create_endpoint(request: ScheduleEntryCreate):
    entry = await create_schedule_entry(
        request.session_id,
        request.date,
        request.topics,
        request.duration_hours,
        request.note or None,
    )
    return entry


@app.put("/api/schedule/{entry_id}")
async def schedule_update_endpoint(entry_id: int, request: ScheduleEntryUpdate):
    entry = await update_schedule_entry(
        entry_id,
        request.session_id,
        request.date,
        request.topics,
        request.duration_hours,
        request.note or None,
    )
    if not entry:
        return {"error": "Entry not found"}
    return entry


@app.delete("/api/schedule/{entry_id}")
async def schedule_delete_endpoint(entry_id: int, session_id: int):
    ok = await delete_schedule_entry(entry_id, session_id)
    return {"ok": ok}


@app.get("/api/stats")
async def stats_endpoint(session_id: int):
    session, summary = await asyncio.gather(
        get_session(session_id),
        get_session_cost_summary(session_id),
    )
    return {
        "budget": {
            "visible_limit":  float(session["daily_visible_limit"]),
            "visible_used":   float(session["visible_used"]),
            "shadow_reserve": float(session["shadow_reserve"]),
            "shadow_used":    float(session["shadow_used"]),
            "quiz_bonus":     float(session["quiz_bonus"]),
        },
        "activity": {
            "local_requests": int(summary["local_requests"]),
            "cloud_requests": int(summary["cloud_requests"]),
            "total_spend":    float(summary["total_spend"]),
            "total_reward":   float(summary["total_reward"]),
        },
    }


@app.delete("/api/session/{session_id}")
async def delete_session_endpoint(session_id: int):
    await delete_session(session_id)
    return {"ok": True}


@app.post("/api/quiz/submit")
async def quiz_submit_endpoint(request: QuizSubmitRequest):
    import json as _json
    result = await submit_quiz_answers(
        session_id=request.session_id,
        tool_output_id=request.tool_output_id,
        answers=request.answers,
    )
    return _json.loads(result)


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
        "requires_tool":    bool(request.force_tool),
        "forced_tool_name": request.force_tool or None,
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
