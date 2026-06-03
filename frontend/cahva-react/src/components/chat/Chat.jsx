import { useState, useRef, useEffect } from 'react';
import Message from './Message';
import ChatInput from './ChatInput';
import FileUpload from './FileUpload';
import Stats from '../widgets/Stats';
import SchedulePanel from '../schedule/SchedulePanel';
import chatRequests from '../../chatRequests';
import './Chat.css';

function Chat({ sessionId, username, onBack }) {
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('chat');
  const [sourceFileIds, setSourceFileIds] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(`doc_source_${sessionId}`) || '[]');
    } catch { return []; }
  });
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

  // On mount: if a request is still in-flight for this session (user switched away
  // and came back), re-attach to the promise so loading state and response are
  // shown correctly.
  useEffect(() => {
    const pending = chatRequests.get(sessionId);
    if (!pending) return;

    setIsLoading(true);

    pending
      .then(() => {
        // Re-fetch history — the backend task has now saved the assistant reply.
        return fetch(`http://localhost:8000/api/history?session_id=${sessionId}`)
          .then(r => r.json())
          .then(d => setMessages(d.messages.map(m => ({
            id: m.id_message,
            text: m.content,
            sender: m.role,
          }))));
      })
      .catch(() => {
        setMessages(prev => [...prev, {
          id: Date.now(),
          text: "Sorry, I'm having trouble connecting to the server.",
          sender: 'assistant',
        }]);
      })
      .finally(() => {
        chatRequests.clear(sessionId);
        setIsLoading(false);
      });
  }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSendMessage = async (text, options = {}) => {
    const userMessage = { id: Date.now(), text, sender: 'user' };
    setMessages(prev => [...prev, userMessage]);
    setIsLoading(true);

    // Build and store the promise at module level so it survives component unmount.
    const fetchPromise = fetch("http://localhost:8000/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        message: text,
        force_tool: options.forceTool ?? '',
        source_file_ids: options.sourceFileIds ?? sourceFileIds,
      }),
    }).then(r => {
      if (!r.ok) throw new Error("Network response was not ok");
      return r.json();
    });

    chatRequests.set(sessionId, fetchPromise);

    try {
      const data = await fetchPromise;
      setMessages(prev => [...prev, {
        id: Date.now() + 1,
        text: data.reply,
        sender: 'assistant',
        model: data.routing_decision,
        total_ms: data.total_ms,
        timings: data.timings,
      }]);
    } catch (error) {
      setMessages(prev => [...prev, {
        id: Date.now() + 1,
        text: "Sorry, I'm having trouble connecting to the server.",
        sender: 'assistant',
      }]);
      console.error("Error:", error);
    } finally {
      chatRequests.clear(sessionId);
      setIsLoading(false);
    }
  };

  return (
    <div className="chat-container">
      <div className="chat-header">
        <button className="back-btn" onClick={onBack} title="Back to sessions">&#8592;</button>
        <h1>Chat Assistant</h1>
        {sourceFileIds.length > 0 && (
          <span className="source-mode-badge">&#128196; Source mode</span>
        )}
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
              <Message
                key={message.id}
                message={message}
                sessionId={sessionId}
                onNewQuiz={() => handleSendMessage('Generate a new quiz', { forceTool: 'generate_quiz' })}
              />
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
          onSourceChange={setSourceFileIds}
        />
      )}
      {activeTab === 'schedule' && <SchedulePanel sessionId={sessionId} />}
      {activeTab === 'stats' && <Stats sessionId={sessionId} username={username} />}
    </div>
  );
}

export default Chat;
