import { useState } from 'react';
import './UsernamePrompt.css';

function UsernamePrompt({ onLogin }) {
  const [username, setUsername] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    const trimmed = username.trim();
    if (!trimmed) return;
    setIsLoading(true);
    setError('');
    try {
      const res = await fetch("http://localhost:8000/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: trimmed }),
      });
      if (!res.ok) throw new Error("Server error");
      const data = await res.json();
      onLogin(data);
    } catch {
      setError("Could not connect to the server. Please try again.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="username-prompt">
      <div className="username-card">
        <h1>CAHVA</h1>
        <p>Enter your username to continue</p>
        <form onSubmit={handleSubmit}>
          <input
            type="text"
            value={username}
            onChange={e => setUsername(e.target.value)}
            placeholder="Username"
            autoFocus
            disabled={isLoading}
          />
          <button type="submit" disabled={!username.trim() || isLoading}>
            {isLoading ? 'Connecting...' : 'Continue'}
          </button>
        </form>
        {error && <p className="prompt-error">{error}</p>}
      </div>
    </div>
  );
}

export default UsernamePrompt;
