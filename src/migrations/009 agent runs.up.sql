CREATE TABLE agent_runs (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message_id     BIGINT      NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    step_no        INTEGER     NOT NULL,
    operation      TEXT        NOT NULL,   -- e.g. find_party, get_outstanding, plan
    params         JSONB,
    result_summary JSONB,                  -- small summary, never full tables
    error          TEXT,
    duration_ms    INTEGER,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (message_id, step_no)
);
