import { useState } from 'react';
import './QuizDisplay.css';

const OPTIONS = ['A', 'B', 'C', 'D'];

function QuizDisplay({ quiz, completed, sessionId }) {
  const [currentQuiz, setCurrentQuiz] = useState(quiz);
  const [displayQuestions, setDisplayQuestions] = useState(() => [...quiz.questions]);
  const [answers, setAnswers] = useState({});
  const [results, setResults] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isRegenerating, setIsRegenerating] = useState(false);
  const [error, setError] = useState('');

  const shuffle = (arr) => [...arr].sort(() => Math.random() - 0.5);

  if (completed) {
    return (
      <div className="quiz-display quiz-completed-card">
        <div className="quiz-header">
          <span className="quiz-title">Quiz Completed</span>
          <span className="quiz-score">{completed.score}/{completed.total_questions}</span>
        </div>
        <p className="quiz-reward-msg">
          Perfect score! +{completed.budget_reward} bonus tokens earned.
          <br />
          <span className="quiz-done-note">New quizzes will draw from different topics to keep things fresh.</span>
        </p>
      </div>
    );
  }

  const allAnswered = displayQuestions.every(
    q => answers[String(q.index)] !== undefined
  );

  const handleSelect = (index, option) => {
    if (results) return;
    setAnswers(prev => ({ ...prev, [String(index)]: option }));
  };

  const handleSubmit = async () => {
    setIsSubmitting(true);
    setError('');
    try {
      const res = await fetch('http://localhost:8000/api/quiz/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          tool_output_id: currentQuiz.tool_output_id,
          answers,
        }),
      });
      const data = await res.json();
      if (data.error) {
        setError(data.error);
      } else {
        setResults(data);
      }
    } catch {
      setError('Could not reach the server.');
    } finally {
      setIsSubmitting(false);
    }
  };

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
        setCurrentQuiz(data);
        setDisplayQuestions([...data.questions]);
        setAnswers({});
        setResults(null);
        setError('');
      }
    } catch {
      setError('Could not regenerate the quiz.');
    } finally {
      setIsRegenerating(false);
    }
  };

  const isPerfect = results && results.score === results.total;

  return (
    <div className="quiz-display">
      <div className="quiz-header">
        <span className="quiz-title">Quiz</span>
        {results && (
          <span className={`quiz-score${isPerfect ? '' : ' quiz-score-partial'}`}>
            {results.score}/{results.total} &nbsp;·&nbsp; +{results.budget_reward} tokens
          </span>
        )}
      </div>

      <div className="quiz-questions">
        {displayQuestions.map((q, displayPos) => {
          const chosen = answers[String(q.index)];
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
                    if (opt === qResult.correct_answer) cls += ' opt-correct';
                    else if (opt === chosen && !qResult.is_correct) cls += ' opt-wrong';
                    else cls += ' opt-neutral';
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
          {isSubmitting ? 'Submitting…' : `Submit (${Object.keys(answers).length}/${displayQuestions.length} answered)`}
        </button>
      )}

      {results && (
        <div className="quiz-results-footer">
          <p className="quiz-reward-msg">
            {isPerfect
              ? 'Perfect score! +500 bonus tokens earned.'
              : `${results.score} / ${results.total} correct — review the explanations above.`}
          </p>
          <div className="quiz-action-row">
            {isPerfect ? (
              <button
                className="quiz-regen-btn"
                onClick={handleRegenerate}
                disabled={isRegenerating}
              >
                {isRegenerating ? 'Generating…' : 'Try Again'}
              </button>
            ) : (
              <>
                <button
                  className="quiz-retry-btn"
                  onClick={() => {
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
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default QuizDisplay;
