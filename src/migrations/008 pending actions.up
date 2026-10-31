CREATE TABLE pending_actions (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    business_id      BIGINT      NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    intent           TEXT        NOT NULL,                 -- e.g. record_payment
    collected_fields JSONB       NOT NULL DEFAULT '{}'::jsonb,
    awaiting         TEXT        NOT NULL,                 -- party_choice | missing_amount | confirmation ...
    status           TEXT        NOT NULL DEFAULT 'open'
                     CHECK (status IN ('open', 'completed', 'cancelled', 'expired')),
    expires_at       TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One open action per business chat: a new request replaces a stale one
-- instead of two actions competing for the next "yes" or "500".
CREATE UNIQUE INDEX pending_actions_one_open_uidx
    ON pending_actions (business_id) WHERE status = 'open';
