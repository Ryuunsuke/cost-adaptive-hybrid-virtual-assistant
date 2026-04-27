import { useState } from 'react';
import './ChatInput.css';

function ChatInput({ onSendMessage }) {
  const [input, setInput] = useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    if (input.trim()) {
      onSendMessage(input.trim());
      setInput('');
    }
  };

  return (
    <form className="chat-input-container" onSubmit={handleSubmit}>
      <input
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        placeholder="Type your message..."
        className="chat-input"
      />
      <button type="submit" className="send-button" disabled={!input.trim()}>
        Send
      </button>
    </form>
  );
}

export default ChatInput;