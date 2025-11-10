import logging
import json
import psycopg
from typing import List, Dict, Any, Optional
from psycopg.rows import dict_row
from datetime import datetime, timezone

class DBManager:
    """
    Manages interactions with the PostgreSQL database.
    """
    def __init__(self, dsn: str):
        """
        Initializes the DBManager with a database connection string.

        Args:
            dsn: PostgreSQL connection string.
        """
        self.dsn = dsn
        self.logger = logging.getLogger(__name__)

    def _get_connection(self):
        """
        Establishes a new database connection.
        """
        try:
            conn = psycopg.connect(self.dsn, row_factory=dict_row)
            return conn
        except psycopg.OperationalError as e:
            self.logger.error(f"Failed to connect to the database: {e}")
            raise

    def get_enabled_resource_groups(self) -> List[Dict[str, Any]]:
        """
        Fetches all enabled resource groups from the database.

        Returns:
            A list of dictionaries, where each dictionary represents a resource group.
        """
        query = "SELECT * FROM resource_groups WHERE enabled = TRUE;"
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query)
                    groups = cur.fetchall()
                    self.logger.info(f"Fetched {len(groups)} enabled resource groups from the database.")
                    return groups
        except Exception as e:
            self.logger.error(f"Failed to fetch enabled resource groups: {e}")
            return []

    def insert_scaling_activity(self, group_id: int, activity_key: str, action: str, status: str,
                                eval_qps: float, eval_capacity: int, target_qps: float, response: dict):
        """
        Inserts a new scaling activity record into the database.
        """
        query = """
            INSERT INTO scaling_activities (resource_group_id, activity_key, action, status, eval_qps, eval_capacity, target_qps, response)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (group_id, activity_key, action, status, eval_qps, eval_capacity, target_qps, json.dumps(response)))

    def insert_error(self, group_id: Optional[int], source: str, message: str, context: dict):
        """
        Inserts a new error record into the database.
        """
        query = """
            INSERT INTO errors (resource_group_id, source, message, context)
            VALUES (%s, %s, %s, %s)
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (group_id, source, message, json.dumps(context)))

    def update_resource_group_state(self, group_id: int, state_updates: Dict[str, Any]):
        """
        Updates the state of a resource group, inserting a new record if it doesn't exist.
        Only allows updates to columns that exist in the schema.
        """
        valid_columns = [
            "last_evaluated_at", "cooldown_until", "consecutive_errors",
            "circuit_open_until", "suspended", "latest_qps", "latest_capacity"
        ]
        
        filtered_updates = {k: v for k, v in state_updates.items() if k in valid_columns}

        if not filtered_updates:
            self.logger.warning(f"No valid fields to update for resource_group_id: {group_id}")
            return

        set_clause = ", ".join([f"{key} = EXCLUDED.{key}" for key in filtered_updates.keys()])
        columns = ", ".join(filtered_updates.keys())
        placeholders = ", ".join(["%s"] * len(filtered_updates))

        query = f"""
            INSERT INTO resource_group_state (resource_group_id, {columns})
            VALUES (%s, {placeholders})
            ON CONFLICT (resource_group_id) DO UPDATE
            SET {set_clause}
        """
        
        values = [group_id] + list(filtered_updates.values())

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, values)

    def increment_consecutive_errors(self, group_id: int):
        """Increments the consecutive_errors counter for the resource group."""
        query = """
            INSERT INTO resource_group_state (resource_group_id, consecutive_errors)
            VALUES (%s, 1)
            ON CONFLICT (resource_group_id) DO UPDATE
            SET consecutive_errors = resource_group_state.consecutive_errors + 1
        """
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (group_id,))

    def get_resource_group_state(self, group_id: int) -> Optional[Dict[str, Any]]:
        """
        Retrieves the state of a resource group from the database.
        """
        query = "SELECT * FROM resource_group_state WHERE resource_group_id = %s;"
        try:
            with self._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, (group_id,))
                    state = cur.fetchone()
                    return state if state else {}
        except Exception as e:
            self.logger.error(f"Failed to get state for group {group_id}: {e}")
            return {}