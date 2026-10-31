CREATE TABLE IF NOT EXISTS parties (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    business_id     BIGINT      NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    name            TEXT        NOT NULL,
    normalized_name TEXT        NOT NULL,   -- lowercased/trimmed by app code
    phone           TEXT,                   -- E.164, optional
    type            TEXT        NOT NULL DEFAULT 'customer'
                    CHECK (type IN ('customer', 'supplier', 'both')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (phone IS NULL OR phone ~ '^\+[1-9][0-9]{6,14}$'),
    -- lets child tables use a composite FK that forces same-business references
    UNIQUE (id, business_id)
);

-- Two "Ramesh" rows in one business are legal, so normalized_name is NOT unique.
CREATE INDEX IF NOT EXISTS parties_business_name_idx ON parties (business_id, normalized_name);
CREATE INDEX IF NOT EXISTS parties_name_trgm_idx ON parties USING gin (normalized_name gin_trgm_ops);
