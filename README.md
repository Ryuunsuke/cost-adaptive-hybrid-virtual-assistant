# CAHVA — Cost-Adaptive Hybrid Virtual Assistant

A study assistant that routes every request through the cheapest model that can handle it. Simple questions stay local (free); complex tasks escalate to GPT-4o mini or GPT-4o only when needed. Quiz performance earns bonus tokens that extend the cloud budget.

---

## Features

### Cost-Adaptive Routing
Every message is classified by a local llama3.2:3b model and routed to one of four paths:

| Path | Model | Trigger |
|------|-------|---------|
| **Local** | llama3.2:3b (Ollama) | Administrative ≥ 0.70 or Informational ≥ 0.85 confidence |
| **Cloud Standard** | GPT-4o mini | Below confidence threshold |
| **Cloud Complex** | GPT-4o | Analytical / multi-step reasoning |
| **Tool Executor** | GPT-4o mini + MCP tools | Forced tool call from frontend |

### Document Source Mode
Upload PDFs and toggle individual files as active sources. When sources are active, all chat questions are answered by the local model using only the text from the selected files — no cloud spend, fully grounded.

### Quiz Generation
Generate multiple-choice quizzes from uploaded documents, selected source files, or a topic string. Questions are stripped of answers before being sent to the frontend; correct answers and explanations are only revealed after all answers are submitted. Perfect scores earn +500 bonus tokens. With multiple source files active, question count scales automatically (10 + 5 per additional source).

### Flashcard Generator
Generate 10 term/definition flip-cards from an uploaded document or a topic. Entirely local — zero cost. Cards are cached until a new file is uploaded.

### Document Summarisation
Summarise an uploaded PDF into structured key points using GPT-4o mini.

### Study Schedule
Generate a day-by-day study plan from a list of topics and a deadline. Entries are saved to the database and appear on the Schedule tab and the Calendar.

### Session Budget Dashboard
The Stats tab shows visible token usage, shadow reserve, quiz bonus earned, and a breakdown of local vs. cloud requests for the current session.

---

## Technology

### Backend
| Package | Version | Purpose |
|---------|---------|---------|
| FastAPI | 0.135.3 | REST API framework |
| Uvicorn | 0.44.0 | ASGI server |
| LangGraph | 1.1.6 | Routing pipeline (state-machine graph) |
| LangChain | 1.2.15 | LLM abstraction layer |
| Ollama | 0.6.1 | Local model inference (llama3.2:3b) |
| OpenAI | 2.37.0 | GPT-4o / GPT-4o mini API |
| FastMCP | 3.2.0 | MCP tool protocol |
| asyncpg | 0.31.0 | Async PostgreSQL driver |
| pypdf | 6.11.0 | PDF text extraction |
| Pydantic | 2.12.5 | Request/response validation |

### Frontend
| Package | Version | Purpose |
|---------|---------|---------|
| React | 19.2.4 | UI framework |
| Vite | 8.0.4 | Build tool / dev server |

### Infrastructure
- **PostgreSQL** — persistent storage for users, sessions, messages, files, tool outputs, quiz attempts, schedule entries, and route logs
- **Ollama** — runs llama3.2:3b locally for triage, argument extraction, and flashcard/schedule generation

---

## API Endpoints

### Auth & Sessions
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/login` | Upsert user by username, return user ID + session list |
| `GET` | `/api/sessions` | List all sessions for a user |
| `POST` | `/api/session/new` | Create a new session |
| `DELETE` | `/api/session/{session_id}` | Delete a session and all its data |

### Chat
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/chat` | Send a message; routes through the LangGraph pipeline |
| `GET` | `/api/history` | Fetch message history for a session (last 500) |

`POST /api/chat` request body:
```json
{
  "session_id": 1,
  "message": "Explain photosynthesis",
  "force_tool": "",
  "source_file_ids": []
}
```

### Files
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/upload` | Upload a PDF; extracts and stores text |
| `GET` | `/api/files` | List all uploaded files for a session |
| `GET` | `/api/file` | Get the most recent uploaded file for a session |

### Quiz
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/quiz/submit` | Submit answers; returns per-question feedback and score |
| `POST` | `/api/quiz/regenerate` | Force-generate a new quiz avoiding previously used questions |

### Schedule
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/schedule` | List schedule entries for a session |
| `POST` | `/api/schedule` | Create a schedule entry |
| `PUT` | `/api/schedule/{entry_id}` | Update a schedule entry |
| `DELETE` | `/api/schedule/{entry_id}` | Delete a schedule entry |
| `GET` | `/api/schedule/user` | All schedule entries across all sessions for a user |

### Calendar
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/calendar` | List calendar events for a user |
| `POST` | `/api/calendar` | Create a calendar event |
| `PUT` | `/api/calendar/{event_id}` | Update a calendar event |
| `DELETE` | `/api/calendar/{event_id}` | Delete a calendar event |

### Stats
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/stats` | Session budget summary (tokens used/remaining, local vs cloud requests) |

---

## MCP Tools

Tools are invoked via `/api/chat` using `force_tool` or automatically when the triage classifier sets `requires_tool: true`.

| Tool | Model | Cost Pool | Description |
|------|-------|-----------|-------------|
| `generate_quiz` | GPT-4o mini | Shadow reserve | Generate N multiple-choice questions from a document or topic. With multiple source files, question count scales: 10 + (N−1) × 5 |
| `submit_quiz_answers` | — | None | Grade submitted answers; reveal correct answers and explanations; award +500 bonus tokens for a perfect score |
| `summarize_document` | GPT-4o mini | Visible/bonus | Summarise an uploaded PDF into structured key points |
| `create_schedule` | GPT-4o mini | Visible/bonus | Build a day-by-day study schedule from topics and a deadline; persists entries to the database |
| `generate_flashcards` | llama3.2:3b | None (local) | Generate 10 term/definition flashcard pairs from a document or topic |

---

## Project Structure

```
CAHVA/
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── main.py                  # FastAPI app, all endpoints
│       ├── .env                     # Environment variables (see setup)
│       ├── services/
│       │   ├── task_router.py       # LangGraph pipeline (triage → routing → nodes)
│       │   ├── LLMs.py              # local_response / cloud_response wrappers
│       │   ├── db_con.py            # asyncpg database layer
│       │   ├── cost_tracker.py      # Budget pools, deductions, route logging
│       │   └── tools/
│       │       └── __init__.py      # Tool registry, ToolProxy, argument extractors
│       └── mcp/
│           └── tools/
│               ├── __init__.py
│               ├── summarize_doc.py
│               ├── create_schedule.py
│               ├── quiz/
│               │   ├── quiz_gen.py
│               │   └── submit_quiz_ans.py
│               └── flashcard/
│                   └── flashcard_gen.py
└── frontend/
    └── cahva-react/
        ├── package.json
        └── src/
            ├── App.jsx
            ├── Chat.jsx / Chat.css
            ├── Message.jsx / Message.css
            ├── FileUpload.jsx / FileUpload.css
            ├── QuizDisplay.jsx / QuizDisplay.css
            ├── FlashcardDisplay.jsx / FlashcardDisplay.css
            ├── SchedulePanel.jsx / SchedulePanel.css
            ├── Calendar.jsx / Calendar.css
            └── Stats.jsx
```

---

## Setup

### Prerequisites
- Python 3.11+
- Node.js 18+
- PostgreSQL 14+
- [Ollama](https://ollama.com) with `llama3.2:3b` pulled
- OpenAI API key

### 1. Clone the repository
```bash
git clone https://github.com/Ryuunsuke/cost-adaptive-hybrid-virtual-assistant.git

cd CAHVA
```

### 2. Pull the local model
```bash
ollama pull llama3.2:3b
```

### 3. Create the PostgreSQL database
```sql
CREATE DATABASE cahvadb;
```
The schema is bootstrapped automatically on first startup — no migration scripts needed.

### 4. Configure the backend environment
Create `backend/app/.env`:
```env
DATABASE_URL=postgresql://postgres:<password>@localhost:5432/cahvadb
OPENAI_API_KEY=sk-...
```

### 5. Install backend dependencies
```bash
cd backend
pip install -r requirements.txt
```

### 6. Start the backend
```bash
cd backend/app
python main.py
```
The API will be available at `http://localhost:8000`.

### 7. Install and start the frontend
```bash
cd frontend/cahva-react
npm install
npm run dev
```
The app will be available at `http://localhost:5173`.

---

## Budget System

Each session has three token pools:

| Pool | Purpose | Refill |
|------|---------|--------|
| **Visible** | Regular cloud requests (GPT-4o mini / GPT-4o) | Daily |
| **Shadow reserve** | Quiz generation only — always available even when visible = 0 | Daily |
| **Quiz bonus** | Earned by getting a perfect quiz score (+500 per perfect submission) | Earned in-session |

The local model (llama3.2:3b) costs nothing and is used whenever the triage classifier is confident enough.
