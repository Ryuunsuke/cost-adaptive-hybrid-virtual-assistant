import { useState, useEffect } from 'react';
import './Stats.css';

function ProgressBar({ remaining, total }) {
  const pct = total > 0 ? Math.min((remaining / total) * 100, 100) : 0;
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
  const [showBonus, setShowBonus] = useState(false);

  const fetchStats = () => {
    setLoading(true);
    fetch(`http://localhost:8000/api/stats?session_id=${sessionId}`)
      .then(r => r.json())
      .then(d => {
        setData(d);
        setLoading(false);

        const bonus = d.budget?.quiz_bonus || 0;
        const seenKey = `bonus_seen_${sessionId}`;
        const seenBonus = parseFloat(localStorage.getItem(seenKey) || '0');

        if (bonus > seenBonus) {
          // New or increased bonus — show the label once, then mark as seen
          setShowBonus(true);
          localStorage.setItem(seenKey, String(bonus));
        } else {
          setShowBonus(false);
          // Keep the stored value in sync with spending so future bonuses trigger again
          if (bonus < seenBonus) {
            localStorage.setItem(seenKey, String(bonus));
          }
        }
      })
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
              {showBonus && (
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
