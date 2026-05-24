import './SessionList.css';

function SessionTokensUsed({ session }) {
  const used  = session.visible_used ?? 0;
  const bonus = session.quiz_bonus   ?? 0;
  return (
    <div className="session-tokens-used">
      <span className="budget-used">{Math.round(used)}</span>
      <span className="session-tokens-label"> tokens spent</span>
      {bonus > 0 && <span className="budget-bonus"> · +{Math.round(bonus)} bonus</span>}
    </div>
  );
}

const GLOBAL_LIMIT = 5000;

function GlobalBudgetCard({ sessions }) {
  const totalUsed  = sessions.reduce((s, x) => s + (x.visible_used ?? 0), 0);
  const totalBonus = sessions.reduce((s, x) => s + (x.quiz_bonus   ?? 0), 0);
  // Effective remaining = unused visible tokens + earned bonus
  const remaining  = Math.max(0, GLOBAL_LIMIT - totalUsed) + totalBonus;
  // Progress bar based only on visible portion (capped at 100%)
  const visiblePct = Math.min(Math.max(0, GLOBAL_LIMIT - totalUsed) / GLOBAL_LIMIT * 100, 100);
  const bonusPct   = Math.min(totalBonus / GLOBAL_LIMIT * 100, 100 - visiblePct);
  const barColor   = visiblePct <= 10 ? '#e53935' : visiblePct <= 40 ? '#fb8c00' : '#1976d2';

  return (
    <div className="global-budget-card">
      <div className="global-budget-header">
        <span className="global-budget-label">Total Budget</span>
        <span className="global-budget-numbers">
          <span className="budget-used">{Math.round(remaining)}</span>
          <span className="budget-sep"> / </span>
          <span className="budget-total">{GLOBAL_LIMIT}</span>
          <span className="global-budget-unit"> tokens remaining</span>
        </span>
      </div>
      <div className="budget-track" style={{ height: 6 }}>
        <div className="budget-fill" style={{ width: `${visiblePct}%`, background: barColor }} />
        {totalBonus > 0 && (
          <div className="budget-bonus-fill" style={{ width: `${bonusPct}%` }} />
        )}
      </div>
      <div className="global-budget-meta">
        {Math.round(totalUsed)} used across {sessions.length} session{sessions.length !== 1 ? 's' : ''}
        {totalBonus > 0 && <span className="global-bonus-note"> · +{Math.round(totalBonus)} bonus</span>}
      </div>
    </div>
  );
}

function SessionList({ username, sessions, onSelectSession, onNewSession, onDeleteSession, onOpenCalendar }) {
  return (
    <div className="session-list-page">
      <div className="session-list-container">
        <div className="session-list-header">
          <h1>Welcome back, {username}</h1>
          <button className="cal-open-btn" onClick={onOpenCalendar} title="Open Calendar">
            &#128197; Schedule
          </button>
        </div>

        {sessions.length > 0 && <GlobalBudgetCard sessions={sessions} />}

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
                  <span className="session-date">
                    {new Date(s.started_at).toLocaleString()}
                  </span>
                  <SessionTokensUsed session={s} />
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
