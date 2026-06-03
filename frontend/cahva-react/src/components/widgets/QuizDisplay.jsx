import { useState } from 'react';
import './QuizDisplay.css';

const OPTIONS = ['A', 'B', 'C', 'D'];

const DRAFT_KEY = (sid, id) => `quiz_draft_${sid}_${id}`;
const DONE_KEY  = (sid)     => `quiz_done_${sid}`;

function markQuizDone(sid, id) {
  if (!sid || !id) return;
  try {
    const ids = JSON.parse(localStorage.getItem(DONE_KEY(sid)) || '[]');
    if (!ids.includes(id))
      localStorage.setItem(DONE_KEY(sid), JSON.stringify([...ids, id]));
  } catch {}
}

function wasQuizDone(sid, id) {
  if (!sid || !id) return false;
  try {
    return JSON.parse(localStorage.getItem(DONE_KEY(sid)) || '[]').includes(id);
  } catch { return false; }
}

// ─────────────────────────────────────────────────────────────────────────────

function QuizDisplay({ quiz, completed, sessionId, onNewQuiz }) {
  // 'quiz' → show questions, 'done' → show minimal completed line
  const [phase, setPhase] = useState(() => {
    if (completed) return 'done';
    if (wasQuizDone(sessionId, quiz?.tool_output_id)) return 'done';
    return 'quiz';
  });

  const [currentQuiz, setCurrentQuiz]         = useState(quiz ?? null);
  const [displayQuestions, setDisplayQuestions] = useState(() =>
    quiz ? [...quiz.questions] : []
  );
  const [answers, setAnswers] = useState(() => {
    if (!quiz?.tool_output_id) return {};
    try {
      const saved = localStorage.getItem(DRAFT_KEY(sessionId, quiz.tool_output_id));
      return saved ? JSON.parse(saved) : {};
    } catch { return {}; }
  });
  const [results,       setResults]       = useState(null);
  const [isSubmitting,  setIsSubmitting]  = useState(false);
  const [isRegenerating,setIsRegenerating]= useState(false);
  const [error,         setError]         = useState('');

  const shuffle = (arr) => [...arr].sort(() => Math.random() - 0.5);

  // Score data for the done line.
  // `reward` is only included when the user just submitted in this session
  // (`results` is set). On subsequent views the +N tokens message is suppressed.
  const doneScore = results
    ? { score: results.score,             total: results.total,             reward: results.budget_reward }
    : completed
    ? { score: completed.score,           total: completed.total_questions, reward: null }
    : null;

  // ── Regenerate ─────────────────────────────────────────────────────────────
  const handleRegenerate = async () => {
    setIsRegenerating(true);
    setError('');
    try {
      const res = await fetch('http://localhost:8000/api/quiz/regenerate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId }),
      });
      const data = await res.json();
      if (data.error) {
        setError(data.error);
      } else {
        localStorage.removeItem(DRAFT_KEY(sessionId, currentQuiz?.tool_output_id));
        setCurrentQuiz(data);
        setDisplayQuestions([...data.questions]);
        setAnswers({});
        setResults(null);
        setPhase('quiz');
        setError('');
      }
    } catch {
      setError('Could not regenerate the quiz.');
    } finally {
      setIsRegenerating(false);
    }
  };

  // ── Minimal "done" line ────────────────────────────────────────────────────
  if (phase === 'done') {
    return (
      <div className="quiz-done-inline">
        <span className="quiz-done-check">&#10003;</span>
        <span className="quiz-done-text">
          Quiz completed
          {doneScore && ` — ${doneScore.score}/${doneScore.total}`}
          {doneScore?.reward != null && ` · +${doneScore.reward} tokens`}
        </span>
        {onNewQuiz && (
          <button className="quiz-new-btn" onClick={onNewQuiz}>
            New Quiz
          </button>
        )}
      </div>
    );
  }

  // ── Active quiz ────────────────────────────────────────────────────────────
  const allAnswered = displayQuestions.every(q => answers[String(q.index)] !== undefined);

  const handleSelect = (index, option) => {
    if (results) return;
    setAnswers(prev => {
      const next = { ...prev, [String(index)]: option };
      if (currentQuiz?.tool_output_id)
        localStorage.setItem(DRAFT_KEY(sessionId, currentQuiz.tool_output_id), JSON.stringify(next));
      return next;
    });
  };

  const handleSubmit = async () => {
    setIsSubmitting(true);
    setError('');
    try {
      const res = await fetch('http://localhost:8000/api/quiz/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id:     sessionId,
          tool_output_id: currentQuiz.tool_output_id,
          answers,
        }),
      });
      const data = await res.json();
      if (data.error) {
        setError(data.error);
      } else {
        localStorage.removeItem(DRAFT_KEY(sessionId, currentQuiz.tool_output_id));
        setResults(data);
        if (data.score === data.total) {
          markQuizDone(sessionId, currentQuiz.tool_output_id);
          setPhase('done');
        }
      }
    } catch {
      setError('Could not reach the server.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const isPerfect = results && results.score === results.total;

  return (
    <div className="quiz-display">
      <div className="quiz-header">
        <span className="quiz-title">Quiz</span>
        {results && (
          <span className={`quiz-score${isPerfect ? '' : ' quiz-score-partial'}`}>
            {results.score}/{results.total} &nbsp;&middot;&nbsp; +{results.budget_reward} tokens
          </span>
        )}
      </div>

      <div className="quiz-questions">
        {displayQuestions.map((q, displayPos) => {
          const chosen  = answers[String(q.index)];
          const qResult = results?.results?.find(r => r.index === q.index);

          return (
            <div key={q.index} className="quiz-question">
              <p className="question-text">
                <span className="question-num">{displayPos + 1}.</span> {q.question}
              </p>
              <div className="quiz-options">
                {OPTIONS.map(opt => {
                  let cls = 'quiz-option';
                  if (qResult) {
                    if (opt === qResult.correct_answer)          cls += ' opt-correct';
                    else if (opt === chosen && !qResult.is_correct) cls += ' opt-wrong';
                    else                                          cls += ' opt-neutral';
                  } else if (opt === chosen) {
                    cls += ' opt-selected';
                  }
                  return (
                    <button
                      key={opt}
                      className={cls}
                      onClick={() => handleSelect(q.index, opt)}
                      disabled={!!results}
                    >
                      <span className="opt-letter">{opt}</span>
                      <span className="opt-text">{q.options[opt]}</span>
                    </button>
                  );
                })}
              </div>
              {qResult && !qResult.is_correct && qResult.explanation && (
                <div className="quiz-explanation">
                  <span className="explanation-label">Explanation:</span>{' '}
                  {qResult.explanation}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {error && <p className="quiz-error">{error}</p>}

      {!results && (
        <button
          className="quiz-submit-btn"
          onClick={handleSubmit}
          disabled={!allAnswered || isSubmitting}
        >
          {isSubmitting
            ? 'Submitting…'
            : `Submit (${Object.keys(answers).length}/${displayQuestions.length} answered)`}
        </button>
      )}

      {results && !isPerfect && (
        <div className="quiz-results-footer">
          <p className="quiz-reward-msg">
            {results.score} / {results.total} correct — review the explanations above.
          </p>
          <div className="quiz-action-row">
            <button
              className="quiz-retry-btn"
              onClick={() => {
                localStorage.removeItem(DRAFT_KEY(sessionId, currentQuiz?.tool_output_id));
                setDisplayQuestions(shuffle(currentQuiz.questions));
                setAnswers({});
                setResults(null);
                setError('');
              }}
            >
              Try Again
            </button>
            <button
              className="quiz-regen-btn"
              onClick={handleRegenerate}
              disabled={isRegenerating}
            >
              {isRegenerating ? 'Generating…' : 'Try New Questions'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default QuizDisplay;
