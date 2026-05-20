import { useState, useEffect, useRef } from 'react';
import './FileUpload.css';

function FileUpload({ sessionId, onAction, onSourceChange }) {
  const [files, setFiles] = useState([]);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState('');
  const [activeSourceIds, setActiveSourceIds] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(`doc_source_${sessionId}`) || '[]');
    } catch { return []; }
  });
  const inputRef = useRef(null);

  useEffect(() => {
    fetch(`http://localhost:8000/api/files?session_id=${sessionId}`)
      .then(r => r.json())
      .then(d => { if (d.files) setFiles(d.files); })
      .catch(() => {});
  }, [sessionId]);

  const toggleSource = (id_file) => {
    setActiveSourceIds(prev => {
      const next = prev.includes(id_file)
        ? prev.filter(id => id !== id_file)
        : [...prev, id_file];
      localStorage.setItem(`doc_source_${sessionId}`, JSON.stringify(next));
      onSourceChange?.(next);
      return next;
    });
  };

  const handleFileChange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setError('');
    setIsUploading(true);

    const formData = new FormData();
    formData.append('session_id', sessionId);
    formData.append('file', file);

    try {
      const res = await fetch('http://localhost:8000/api/upload', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      if (data.ok) {
        const newFile = {
          id_file: data.id_file,
          filename: data.filename,
          char_count: data.char_count,
          uploaded_at: new Date().toISOString(),
        };
        setFiles(prev => [newFile, ...prev]);
      } else {
        setError(data.error || 'Upload failed.');
      }
    } catch {
      setError('Could not reach the server.');
    } finally {
      setIsUploading(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  return (
    <div className="file-upload-panel">
      <section className="file-section">
        <h2>Documents</h2>
        <label className={`upload-btn ${isUploading ? 'uploading' : ''}`}>
          {isUploading ? 'Uploading...' : '+ Upload PDF'}
          <input
            ref={inputRef}
            type="file"
            accept=".pdf"
            onChange={handleFileChange}
            disabled={isUploading}
            hidden
          />
        </label>
        {error && <p className="upload-error">{error}</p>}
      </section>

      {files.length > 0 ? (
        <div className="file-list">
          {files.map((f, idx) => {
            const isSource = activeSourceIds.includes(f.id_file);
            const hasText = f.char_count > 0;
            return (
              <section key={f.id_file ?? idx} className="file-card">
                {idx === 0 && <span className="file-active-badge">Active</span>}
                <div className="file-name">&#128196; {f.filename}</div>
                <div className="file-meta">{f.char_count.toLocaleString()} characters extracted</div>
                <div className="file-actions">
                  <button
                    className="action-btn btn-summarize"
                    onClick={() => onAction(`Summarize the document '${f.filename}'`, { forceTool: 'summarize_document' })}
                  >
                    Summarize
                  </button>
                  <button
                    className="action-btn btn-quiz"
                    onClick={() => onAction(
                      activeSourceIds.length > 0
                        ? 'Generate a quiz from selected sources'
                        : `Generate a quiz from the document '${f.filename}'`,
                      { forceTool: 'generate_quiz' }
                    )}
                  >
                    Generate Quiz
                  </button>
                  {hasText && (
                    <button
                      className={`action-btn btn-source ${isSource ? 'source-on' : 'source-off'}`}
                      onClick={() => toggleSource(f.id_file)}
                      title={isSource ? 'Click to deactivate as source' : 'Click to use as local model source'}
                    >
                      {isSource ? 'Source ON' : 'Source OFF'}
                    </button>
                  )}
                </div>
              </section>
            );
          })}
        </div>
      ) : (
        !isUploading && (
          <div className="upload-placeholder">
            <p>Upload a PDF to enable document tools</p>
            <p className="placeholder-sub">Summarize &middot; Generate Quiz &middot; Source Mode</p>
          </div>
        )
      )}
    </div>
  );
}

export default FileUpload;
