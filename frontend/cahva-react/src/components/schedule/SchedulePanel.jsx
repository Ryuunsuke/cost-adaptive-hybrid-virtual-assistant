import { useState, useEffect } from 'react';
import './SchedulePanel.css';

const EMPTY_FORM = { date: '', topics: '', duration_hours: 2, note: '' };

function entryToForm(entry) {
  return {
    date: entry.date,
    topics: Array.isArray(entry.topics) ? entry.topics.join(', ') : entry.topics,
    duration_hours: entry.duration_hours,
    note: entry.note || '',
  };
}

function SchedulePanel({ sessionId }) {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);

  const fetchEntries = async () => {
    setLoading(true);
    try {
      const res = await fetch(`http://localhost:8000/api/schedule?session_id=${sessionId}`);
      const data = await res.json();
      setEntries(data.entries || []);
    } catch {}
    setLoading(false);
  };

  useEffect(() => { fetchEntries(); }, [sessionId]);

  const openAdd = () => {
    setEditId(null);
    setForm(EMPTY_FORM);
    setShowForm(true);
  };

  const openEdit = (entry) => {
    setEditId(entry.id_entry);
    setForm(entryToForm(entry));
    setShowForm(true);
  };

  const handleCancel = () => {
    setShowForm(false);
    setEditId(null);
    setForm(EMPTY_FORM);
  };

  const handleSubmit = async () => {
    const topics = form.topics.split(',').map(t => t.trim()).filter(Boolean);
    const body = {
      session_id: sessionId,
      date: form.date,
      topics,
      duration_hours: parseFloat(form.duration_hours) || 2,
      note: form.note,
    };
    if (editId) {
      await fetch(`http://localhost:8000/api/schedule/${editId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    } else {
      await fetch('http://localhost:8000/api/schedule', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    }
    handleCancel();
    fetchEntries();
  };

  const handleDelete = async (id) => {
    await fetch(`http://localhost:8000/api/schedule/${id}?session_id=${sessionId}`, {
      method: 'DELETE',
    });
    fetchEntries();
  };

  const set = (key, val) => setForm(f => ({ ...f, [key]: val }));

  if (loading) return <div className="sp-loading">Loading schedule…</div>;

  return (
    <div className="sp-panel">
      <div className="sp-header">
        <h2 className="sp-title">Session Schedule</h2>
        <button className="sp-add-btn" onClick={showForm && !editId ? handleCancel : openAdd}>
          {showForm && !editId ? '✕ Cancel' : '+ Add Entry'}
        </button>
      </div>

      {showForm && (
        <div className="sp-form">
          <p className="sp-form-title">{editId ? 'Edit Entry' : 'New Entry'}</p>
          <div className="sp-form-grid">
            <label className="sp-label">
              Date
              <input
                type="date"
                className="sp-input"
                value={form.date}
                onChange={e => set('date', e.target.value)}
              />
            </label>
            <label className="sp-label">
              Duration (h)
              <input
                type="number"
                className="sp-input"
                min="0.5" max="24" step="0.5"
                value={form.duration_hours}
                onChange={e => set('duration_hours', e.target.value)}
              />
            </label>
          </div>
          <label className="sp-label">
            Topics <span className="sp-hint">(comma-separated)</span>
            <input
              type="text"
              className="sp-input"
              placeholder="e.g. Calculus, Linear Algebra"
              value={form.topics}
              onChange={e => set('topics', e.target.value)}
            />
          </label>
          <label className="sp-label">
            Notes
            <textarea
              className="sp-textarea"
              rows={2}
              value={form.note}
              onChange={e => set('note', e.target.value)}
            />
          </label>
          <div className="sp-form-actions">
            <button className="sp-save-btn" onClick={handleSubmit} disabled={!form.date || !form.topics.trim()}>
              {editId ? 'Save Changes' : 'Add Entry'}
            </button>
            <button className="sp-cancel-btn" onClick={handleCancel}>Cancel</button>
          </div>
        </div>
      )}

      {entries.length === 0 ? (
        <div className="sp-empty">
          No schedule entries yet. Ask the assistant to create a study plan, or add entries manually.
        </div>
      ) : (
        <div className="sp-entries">
          {entries.map(entry => (
            <div key={entry.id_entry} className="sp-entry">
              <div className="sp-entry-date">
                {new Date(entry.date + 'T00:00:00').toLocaleDateString(undefined, {
                  weekday: 'short', month: 'short', day: 'numeric',
                })}
              </div>
              <div className="sp-entry-body">
                <div className="sp-entry-topics">
                  {(Array.isArray(entry.topics) ? entry.topics : [entry.topics]).map((t, i) => (
                    <span key={i} className="sp-topic-tag">{t}</span>
                  ))}
                </div>
                {entry.note && <div className="sp-entry-note">{entry.note}</div>}
              </div>
              <div className="sp-entry-right">
                <span className="sp-entry-hours">{entry.duration_hours}h</span>
                <div className="sp-entry-actions">
                  <button className="sp-edit-btn" onClick={() => openEdit(entry)} title="Edit">✏</button>
                  <button className="sp-delete-btn" onClick={() => handleDelete(entry.id_entry)} title="Delete">✕</button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default SchedulePanel;
