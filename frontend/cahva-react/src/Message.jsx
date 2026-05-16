import './Message.css';

const BADGE_MAP = {
  'llama3.2:3b':                              { label: 'Local',              cls: 'badge-local'   },
  'GPT-4o mini':                              { label: 'GPT-4o mini',        cls: 'badge-mini'    },
  'GPT-4o':                                   { label: 'GPT-4o',             cls: 'badge-complex' },
  'GPT-4o mini (tool path)':                  { label: 'GPT-4o mini + tools',cls: 'badge-tool'    },
  'GPT-4o mini (tool path – synthesis blocked)': { label: 'GPT-4o mini + tools', cls: 'badge-tool' },
};

function Message({ message }) {
  const { text, sender, model } = message;
  const badge = sender === 'assistant' && model ? BADGE_MAP[model] : null;

  return (
    <div className={`message ${sender}`}>
      <div className="message-wrapper">
        {badge && (
          <span className={`model-badge ${badge.cls}`}>{badge.label}</span>
        )}
        <div className="message-content">{text}</div>
      </div>
    </div>
  );
}

export default Message;
