import { useState, useRef, useEffect } from 'react';
import Message from './Message';
import ChatInput from './ChatInput';
import FileUpload from './FileUpload';
import Stats from './Stats';
import SchedulePanel from './SchedulePanel';
import './Chat.css';

function Chat({ sessionId, username, onBack }) {
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('chat');
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

  const handleSendMessage = async (text, options = {}) => {
    const userMessage = { id: Date.now(), text, sender: 'user' };
    setMessages(prev => [...prev, userMessage]);
    setIsLoading(true);

    try {
      const response = await fetch("http://localhost:8000/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          message: text,
          force_tool: options.forceTool ?? '',
        }),
      });

      if (!response.ok) throw new Error("Network response was not ok");

      const data = await response.json();
      const assistantMessage = {
        id: Date.now() + 1,
        text: data.reply,
        sender: 'assistant',
        model: data.routing_decision,
      };
      setMessages(prev => [...prev, assistantMessage]);
    } catch (error) {
      setMessages(prev => [...prev, {
        id: Date.now() + 1,
        text: "Sorry, I'm having trouble connecting to the server.",
        sender: 'assistant',
      }]);
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

      <div className="chat-tabs">
        <button
          className={`chat-tab ${activeTab === 'chat' ? 'active' : ''}`}
          onClick={() => setActiveTab('chat')}
        >Chat</button>
        <button
          className={`chat-tab ${activeTab === 'files' ? 'active' : ''}`}
          onClick={() => setActiveTab('files')}
        >Files</button>
        <button
          className={`chat-tab ${activeTab === 'schedule' ? 'active' : ''}`}
          onClick={() => setActiveTab('schedule')}
        >Schedule</button>
        <button
          className={`chat-tab ${activeTab === 'stats' ? 'active' : ''}`}
          onClick={() => setActiveTab('stats')}
        >Stats</button>
      </div>

      {activeTab === 'chat' && (
        <>
          <div className="messages-container">
            {messages.map(message => (
              <Message key={message.id} message={message} sessionId={sessionId} />
            ))}
            {isLoading && <div className="message assistant">Thinking...</div>}
            <div ref={messagesEndRef} />
          </div>
          <ChatInput onSendMessage={handleSendMessage} />
        </>
      )}
      {activeTab === 'files' && (
        <FileUpload
          sessionId={sessionId}
          onAction={(msg, opts) => {
            setActiveTab('chat');
            handleSendMessage(msg, opts);
          }}
        />
      )}
      {activeTab === 'schedule' && <SchedulePanel sessionId={sessionId} />}
      {activeTab === 'stats' && <Stats sessionId={sessionId} />}
    </div>
  );
}

export default Chat;
