import { useState, useRef, useEffect } from 'react';
import Message from './Message';
import ChatInput from './ChatInput';
import './Chat.css';

function Chat() {
  const [messages, setMessages] = useState([
    { id: 1, text: 'Hello! How can I help you today?', sender: 'assistant' }
  ]);
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Updated function to be async
  const handleSendMessage = async (text) => {
    // 1. Add user message to UI immediately
    const userMessage = {
      id: Date.now(), // Better than length+1 for unique IDs
      text,
      sender: 'user'
    };
    setMessages(prev => [...prev, userMessage]);
    setIsLoading(true);

    try {
      // 2. Call your FastAPI Backend, IP address from tailscale, only works for those in the same network or tunnel
      const response = await fetch("http://100.121.7.58:8000/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ message: text }),
      });

      if (!response.ok) throw new Error("Network response was not ok");

      const data = await response.json();

      // 3. Add the real backend response to UI
      const assistantMessage = {
        id: Date.now() + 1,
        text: data.reply, // This comes from your FastAPI return {"reply": ...}
        sender: 'assistant'
      };
      
      setMessages(prev => [...prev, assistantMessage]);
    } catch (error) {
      // Handle errors (backend down, etc.)
      const errorMessage = {
        id: Date.now() + 1,
        text: "Sorry, I'm having trouble connecting to the server.",
        sender: 'assistant'
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
        <h1>Chat Assistant</h1>
      </div>
      <div className="messages-container">
        {messages.map(message => (
          <Message key={message.id} message={message} />
        ))}
        {/* Optional: Show a loading indicator */}
        {isLoading && <div className="message assistant">Thinking...</div>}
        <div ref={messagesEndRef} />
      </div>
      <ChatInput onSendMessage={handleSendMessage} />
    </div>
  );
}

export default Chat;