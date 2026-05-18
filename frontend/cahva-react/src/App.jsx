import { useState } from 'react';
import UsernamePrompt from './UsernamePrompt';
import SessionList from './SessionList';
import Calendar from './Calendar';
import Chat from './Chat';
import './App.css';

function App() {
  const [screen, setScreen] = useState('login');
  const [user, setUser] = useState(null);
  const [sessionId, setSessionId] = useState(null);

  const startNewSession = async (userId) => {
    const res = await fetch("http://localhost:8000/api/session/new", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId }),
    });
    const data = await res.json();
    const newSession = {
      session_id:          data.session_id,
      started_at:          data.started_at,
      daily_visible_limit: data.daily_visible_limit,
      visible_used:        data.visible_used,
      quiz_bonus:          data.quiz_bonus,
    };
    setUser(prev => ({ ...prev, sessions: [newSession, ...prev.sessions] }));
    setSessionId(data.session_id);
    setScreen('chat');
  };

  const handleLogin = async (data) => {
    setUser({ userId: data.user_id, username: data.username, sessions: data.sessions });
    if (data.sessions.length === 0) {
      await startNewSession(data.user_id);
    } else {
      setScreen('sessions');
    }
  };

  const handleSelectSession = (id) => {
    setSessionId(id);
    setScreen('chat');
  };

  const handleDeleteSession = async (id) => {
    await fetch(`http://localhost:8000/api/session/${id}`, { method: "DELETE" });
    setUser(prev => ({ ...prev, sessions: prev.sessions.filter(s => s.session_id !== id) }));
  };

  const handleBackToSessions = async () => {
    try {
      const res = await fetch(`http://localhost:8000/api/sessions?user_id=${user.userId}`);
      const data = await res.json();
      setUser(prev => ({ ...prev, sessions: data.sessions }));
    } catch {}
    setScreen('sessions');
  };

  if (screen === 'login') {
    return <UsernamePrompt onLogin={handleLogin} />;
  }

  if (screen === 'calendar') {
    return (
      <Calendar
        userId={user.userId}
        onBack={() => setScreen('sessions')}
      />
    );
  }

  if (screen === 'sessions') {
    return (
      <SessionList
        username={user.username}
        sessions={user.sessions}
        onSelectSession={handleSelectSession}
        onNewSession={() => startNewSession(user.userId)}
        onDeleteSession={handleDeleteSession}
        onOpenCalendar={() => setScreen('calendar')}
      />
    );
  }

  return (
    <div className="app">
      <Chat
        sessionId={sessionId}
        username={user.username}
        onBack={handleBackToSessions}
      />
    </div>
  );
}

export default App;
