CREATE TABLE IF NOT EXISTS businesses (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name             TEXT        NOT NULL,
    channel          TEXT        NOT NULL DEFAULT 'telegram'
                     CHECK (channel IN ('telegram', 'whatsapp')),
    external_chat_id TEXT        NOT NULL,   -- Telegram chat_id / WhatsApp number
    language_pref    TEXT        NOT NULL DEFAULT 'en',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (channel, external_chat_id)
);
