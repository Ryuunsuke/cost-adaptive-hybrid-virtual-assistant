import QuizDisplay from './QuizDisplay';
import './Message.css';

const BADGE_MAP = {
  'llama3.2:3b':                              { label: 'Local',              cls: 'badge-local'   },
  'GPT-4o mini':                              { label: 'GPT-4o mini',        cls: 'badge-mini'    },
  'GPT-4o':                                   { label: 'GPT-4o',             cls: 'badge-complex' },
  'GPT-4o mini (tool path)':                  { label: 'GPT-4o mini + tools',cls: 'badge-tool'    },
  'GPT-4o mini (tool path – synthesis blocked)': { label: 'GPT-4o mini + tools', cls: 'badge-tool' },
};

function tryParseQuiz(text) {
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed.tool_output_id === 'number' && Array.isArray(parsed.questions)) {
      return { type: 'quiz', data: parsed };
    }
    if (parsed && parsed.quiz_completed === true) {
      return { type: 'completed', data: parsed };
    }
  } catch {}
  return null;
}

function tryParseSchedule(text) {
  try {
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed) && parsed.length > 0 && typeof parsed[0].day === 'string') {
      return parsed;
    }
  } catch {}
  return null;
}

function ScheduleInline({ entries }) {
  return (
    <div className="schedule-inline">
      <div className="si-header">Study Schedule — {entries.length} day{entries.length !== 1 ? 's' : ''}</div>
      <div className="si-list">
        {entries.map((e, i) => (
          <div key={i} className="si-entry">
            <span className="si-date">{e.day}</span>
            <span className="si-topics">
              {Array.isArray(e.topics) ? e.topics.join(' · ') : e.topics}
            </span>
            <span className="si-hours">{e.hours}h</span>
          </div>
        ))}
      </div>
      <p className="si-tip">Open the Schedule tab to edit or manage these entries.</p>
    </div>
  );
}

function Message({ message, sessionId }) {
  const { text, sender, model } = message;
  const badge = sender === 'assistant' && model ? BADGE_MAP[model] : null;
  const quizInfo = sender === 'assistant' ? tryParseQuiz(text) : null;
  const scheduleEntries = !quizInfo && sender === 'assistant' ? tryParseSchedule(text) : null;

  return (
    <div className={`message ${sender}`}>
      <div className="message-wrapper">
        {badge && (
          <span className={`model-badge ${badge.cls}`}>{badge.label}</span>
        )}
        {quizInfo?.type === 'quiz'
          ? <QuizDisplay quiz={quizInfo.data} sessionId={sessionId} />
          : quizInfo?.type === 'completed'
          ? <QuizDisplay completed={quizInfo.data} />
          : scheduleEntries
          ? <ScheduleInline entries={scheduleEntries} />
          : <div className="message-content">{text}</div>
        }
      </div>
    </div>
  );
}

export default Message;
