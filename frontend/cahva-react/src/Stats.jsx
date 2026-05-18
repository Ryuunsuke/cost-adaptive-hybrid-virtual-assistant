import { useState, useEffect } from 'react';
import './Stats.css';

function ProgressBar({ remaining, total }) {
  const pct = total > 0 ? Math.min((remaining / total) * 100, 100) : 0;
  // Red when nearly empty, orange when low, blue when healthy
  const color = pct <= 10 ? '#dc3545' : pct <= 40 ? '#fd7e14' : '#007bff';
  return (
    <div className="progress-track">
      <div className="progress-fill" style={{ width: `${pct}%`, backgroundColor: color }} />
    </div>
  );
}

function Stats({ sessionId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchStats = () => {
    setLoading(true);
    fetch(`http://localhost:8000/api/stats?session_id=${sessionId}`)
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  };

  useEffect(() => { fetchStats(); }, [sessionId]);

  if (loading) return <div className="stats-loading">Loading stats...</div>;
  if (!data)   return <div className="stats-loading">Could not load stats.</div>;

  const { budget, activity } = data;
  const totalRequests = activity.local_requests + activity.cloud_requests;

  return (
    <div className="stats-panel">
      <section className="stats-section">
        <div className="stats-section-header">
          <h2>Budget</h2>
          <button className="stats-refresh-btn" onClick={fetchStats}>↻ Refresh</button>
        </div>

        <div className="stat-row">
          <div className="stat-label">
            <span>Available Tokens</span>
            <span className="stat-value">
              {(Math.max(0, budget.visible_limit - budget.visible_used) + budget.quiz_bonus).toFixed(0)}
              {' / '}
              {budget.visible_limit.toFixed(0)}
              {budget.quiz_bonus > 0 && (
                <span style={{ color: '#2e7d32', fontSize: '0.82em' }}> (+{budget.quiz_bonus.toFixed(0)} bonus)</span>
              )}
            </span>
          </div>
          <ProgressBar
            remaining={Math.max(0, budget.visible_limit - budget.visible_used)}
            total={budget.visible_limit}
          />
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
