import './Message.css';

function Message({ message }) {
  const { text, sender } = message;

  return (
    <div className={`message ${sender}`}>
      <div className="message-content">
        {text}
      </div>
    </div>
  );
}

export default Message;