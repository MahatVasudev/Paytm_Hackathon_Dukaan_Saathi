CREATE TABLE IF NOT EXISTS messages (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    business_id           BIGINT      NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    direction             TEXT        NOT NULL CHECK (direction IN ('in', 'out')),
    type                  TEXT        NOT NULL
                          CHECK (type IN ('text', 'voice', 'button_tap', 'system')),
    text                  TEXT,                 -- what the user typed / what the bot sent
    transcript            TEXT,                 -- speech-to-text output for voice notes
    transcription_status  TEXT        NOT NULL DEFAULT 'not_needed'
                          CHECK (transcription_status IN ('not_needed', 'pending', 'done', 'failed')),
    language_detected     TEXT,
    external_message_id   TEXT,                 -- Telegram message_id
    update_id             BIGINT,               -- Telegram update_id (inbound only)
    party_id              BIGINT,               -- nullable; only for future party-directed messages
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at          TIMESTAMPTZ,
    UNIQUE (id, business_id),
    -- party must belong to the same business (not enforced while party_id is NULL)
    FOREIGN KEY (party_id, business_id) REFERENCES parties (id, business_id)
);

-- Idempotency: the same inbound/outbound message can't be logged twice per chat.
CREATE UNIQUE INDEX IF NOT EXISTS messages_external_id_uidx
    ON messages (business_id, direction, external_message_id)
    WHERE external_message_id IS NOT NULL;
-- Telegram resends the same update after timeouts; dedupe on it.
CREATE UNIQUE INDEX IF NOT EXISTS messages_update_id_uidx
    ON messages (update_id) WHERE update_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS messages_business_created_idx ON messages (business_id, created_at DESC);
