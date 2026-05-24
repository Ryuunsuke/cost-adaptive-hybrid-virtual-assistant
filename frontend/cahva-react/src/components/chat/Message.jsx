import QuizDisplay from '../widgets/QuizDisplay';
import FlashcardDisplay from '../widgets/FlashcardDisplay';
import './Message.css';

// ── Inline formatter: **bold**, *italic*, `code` ──────────────────────────
function renderInline(text, baseKey) {
  const parts = [];
  const regex = /\*\*([^*\n]+)\*\*|\*([^*\n]+)\*|`([^`\n]+)`/g;
  let last = 0, m;
  while ((m = regex.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    if      (m[1] !== undefined) parts.push(<strong key={`${baseKey}-b${m.index}`}>{m[1]}</strong>);
    else if (m[2] !== undefined) parts.push(<em     key={`${baseKey}-i${m.index}`}>{m[2]}</em>);
    else if (m[3] !== undefined) parts.push(<code   key={`${baseKey}-c${m.index}`} className="md-code">{m[3]}</code>);
    last = regex.lastIndex;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts.length === 1 && typeof parts[0] === 'string' ? parts[0] : parts;
}

// ── Line-by-line markdown renderer ───────────────────────────────────────
// Processes the text one line at a time so mixed content (e.g. a sentence
// immediately followed by a numbered list without a blank line) is handled
// correctly, and code-fence contents are never split on blank lines.
function MarkdownText({ text }) {
  const lines = text.split('\n');
  const elements = [];
  let i = 0;
  let k = 0;

  while (i < lines.length) {
    const raw   = lines[i];
    const line  = raw.trim();

    // blank line — just advance
    if (!line) { i++; continue; }

    // ── fenced code block ────────────────────────────────────────────
    if (line.startsWith('```')) {
      const codeLines = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing fence
      elements.push(
        <pre key={k++} className="md-pre"><code>{codeLines.join('\n')}</code></pre>
      );
      continue;
    }

    // ── headings ─────────────────────────────────────────────────────
    const h3 = line.match(/^###\s+(.*)/);
    if (h3) { elements.push(<h3 key={k} className="md-h3">{renderInline(h3[1], k++)}</h3>); i++; continue; }
    const h2 = line.match(/^##\s+(.*)/);
    if (h2) { elements.push(<h2 key={k} className="md-h2">{renderInline(h2[1], k++)}</h2>); i++; continue; }
    const h1 = line.match(/^#\s+(.*)/);
    if (h1) { elements.push(<h1 key={k} className="md-h1">{renderInline(h1[1], k++)}</h1>); i++; continue; }

    // ── unordered list ───────────────────────────────────────────────
    if (/^[-*]\s/.test(line)) {
      const items = [];
      while (i < lines.length) {
        const l = lines[i].trim();
        if (/^[-*]\s/.test(l)) {
          items.push(l.replace(/^[-*]\s+/, ''));
          i++;
        } else if (!l) {
          // skip blank only if the next non-blank line is also a list item
          let j = i + 1;
          while (j < lines.length && !lines[j].trim()) j++;
          if (j < lines.length && /^[-*]\s/.test(lines[j].trim())) { i++; }
          else break;
        } else break;
      }
      elements.push(
        <ul key={k} className="md-ul">
          {items.map((item, j) => <li key={j}>{renderInline(item, `${k}-${j}`)}</li>)}
        </ul>
      );
      k++;
      continue;
    }

    // ── ordered list ─────────────────────────────────────────────────
    if (/^\d+\.\s/.test(line)) {
      const items = [];
      while (i < lines.length) {
        const l = lines[i].trim();
        if (/^\d+\.\s/.test(l)) {
          items.push(l.replace(/^\d+\.\s+/, ''));
          i++;
        } else if (!l) {
          // skip blank only if the next non-blank line is also a list item
          let j = i + 1;
          while (j < lines.length && !lines[j].trim()) j++;
          if (j < lines.length && /^\d+\.\s/.test(lines[j].trim())) { i++; }
          else break;
        } else break;
      }
      elements.push(
        <ol key={k} className="md-ol">
          {items.map((item, j) => <li key={j}>{renderInline(item, `${k}-${j}`)}</li>)}
        </ol>
      );
      k++;
      continue;
    }

    // ── paragraph — collect consecutive plain-text lines ─────────────
    const paraLines = [];
    while (i < lines.length) {
      const l = lines[i].trim();
      if (!l) { i++; break; }
      if (l.startsWith('```') || /^#{1,3}\s/.test(l) || /^[-*]\s/.test(l) || /^\d+\.\s/.test(l)) break;
      paraLines.push(lines[i].trim());
      i++;
    }
    if (paraLines.length) {
      const pk = k++;
      elements.push(
        <p key={pk} className="md-p">
          {paraLines.reduce((acc, pLine, j) => {
            if (j > 0) acc.push(<br key={`${pk}-br${j}`} />);
            const inlined = renderInline(pLine, `${pk}-${j}`);
            acc.push(...(Array.isArray(inlined) ? inlined : [inlined]));
            return acc;
          }, [])}
        </p>
      );
    }
  }

  return <>{elements}</>;
}

const BADGE_MAP = {
  'llama3.2:3b':                              { label: 'Local',              cls: 'badge-local'   },
  'GPT-4o mini':                              { label: 'GPT-4o mini',        cls: 'badge-mini'    },
  'GPT-4o':                                   { label: 'GPT-4o',             cls: 'badge-complex' },
  'GPT-4o mini (tool path)':                  { label: 'GPT-4o mini + tools',cls: 'badge-tool'    },
  'GPT-4o mini (tool path – synthesis blocked)': { label: 'GPT-4o mini + tools', cls: 'badge-tool' },
  'llama3.2:3b (tool path)':                    { label: 'Local + tools',        cls: 'badge-local'   },
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

function tryParseFlashcards(text) {
  try {
    const parsed = JSON.parse(text);
    if (
      parsed &&
      typeof parsed.tool_output_id === 'number' &&
      Array.isArray(parsed.cards) &&
      parsed.cards.length > 0 &&
      typeof parsed.cards[0].term === 'string'
    ) {
      return parsed.cards;
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

function TimingLabel({ total_ms, timings }) {
  if (!total_ms) return null;
  const secs = (total_ms / 1000).toFixed(1);
  const tools = timings?.tools;
  const toolBreakdown = tools && Object.keys(tools).length > 0
    ? Object.entries(tools).map(([name, ms]) => `${name}: ${(ms / 1000).toFixed(1)}s`).join(' · ')
    : null;
  return (
    <div className="timing-row">
      <span className="timing-total">&#9201; {secs}s</span>
      {toolBreakdown && <span className="timing-tools">{toolBreakdown}</span>}
    </div>
  );
}

function Message({ message, sessionId }) {
  const { text, sender, model, total_ms, timings } = message;
  const badge = sender === 'assistant' && model ? BADGE_MAP[model] : null;
  const quizInfo = sender === 'assistant' ? tryParseQuiz(text) : null;
  const flashcards = !quizInfo && sender === 'assistant' ? tryParseFlashcards(text) : null;
  const scheduleEntries = !quizInfo && !flashcards && sender === 'assistant' ? tryParseSchedule(text) : null;

  return (
    <div className={`message ${sender}`}>
      <div className="message-wrapper">
        {badge && (
          <span className={`model-badge ${badge.cls}`}>{badge.label}</span>
        )}
        {sender === 'assistant' && <TimingLabel total_ms={total_ms} timings={timings} />}
        {quizInfo?.type === 'quiz'
          ? <QuizDisplay quiz={quizInfo.data} sessionId={sessionId} />
          : quizInfo?.type === 'completed'
          ? <QuizDisplay completed={quizInfo.data} />
          : flashcards
          ? <FlashcardDisplay cards={flashcards} />
          : scheduleEntries
          ? <ScheduleInline entries={scheduleEntries} />
          : sender === 'assistant'
          ? <div className="message-content md-body"><MarkdownText text={text} /></div>
          : <div className="message-content">{text}</div>
        }
      </div>
    </div>
  );
}

export default Message;
