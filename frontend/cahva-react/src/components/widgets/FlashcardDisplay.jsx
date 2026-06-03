import { useState, useEffect, useRef } from 'react';
import './FlashcardDisplay.css';

function FlashcardDisplay({ cards }) {
  const [current, setCurrent]       = useState(0);
  const [flipped, setFlipped]       = useState(false);
  const [sceneHeight, setSceneHeight] = useState(160);
  const frontRef = useRef(null);
  const backRef  = useRef(null);

  const total = cards.length;
  const card  = cards[current];

  // After each card change, measure both faces and size the scene to the tallest
  useEffect(() => {
    const fh = frontRef.current?.getBoundingClientRect().height ?? 160;
    const bh = backRef.current?.getBoundingClientRect().height  ?? 160;
    setSceneHeight(Math.max(fh, bh, 140));
  }, [card.term, card.definition]);

  const goTo = (idx) => {
    setCurrent(idx);
    setFlipped(false);
  };

  return (
    <div className="fc-wrapper">
      <div className="fc-progress">
        {current + 1} / {total}
      </div>

      <div
        className="fc-scene"
        style={{ height: sceneHeight }}
        onClick={() => setFlipped(f => !f)}
        title={flipped ? 'Click to see term' : 'Click to see definition'}
      >
        <div className={`fc-card${flipped ? ' is-flipped' : ''}`}>
          <div className="fc-face fc-front" ref={frontRef}>
            <span className="fc-face-label">Term</span>
            <p className="fc-face-text">{card.term}</p>
            {!flipped && <span className="fc-hint">click to flip</span>}
          </div>
          <div className="fc-face fc-back" ref={backRef}>
            <span className="fc-face-label">Definition</span>
            <p className="fc-face-text">{card.definition}</p>
          </div>
        </div>
      </div>

      <div className="fc-nav">
        <button
          className="fc-nav-btn"
          onClick={() => goTo(current - 1)}
          disabled={current === 0}
        >
          &#8592; Prev
        </button>
        <button
          className="fc-nav-btn"
          onClick={() => goTo(current + 1)}
          disabled={current === total - 1}
        >
          Next &#8594;
        </button>
      </div>
    </div>
  );
}

export default FlashcardDisplay;
