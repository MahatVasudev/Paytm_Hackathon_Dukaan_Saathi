CREATE TABLE IF NOT EXISTS media (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message_id       BIGINT      NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    kind             TEXT        NOT NULL CHECK (kind IN ('audio', 'image', 'document')),
    mime_type        TEXT,
    size_bytes       BIGINT      CHECK (size_bytes IS NULL OR size_bytes >= 0),
    external_file_id TEXT,        -- Telegram file_id
    storage_path     TEXT,        -- local path / bucket key once downloaded
    sha256           TEXT,        -- catches the same file sent twice
    downloaded_at    TIMESTAMPTZ,
    deleted_at       TIMESTAMPTZ, -- retention: set when the file is purged
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS media_message_idx ON media (message_id);
CREATE INDEX IF NOT EXISTS media_sha256_idx  ON media (sha256) WHERE sha256 IS NOT NULL;
