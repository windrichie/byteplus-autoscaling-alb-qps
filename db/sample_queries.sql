-- Populate resource groups
BEGIN;
INSERT INTO resource_groups (alb_id, asg_id, region, target_qps, scale_up_cooldown_seconds, scale_down_cooldown_seconds, general_cooldown_seconds, dry_run, enabled)
VALUES
('alb-301s53o7ru1hc72fuw7gjti5m', 'scg-ye3iy9dy6um55a3jqskk', 'ap-southeast-3', 1, 300, 600, 180, true, true),
('alb-300sckquu4rgg72fuw70ex4xa', 'scg-ye8lgeflyxhyaoky5ouj', 'ap-southeast-3', 1, 300, 600, 180, true, true);
COMMIT;