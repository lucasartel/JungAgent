CREATE TABLE IF NOT EXISTS telegram_audio_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    telegram_message_id TEXT,
    telegram_file_id TEXT,
    audio_kind TEXT NOT NULL,
    mime_type TEXT,
    duration_seconds INTEGER,
    file_size_bytes INTEGER,
    transcription_model TEXT,
    status TEXT NOT NULL DEFAULT 'received',
    transcript TEXT,
    error_message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_telegram_audio_events_user_created
ON telegram_audio_events(user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_telegram_audio_events_status
ON telegram_audio_events(status);
