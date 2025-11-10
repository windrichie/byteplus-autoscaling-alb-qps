# Codebase Analysis: ALB QPS-based AutoScaling Solution

This document summarizes the current architecture and implementation.

## 1. Project Overview

A FaaS-based solution that automatically scales BytePlus AutoScaling Groups using ALB QPS metrics. The function now evaluates all enabled resource groups stored in Postgres, each mapping a single ALB to a single ASG in a specific region.

## 2. Architecture

Components:
- FaaS Function (index.py): main entrypoint; batch evaluation across resource groups.
- CloudMonitor (cloudmonitor_client.py): retrieves ALB QPS metrics.
- AutoScaling (autoscaling_client.py): reads ASG limits/status and performs scale_out/scale_in.
- Postgres (db_manager.py): configuration source (resource_groups), state (resource_group_state), activities (scaling_activities), and central error log (errors).

## 3. Key Files
- index.py: FaaS handler; initializes config, DB, clients; runs batch evaluation; includes alerting.
- config.py: ScalingConfig from environment; copy_with_group overlays per-group DB values (target_qps, cooldowns, dry_run, region) and carries resource_group_id.
- scaling_engine.py: core decision logic; dynamic mode (default) computes optimal instances and applies ASG limits; static mode uses thresholds; records scaling activities and errors.
- state_manager.py: writes cooldown_until and last_evaluated_at to resource_group_state; increments consecutive_errors on failures.
- db_manager.py: implements CRUD aligned with Postgres schema; ensures only valid columns are written; returns enabled resource groups.
- db/postgres_schema.sql: defines tables resource_groups, resource_group_state, scaling_activities, errors and their constraints/indexes.

## 4. Core Logic Flow
1. Load env config, then fetch enabled resource_groups from DB.
2. For each group, copy base config with group overrides.
3. Fetch ALB QPS and ASG status; compute qps_per_instance.
4. Decide action:
   - Dynamic: optimal_instances = ceil(QPS / target_qps_per_instance); apply ASG min/max; enforce per-action safety caps.
   - Static: compare QPS/instance to thresholds; scale by 1.
5. Check cooldowns: scale_up_cooldown_seconds, scale_down_cooldown_seconds, and general cooldown per group.
6. Execute action (unless dry_run), record activity with activity_key = resource_group_id + desired_capacity + time bucket.
7. Update state and errors in DB.

## 5. Configuration Model
- Environment: ACCESS_KEY_ID, SECRET_ACCESS_KEY, REGION (default), DB_DSN, METRIC_PERIOD, LOG_LEVEL, ENABLE_DETAILED_LOGGING, INITIAL_DELAY_SECONDS, ALERT_WEBHOOK_URL.
- Per-group DB: alb_id, asg_id, region, target_qps, scale_up_cooldown_seconds, scale_down_cooldown_seconds, general_cooldown_seconds, dry_run, enabled.
- Thresholds are only relevant in static mode.
- Scale-to-zero allowed; ASG min/max are the source of truth for bounds.

## 6. Limitations and Notes
- Assumes 1:1 mapping at group level (one ALB to one ASG per row). Batch evaluation across multiple rows is supported.
- CloudMonitor or AutoScaling API rate limits may affect responsiveness with very low METRIC_PERIOD values.
- Ensure DB connectivity and proper indexes on resource_groups and scaling_activities for performance.