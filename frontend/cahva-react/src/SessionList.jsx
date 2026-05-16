import './SessionList.css';

function SessionList({ username, sessions, onSelectSession, onNewSession, onDeleteSession }) {
  return (
    <div className="session-list-page">
      <div className="session-list-container">
        <h1>Welcome back, {username}</h1>
        <button className="new-session-btn" onClick={onNewSession}>
          + New Session
        </button>
        {sessions.length > 0 && (
          <div className="sessions">
            <h2>Previous Sessions</h2>
            {sessions.map(s => (
              <div key={s.session_id} className="session-item">
                <button
                  className="session-select"
                  onClick={() => onSelectSession(s.session_id)}
                >
                  {new Date(s.started_at).toLocaleString()}
                </button>
                <button
                  className="session-delete"
                  onClick={() => onDeleteSession(s.session_id)}
                  title="Delete session"
                >
                  &#x2715;
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default SessionList;
