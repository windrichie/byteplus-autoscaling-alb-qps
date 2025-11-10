# BytePlus ALB QPS-based Autoscaling (Multi-group, DB-backed)

This solution is a FaaS function that scales BytePlus AutoScaling Groups based on ALB QPS metrics. It evaluates all enabled resource groups stored in a PostgreSQL database and applies dynamic scaling (scale to meet target QPS).

## Key capabilities
- Batch evaluation across multiple resource groups (one ALB ↔ one ASG per group)
- Per-group policy sourced from Postgres (DB = source of truth)
- Dynamic scaling (default): compute optimal instances = ceil(QPS / target_qps_per_instance)
- ASG min/max (from cloud API) enforce capacity bounds
- Cooldowns: per-group scale_up, scale_down, and general cooldowns
- Activity recording with idempotency key (group_id + desired_capacity + time bucket)
- Rich result payloads include alb_id, asg_id per group for easy inspection

## Architecture overview
- FaaS handler (index.py): loads env, initializes clients, runs batch evaluation
- Scaling engine (scaling_engine.py): dynamic/static logic, cooldown checks, activity/error recording
- CloudMonitor client: gets ALB QPS
- AutoScaling client: reads ASG min/max, status, and performs scale actions
- Postgres via DBManager: reads resource_groups; writes resource_group_state, scaling_activities, errors

## Database schema (summary)
Tables defined in db/postgres_schema.sql:
- resource_groups(id, alb_id, asg_id, region, target_qps,
  scale_up_cooldown_seconds, scale_down_cooldown_seconds, general_cooldown_seconds,
  dry_run, enabled, timestamps)
- resource_group_state(resource_group_id, last_evaluated_at, cooldown_until,
  consecutive_errors, circuit_open_until, suspended, latest_qps, latest_capacity, timestamps)
- scaling_activities(resource_group_id, activity_key, requested_at, action,
  delta, desired_capacity, status, response, error_message,
  eval_qps, eval_capacity, target_qps, timestamps)
- errors(resource_group_id, source, message, context, occurred_at)

## Configuration
Environment variables (shared):
- REQUIRED: DB_DSN, ACCESS_KEY_ID, SECRET_ACCESS_KEY, REGION
- Optional: METRIC_PERIOD (default 300), LOG_LEVEL, ENABLE_DETAILED_LOGGING, INITIAL_DELAY_SECONDS, ALERT_WEBHOOK_URL
- Not required in multi‑group mode: AUTOSCALING_GROUP_ID, ALB_ID (sourced per group from DB)

Per-group values (from DB):
- alb_id, asg_id, region
- target_qps (per instance)
- scale_up_cooldown_seconds, scale_down_cooldown_seconds, general_cooldown_seconds
- dry_run, enabled

## Local testing
1) Set env in autoscaling-alb-solution/.env (DB_DSN, ACCESS_KEY_ID, SECRET_ACCESS_KEY, REGION)
2) Apply schema (db/postgres_schema.sql) and insert resource_groups rows
3) Run:
   - `set -a; source .env; set +a; python3 local_test.py`

Result payload example (per group):
- Includes: action, reason, current_qps, current_instances, qps_per_instance,
  target_qps_per_instance, dry_run, alb_id, asg_id, scaling_amount,
  optimal_instances, required_change, limited_by_safety, activity_key

## Deployment
- Package and deploy the function to your FaaS environment
- Configure the same environment variables (DB_DSN, ACCESS_KEY_ID, SECRET_ACCESS_KEY, REGION)
- Ensure DB is reachable and resource_groups are populated with enabled=true
- Set per-group dry_run=true to verify decisions without executing; flip to false for real scaling

## Behavior clarifications
- Scale‑to‑zero is permitted; if you want to guard against zero, set ASG min to 1
- ASG min/max are enforced from cloud API at runtime
- General cooldown, scale_up, and scale_down cooldowns are applied per group
- Activities are recorded with status 'dry_run' unless real scaling is executed