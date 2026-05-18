import { useState, useEffect } from 'react';
import './Calendar.css';

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = [
  'January','February','March','April','May','June',
  'July','August','September','October','November','December',
];

function isSameDay(a, b) {
  return a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate();
}

function toLocalDatetimeValue(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function EventModal({ event, onSave, onDelete, onClose }) {
  const isNew = !event.id_event;
  const [title, setTitle] = useState(event.title || '');
  const [description, setDescription] = useState(event.description || '');
  const [startDate, setStartDate] = useState(
    event.start_date ? toLocalDatetimeValue(event.start_date) : toLocalDatetimeValue(event._defaultDate)
  );
  const [endDate, setEndDate] = useState(
    event.end_date ? toLocalDatetimeValue(event.end_date) : ''
  );
  const [error, setError] = useState('');

  const handleSave = () => {
    if (!title.trim()) { setError('Title is required.'); return; }
    if (!startDate)    { setError('Start date is required.'); return; }
    onSave({ title: title.trim(), description, start_date: new Date(startDate).toISOString(), end_date: endDate ? new Date(endDate).toISOString() : null });
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3>{isNew ? 'New Event' : 'Edit Event'}</h3>
          <button className="modal-close" onClick={onClose}>&#x2715;</button>
        </div>
        <div className="modal-body">
          <label>Title</label>
          <input
            className="modal-input"
            value={title}
            onChange={e => setTitle(e.target.value)}
            placeholder="Event title"
            autoFocus
          />
          <label>Description</label>
          <textarea
            className="modal-textarea"
            value={description}
            onChange={e => setDescription(e.target.value)}
            placeholder="Optional description"
            rows={2}
          />
          <label>Start</label>
          <input
            className="modal-input"
            type="datetime-local"
            value={startDate}
            onChange={e => setStartDate(e.target.value)}
          />
          <label>End (optional)</label>
          <input
            className="modal-input"
            type="datetime-local"
            value={endDate}
            onChange={e => setEndDate(e.target.value)}
          />
          {error && <p className="modal-error">{error}</p>}
        </div>
        <div className="modal-footer">
          {!isNew && (
            <button className="modal-btn btn-delete" onClick={onDelete}>Delete</button>
          )}
          <button className="modal-btn btn-cancel" onClick={onClose}>Cancel</button>
          <button className="modal-btn btn-save" onClick={handleSave}>Save</button>
        </div>
      </div>
    </div>
  );
}

function Calendar({ userId, onBack }) {
  const today = new Date();
  const [current, setCurrent] = useState(new Date(today.getFullYear(), today.getMonth(), 1));
  const [events, setEvents] = useState([]);
  const [studyEntries, setStudyEntries] = useState([]);
  const [modal, setModal] = useState(null); // null | event-like object (new or existing)

  const fetchEvents = () => {
    fetch(`http://localhost:8000/api/calendar?user_id=${userId}`)
      .then(r => r.json())
      .then(d => setEvents(d.events || []))
      .catch(() => {});
  };

  const fetchStudyEntries = () => {
    fetch(`http://localhost:8000/api/schedule/user?user_id=${userId}`)
      .then(r => r.json())
      .then(d => setStudyEntries(d.entries || []))
      .catch(() => {});
  };

  useEffect(() => { fetchEvents(); fetchStudyEntries(); }, [userId]);

  const prevMonth = () => setCurrent(new Date(current.getFullYear(), current.getMonth() - 1, 1));
  const nextMonth = () => setCurrent(new Date(current.getFullYear(), current.getMonth() + 1, 1));

  const handleDayClick = (date) => {
    const iso = date.toISOString();
    setModal({ _defaultDate: iso, title: '', description: '', start_date: null, end_date: null });
  };

  const handleEventClick = (e, ev) => {
    e.stopPropagation();
    setModal({ ...ev });
  };

  const handleSave = async (data) => {
    if (modal.id_event) {
      await fetch(`http://localhost:8000/api/calendar/${modal.id_event}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, ...data }),
      });
    } else {
      await fetch('http://localhost:8000/api/calendar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId, ...data }),
      });
    }
    setModal(null);
    fetchEvents();
  };

  const handleDelete = async () => {
    if (!modal.id_event) return;
    await fetch(`http://localhost:8000/api/calendar/${modal.id_event}?user_id=${userId}`, {
      method: 'DELETE',
    });
    setModal(null);
    fetchEvents();
  };

  // Build calendar grid
  const year = current.getFullYear();
  const month = current.getMonth();
  const firstDow = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cells = [];
  for (let i = 0; i < firstDow; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(new Date(year, month, d));
  while (cells.length % 7 !== 0) cells.push(null);

  const eventsOnDay = (date) => {
    if (!date) return [];
    return events.filter(ev => {
      const evDate = new Date(ev.start_date);
      return isSameDay(evDate, date);
    });
  };

  const studyOnDay = (date) => {
    if (!date) return [];
    return studyEntries.filter(se => {
      // se.date is 'YYYY-MM-DD' from the backend
      const seDate = new Date(se.date + 'T00:00:00');
      return isSameDay(seDate, date);
    });
  };

  return (
    <div className="calendar-page">
      <div className="calendar-container">
        <div className="calendar-topbar">
          <button className="cal-back-btn" onClick={onBack}>&#8592; Sessions</button>
          <h1>Schedule</h1>
        </div>

        <div className="cal-nav">
          <button className="cal-nav-btn" onClick={prevMonth}>&#8249;</button>
          <span className="cal-month-label">{MONTHS[month]} {year}</span>
          <button className="cal-nav-btn" onClick={nextMonth}>&#8250;</button>
        </div>

        <div className="cal-grid">
          {DAYS.map(d => (
            <div key={d} className="cal-day-header">{d}</div>
          ))}
          {cells.map((date, i) => {
            const dayEvents  = eventsOnDay(date);
            const dayStudy   = studyOnDay(date);
            const isToday    = date && isSameDay(date, today);
            return (
              <div
                key={i}
                className={`cal-cell${date ? ' cal-cell-active' : ' cal-cell-empty'}${isToday ? ' cal-today' : ''}`}
                onClick={date ? () => handleDayClick(date) : undefined}
              >
                {date && <span className="cal-date-num">{date.getDate()}</span>}
                {dayEvents.map(ev => (
                  <div
                    key={ev.id_event}
                    className="cal-event-chip"
                    onClick={(e) => handleEventClick(e, ev)}
                    title={ev.description}
                  >
                    {ev.title}
                  </div>
                ))}
                {dayStudy.map(se => {
                  const topics = Array.isArray(se.topics) ? se.topics : [se.topics];
                  const label  = topics[0] + (topics.length > 1 ? ` +${topics.length - 1}` : '');
                  const tip    = `${topics.join(', ')} · ${se.duration_hours}h${se.note ? ' · ' + se.note : ''}`;
                  return (
                    <div
                      key={se.id_entry}
                      className="cal-study-chip"
                      title={tip}
                    >
                      {label}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>

      {modal && (
        <EventModal
          event={modal}
          onSave={handleSave}
          onDelete={handleDelete}
          onClose={() => setModal(null)}
        />
      )}
    </div>
  );
}

export default Calendar;
