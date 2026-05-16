import { useState, useRef, useEffect } from 'react';
import Message from './Message';
import ChatInput from './ChatInput';
import './Chat.css';

function Chat({ sessionId, username, onBack }) {
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    fetch(`http://localhost:8000/api/history?session_id=${sessionId}`)
      .then(res => res.json())
      .then(data => {
        setMessages(data.messages.map(m => ({
          id: m.id_message,
          text: m.content,
          sender: m.role,
        })));
      })
      .catch(() => {});
  }, [sessionId]);

  const handleSendMessage = async (text) => {
    const userMessage = { id: Date.now(), text, sender: 'user' };
    setMessages(prev => [...prev, userMessage]);
    setIsLoading(true);

    try {
      const response = await fetch("http://localhost:8000/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message: text }),
      });

      if (!response.ok) throw new Error("Network response was not ok");

      const data = await response.json();
      const assistantMessage = { id: Date.now() + 1, text: data.reply, sender: 'assistant' };
      setMessages(prev => [...prev, assistantMessage]);
    } catch (error) {
      const errorMessage = {
        id: Date.now() + 1,
        text: "Sorry, I'm having trouble connecting to the server.",
        sender: 'assistant',
      };
      setMessages(prev => [...prev, errorMessage]);
      console.error("Error:", error);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="chat-container">
      <div className="chat-header">
        <button className="back-btn" onClick={onBack} title="Back to sessions">&#8592;</button>
        <h1>Chat Assistant</h1>
        <span className="chat-username">{username}</span>
      </div>
      <div className="messages-container">
        {messages.map(message => (
          <Message key={message.id} message={message} />
        ))}
        {isLoading && <div className="message assistant">Thinking...</div>}
        <div ref={messagesEndRef} />
      </div>
      <ChatInput onSendMessage={handleSendMessage} />
    </div>
  );
}

export default Chat;
