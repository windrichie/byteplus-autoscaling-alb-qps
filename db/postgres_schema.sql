-- PostgreSQL schema for ALB/ASG autoscaling with "resource groups" naming
-- This DDL defines: resource_groups, resource_group_state, scaling_activities, errors, locks, and optional function_runs
-- Safe to run multiple times with IF NOT EXISTS where applicable (Postgres has limited IF NOT EXISTS for constraints)

BEGIN;

-- 1) Resource groups: single ALB mapped to single ASG (one row per group)
CREATE TABLE IF NOT EXISTS resource_groups (
    id BIGSERIAL PRIMARY KEY,
    -- Core identifiers
    alb_id TEXT NOT NULL,
    asg_id TEXT NOT NULL,
    region TEXT NOT NULL,

    -- Scaling policy
    target_qps NUMERIC(18,6) NOT NULL,
    scale_up_cooldown_seconds INTEGER NOT NULL DEFAULT 300,
    scale_down_cooldown_seconds INTEGER NOT NULL DEFAULT 600,
    general_cooldown_seconds INTEGER NOT NULL DEFAULT 180,

    -- Operability
    dry_run BOOLEAN NOT NULL DEFAULT TRUE,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    last_validated_at TIMESTAMPTZ NULL,

    -- Auditing
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Uniqueness guard to prevent duplicate group definitions in same region
    CONSTRAINT uq_group_resource UNIQUE (alb_id, asg_id, region),
    -- Safety checks
    CONSTRAINT ck_target_qps CHECK (target_qps > 0)
);

-- Helpful indexes
CREATE INDEX IF NOT EXISTS idx_resource_groups_enabled ON resource_groups (enabled);
CREATE INDEX IF NOT EXISTS idx_resource_groups_region ON resource_groups (region);

-- Trigger to auto-update updated_at (optional; can also be done application-side)
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_resource_groups_updated_at ON resource_groups;
CREATE TRIGGER trg_resource_groups_updated_at
BEFORE UPDATE ON resource_groups
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 2) Per-resource-group state: cooldowns, recent metrics, health, and circuit breaker
CREATE TABLE IF NOT EXISTS resource_group_state (
    resource_group_id BIGINT PRIMARY KEY REFERENCES resource_groups(id) ON DELETE CASCADE,

    last_evaluated_at TIMESTAMPTZ NULL,
    cooldown_until TIMESTAMPTZ NULL,

    -- Error/circuit breaker book-keeping
    consecutive_errors INTEGER NOT NULL DEFAULT 0,
    circuit_open_until TIMESTAMPTZ NULL,
    suspended BOOLEAN NOT NULL DEFAULT FALSE,

    -- Latest observed metrics (for debugging/ops)
    latest_qps NUMERIC(18,6) NULL,
    latest_capacity INTEGER NULL,

    -- Auditing
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_state_cooldown_until ON resource_group_state (cooldown_until);
CREATE INDEX IF NOT EXISTS idx_state_circuit_open_until ON resource_group_state (circuit_open_until);

DROP TRIGGER IF EXISTS trg_resource_group_state_updated_at ON resource_group_state;
CREATE TRIGGER trg_resource_group_state_updated_at
BEFORE UPDATE ON resource_group_state
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 3) Scaling activities: idempotent record of all scaling decisions and outcomes
CREATE TABLE IF NOT EXISTS scaling_activities (
    id BIGSERIAL PRIMARY KEY,
    resource_group_id BIGINT NOT NULL REFERENCES resource_groups(id) ON DELETE CASCADE,

    -- Idempotency key: constructed by the application (e.g., group_id + desired_capacity + time bucket)
    activity_key TEXT NOT NULL,

    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    action TEXT NOT NULL CHECK (action IN ('scale_up', 'scale_down', 'no_op', 'scale_out', 'scale_in')),
    delta INTEGER NOT NULL DEFAULT 0,
    desired_capacity INTEGER NULL,

    -- Execution status progression
    status TEXT NOT NULL CHECK (status IN ('sent', 'accepted', 'in_progress', 'successful', 'failed', 'skipped', 'success', 'dry_run')),
    response JSONB NULL,
    error_message TEXT NULL,

    -- Observability
    eval_qps NUMERIC(18,6) NULL,
    eval_capacity INTEGER NULL,
    target_qps NUMERIC(18,6) NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_activity_key UNIQUE (activity_key)
);

CREATE INDEX IF NOT EXISTS idx_scaling_activities_group ON scaling_activities (resource_group_id);
CREATE INDEX IF NOT EXISTS idx_scaling_activities_created ON scaling_activities (created_at DESC);

-- 4) Errors: central error log for troubleshooting; can include non-group-specific errors
CREATE TABLE IF NOT EXISTS errors (
    id BIGSERIAL PRIMARY KEY,
    resource_group_id BIGINT NULL REFERENCES resource_groups(id) ON DELETE CASCADE,
    source TEXT NOT NULL, -- e.g., 'cloudmonitor', 'autoscaling', 'db', 'engine'
    message TEXT NOT NULL,
    context JSONB NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_errors_group ON errors (resource_group_id);
CREATE INDEX IF NOT EXISTS idx_errors_occurred ON errors (occurred_at DESC);

-- 5) Locks: optional table-backed distributed lock
-- If you choose not to use Postgres advisory locks, this provides a lease-based lock per resource group.
-- CREATE TABLE IF NOT EXISTS locks (
--     resource_group_id BIGINT PRIMARY KEY REFERENCES resource_groups(id) ON DELETE CASCADE,
--     owner TEXT NOT NULL,                -- e.g., function instance or host identifier
--     acquired_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
--     expires_at TIMESTAMPTZ NOT NULL,    -- set short lease (e.g., NOW() + INTERVAL '30 seconds')
--     metadata JSONB NULL
-- );

-- CREATE INDEX IF NOT EXISTS idx_locks_expires ON locks (expires_at);

-- Recommended lock acquisition pattern (application-side):
--   INSERT INTO locks(resource_group_id, owner, expires_at)
--   VALUES($1, $2, NOW() + INTERVAL '30 seconds')
--   ON CONFLICT (resource_group_id) DO NOTHING;        -- success iff row inserted
--   To renew: UPDATE locks SET expires_at = NOW() + INTERVAL '30 seconds' WHERE resource_group_id = $1 AND owner = $2;
--   To release: DELETE FROM locks WHERE resource_group_id = $1 AND owner = $2;
--   Cleanup: DELETE FROM locks WHERE expires_at < NOW();

-- 6) Optional: function run bookkeeping for observability
-- CREATE TABLE IF NOT EXISTS function_runs (
--     id BIGSERIAL PRIMARY KEY,
--     function_name TEXT NOT NULL, -- e.g., 'faas_runner_A' | 'faas_runner_B'
--     started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
--     completed_at TIMESTAMPTZ NULL,
--     evaluated_groups INTEGER NOT NULL DEFAULT 0,
--     errors_count INTEGER NOT NULL DEFAULT 0,
--     notes TEXT NULL
-- );

COMMIT;

-- Advisory locks alternative (no table) — for documentation:
-- Acquire: SELECT pg_try_advisory_lock(1263, resource_group_id);  -- 1263 is a chosen namespace, change as needed
-- Release: SELECT pg_advisory_unlock(1263, resource_group_id);
-- Check held: SELECT pg_advisory_lock_shared(1263, resource_group_id); -- or pg_advisory_lock for blocking
-- Recommended: Use pg_try_advisory_lock in application with timeout/backoff to avoid contention.