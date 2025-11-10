import json
import os
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
from pathlib import Path
from db_manager import DBManager


class StateManager:
    """
    Manages the state of scaling groups, including cooldown periods and error counts.
    """

    def __init__(self, db_manager: DBManager):
        self.db_manager = db_manager
        self.logger = logging.getLogger(__name__)

    def record_scaling_activity(self, group_id: int, activity_key: str, action: str, status: str,
                                eval_qps: float, eval_capacity: int, target_qps: float, response: dict):
        """
        Records a scaling activity.
        """
        try:
            self.db_manager.insert_scaling_activity(group_id, activity_key, action, status, eval_qps, eval_capacity, target_qps, response)
        except Exception as e:
            self.logger.error(f"Failed to record scaling activity for group {group_id}: {e}")

    def record_error(self, group_id: int, source: str, message: str, context: dict):
        """
        Records an error and increments the consecutive error count for the group.
        """
        try:
            self.db_manager.insert_error(group_id, source, message, context)
            # Increment consecutive_errors in resource_group_state
            if group_id:
                self.db_manager.increment_consecutive_errors(group_id)
        except Exception as e:
            self.logger.error(f"Failed to record error for group {group_id}: {e}")

    def update_cooldown_state(self, group_id: int, cooldown_seconds: int):
        """
        Updates the cooldown state for a scaling group.
        """
        try:
            now = datetime.now(timezone.utc)
            cooldown_until = now + timedelta(seconds=cooldown_seconds)
            state_updates = {
                "cooldown_until": cooldown_until,
                "last_evaluated_at": now
            }
            self.db_manager.update_resource_group_state(group_id, state_updates)
        except Exception as e:
            self.logger.error(f"Failed to update cooldown state for group {group_id}: {e}")

    def get_cooldown_state(self, group_id: int) -> Optional[Dict[str, Any]]:
        """
        Retrieves the cooldown state for a resource group from the database.
        """
        return self.db_manager.get_resource_group_state(group_id)

    def is_in_cooldown(self, group_id: int, action_type: str, cooldown_seconds: int) -> bool:
        """
        Checks if a resource group is currently in cooldown.
        Ignores action_type and cooldown_seconds, relies on DB cooldown_until.
        """
        state = self.get_cooldown_state(group_id)
        if not state:
            return False

        cooldown_until = state.get("cooldown_until")
        if cooldown_until and cooldown_until > datetime.now(timezone.utc):
            remaining = (cooldown_until - datetime.now(timezone.utc)).total_seconds()
            self.logger.info(f"Group {group_id} is in cooldown. {int(remaining)}s remaining.")
            return True
        return False