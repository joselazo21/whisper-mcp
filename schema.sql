CREATE TABLE IF NOT EXISTS transcription_jobs (
    id UUID PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'done', 'failed', 'cancelled')),
    audio_url TEXT,
    audio_path TEXT,
    language TEXT,
    text TEXT,
    segments JSONB,
    progress DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 1),
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    CHECK ((audio_url IS NULL) <> (audio_path IS NULL))
);

CREATE INDEX IF NOT EXISTS transcription_jobs_expired_lease
    ON transcription_jobs (lease_expires_at) WHERE status = 'processing';

CREATE TABLE IF NOT EXISTS job_outbox (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES transcription_jobs(id) ON DELETE CASCADE,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS job_outbox_ready ON job_outbox (available_at, id);
