import { useState, useEffect } from 'react';
import './Stats.css';

function ProgressBar({ used, total }) {
  const pct = total > 0 ? Math.min((used / total) * 100, 100) : 0;
  const color = pct >= 90 ? '#dc3545' : pct >= 60 ? '#fd7e14' : '#007bff';
  return (
    <div className="progress-track">
      <div className="progress-fill" style={{ width: `${pct}%`, backgroundColor: color }} />
    </div>
  );
}

function Stats({ sessionId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`http://localhost:8000/api/stats?session_id=${sessionId}`)
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [sessionId]);

  if (loading) return <div className="stats-loading">Loading stats...</div>;
  if (!data)   return <div className="stats-loading">Could not load stats.</div>;

  const { budget, activity } = data;
  const totalRequests = activity.local_requests + activity.cloud_requests;

  return (
    <div className="stats-panel">
      <section className="stats-section">
        <h2>Budget</h2>

        <div className="stat-row">
          <div className="stat-label">
            <span>Visible Pool</span>
            <span className="stat-value">{budget.visible_used.toFixed(0)} / {budget.visible_limit.toFixed(0)}</span>
          </div>
          <ProgressBar used={budget.visible_used} total={budget.visible_limit} />
        </div>

        <div className="stat-row">
          <div className="stat-label">
            <span>Quiz Bonus</span>
            <span className="stat-value">{budget.quiz_bonus.toFixed(0)} tokens</span>
          </div>
        </div>
      </section>

      <section className="stats-section">
        <h2>Session Activity</h2>
        <div className="activity-grid">
          <div className="activity-card">
            <span className="activity-number">{totalRequests}</span>
            <span className="activity-label">Total Requests</span>
          </div>
          <div className="activity-card">
            <span className="activity-number local">{activity.local_requests}</span>
            <span className="activity-label">Local (Free)</span>
          </div>
          <div className="activity-card">
            <span className="activity-number cloud">{activity.cloud_requests}</span>
            <span className="activity-label">Cloud</span>
          </div>
          <div className="activity-card">
            <span className="activity-number">${activity.total_spend.toFixed(4)}</span>
            <span className="activity-label">Total Cost</span>
          </div>
        </div>
      </section>
    </div>
  );
}

export default Stats;
