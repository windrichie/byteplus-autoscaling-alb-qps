import json
import os
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from pathlib import Path


class StateManager:
    """
    State management system using TOS mount for persistence.
    Handles scaling state, cooldown tracking, and activity history.
    """
    
    def __init__(self, tos_mount_path: str = "/tosmount", state_file: str = "scaling_state.json"):
        self.tos_mount_path = tos_mount_path
        self.state_file_path = os.path.join(tos_mount_path, state_file)
        self.logger = logging.getLogger(__name__)
        
        # Ensure the mount path exists
        Path(tos_mount_path).mkdir(parents=True, exist_ok=True)
        
        # Initialize state if file doesn't exist
        if not os.path.exists(self.state_file_path):
            self._initialize_state()
    
    def _initialize_state(self) -> None:
        """
        Initialize the state file with default values.
        """
        initial_state = {
            "version": "1.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "scaling_history": [],
            "cooldown_state": {
                "last_scaling_action": None,
                "last_scale_up": None,
                "last_scale_down": None,
                "last_general_action": None
            },
            "metrics_cache": {
                "last_qps_value": None,
                "last_qps_timestamp": None,
                "last_instance_count": None
            },
            "error_tracking": {
                "consecutive_errors": 0,
                "last_error": None,
                "last_error_timestamp": None
            },
            "statistics": {
                "total_scale_ups": 0,
                "total_scale_downs": 0,
                "total_executions": 0,
                "total_errors": 0
            }
        }
        
        self._save_state(initial_state)
        self.logger.info(f"Initialized state file at {self.state_file_path}")
    
    def _load_state(self) -> Dict[str, Any]:
        """
        Load state from the TOS-mounted file.
        
        Returns:
            Dictionary containing the current state
        """
        try:
            with open(self.state_file_path, 'r') as f:
                state = json.load(f)
            return state
        except FileNotFoundError:
            self.logger.warning("State file not found, initializing new state")
            self._initialize_state()
            return self._load_state()
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse state file: {e}")
            # Backup corrupted file and create new one
            backup_path = f"{self.state_file_path}.backup.{int(datetime.now().timestamp())}"
            os.rename(self.state_file_path, backup_path)
            self.logger.info(f"Backed up corrupted state file to {backup_path}")
            self._initialize_state()
            return self._load_state()
        except Exception as e:
            self.logger.error(f"Unexpected error loading state: {e}")
            raise
    
    def _save_state(self, state: Dict[str, Any]) -> None:
        """
        Save state to the TOS-mounted file.
        
        Args:
            state: State dictionary to save
        """
        try:
            # Update last_updated timestamp
            state["last_updated"] = datetime.now(timezone.utc).isoformat()
            
            # Write to temporary file first, then rename for atomic operation
            temp_path = f"{self.state_file_path}.tmp"
            with open(temp_path, 'w') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            
            # Atomic rename
            os.rename(temp_path, self.state_file_path)
            
        except Exception as e:
            self.logger.error(f"Failed to save state: {e}")
            # Clean up temp file if it exists
            if os.path.exists(f"{self.state_file_path}.tmp"):
                os.remove(f"{self.state_file_path}.tmp")
            raise
    
    def get_cooldown_state(self) -> Dict[str, Any]:
        """
        Get current cooldown state.
        
        Returns:
            Dictionary containing cooldown timestamps
        """
        state = self._load_state()
        return state.get("cooldown_state", {})
    
    def update_cooldown_state(self, action_type: str, timestamp: Optional[datetime] = None) -> None:
        """
        Update cooldown state after a scaling action.
        
        Args:
            action_type: Type of action ('scale_up', 'scale_down', 'general')
            timestamp: Timestamp of the action (defaults to now)
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        timestamp_str = timestamp.isoformat()
        
        state = self._load_state()
        cooldown_state = state.get("cooldown_state", {})
        
        # Update specific action timestamp
        if action_type == "scale_up":
            cooldown_state["last_scale_up"] = timestamp_str
        elif action_type == "scale_down":
            cooldown_state["last_scale_down"] = timestamp_str
        
        # Always update general timestamps
        cooldown_state["last_scaling_action"] = timestamp_str
        cooldown_state["last_general_action"] = timestamp_str
        
        state["cooldown_state"] = cooldown_state
        self._save_state(state)
        
        self.logger.info(f"Updated cooldown state for {action_type} at {timestamp_str}")
    
    def is_in_cooldown(self, action_type: str, cooldown_seconds: int) -> bool:
        """
        Check if we're still in cooldown period for a specific action type.
        
        Args:
            action_type: Type of action to check ('scale_up', 'scale_down', 'general')
            cooldown_seconds: Cooldown period in seconds
            
        Returns:
            True if still in cooldown, False otherwise
        """
        cooldown_state = self.get_cooldown_state()
        
        timestamp_key = {
            "scale_up": "last_scale_up",
            "scale_down": "last_scale_down",
            "general": "last_general_action"
        }.get(action_type)
        
        if not timestamp_key or not cooldown_state.get(timestamp_key):
            return False
        
        try:
            last_action_time = datetime.fromisoformat(cooldown_state[timestamp_key])
            time_since_action = (datetime.now(timezone.utc) - last_action_time).total_seconds()
            
            in_cooldown = time_since_action < cooldown_seconds
            
            if in_cooldown:
                remaining_time = cooldown_seconds - time_since_action
                self.logger.info(f"Still in {action_type} cooldown: {remaining_time:.0f}s remaining")
            
            return in_cooldown
            
        except Exception as e:
            self.logger.error(f"Error checking cooldown for {action_type}: {e}")
            return False
    
    def add_scaling_activity(self, activity: Dict[str, Any]) -> None:
        """
        Add a scaling activity to the history.
        
        Args:
            activity: Dictionary containing scaling activity details
        """
        state = self._load_state()
        
        # Add timestamp if not present
        if "timestamp" not in activity:
            activity["timestamp"] = datetime.now(timezone.utc).isoformat()
        
        # Add to history
        scaling_history = state.get("scaling_history", [])
        scaling_history.append(activity)
        
        # Keep only last 100 activities to prevent file from growing too large
        if len(scaling_history) > 100:
            scaling_history = scaling_history[-100:]
        
        state["scaling_history"] = scaling_history
        
        # Update statistics
        stats = state.get("statistics", {})
        stats["total_executions"] = stats.get("total_executions", 0) + 1
        
        if activity.get("action") == "scale_up":
            stats["total_scale_ups"] = stats.get("total_scale_ups", 0) + 1
        elif activity.get("action") == "scale_down":
            stats["total_scale_downs"] = stats.get("total_scale_downs", 0) + 1
        
        state["statistics"] = stats
        
        self._save_state(state)
        self.logger.info(f"Added scaling activity: {activity.get('action', 'unknown')}")
    
    def get_scaling_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent scaling history.
        
        Args:
            limit: Maximum number of activities to return
            
        Returns:
            List of recent scaling activities
        """
        state = self._load_state()
        scaling_history = state.get("scaling_history", [])
        
        # Return most recent activities
        return scaling_history[-limit:] if scaling_history else []
    
    def update_metrics_cache(self, qps_value: Optional[float], instance_count: Optional[int]) -> None:
        """
        Update cached metrics for reference.
        
        Args:
            qps_value: Current QPS value
            instance_count: Current instance count
        """
        state = self._load_state()
        
        metrics_cache = state.get("metrics_cache", {})
        
        if qps_value is not None:
            metrics_cache["last_qps_value"] = qps_value
            metrics_cache["last_qps_timestamp"] = datetime.now(timezone.utc).isoformat()
        
        if instance_count is not None:
            metrics_cache["last_instance_count"] = instance_count
        
        state["metrics_cache"] = metrics_cache
        self._save_state(state)
    
    def get_metrics_cache(self) -> Dict[str, Any]:
        """
        Get cached metrics.
        
        Returns:
            Dictionary containing cached metrics
        """
        state = self._load_state()
        return state.get("metrics_cache", {})
    
    def record_error(self, error_message: str, error_type: str = "general") -> None:
        """
        Record an error occurrence.
        
        Args:
            error_message: Description of the error
            error_type: Type/category of the error
        """
        state = self._load_state()
        
        error_tracking = state.get("error_tracking", {})
        error_tracking["consecutive_errors"] = error_tracking.get("consecutive_errors", 0) + 1
        error_tracking["last_error"] = {
            "message": error_message,
            "type": error_type,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        error_tracking["last_error_timestamp"] = datetime.now(timezone.utc).isoformat()
        
        state["error_tracking"] = error_tracking
        
        # Update statistics
        stats = state.get("statistics", {})
        stats["total_errors"] = stats.get("total_errors", 0) + 1
        state["statistics"] = stats
        
        self._save_state(state)
        self.logger.error(f"Recorded error: {error_type} - {error_message}")
    
    def clear_error_count(self) -> None:
        """
        Clear the consecutive error count (call after successful operation).
        """
        state = self._load_state()
        
        error_tracking = state.get("error_tracking", {})
        if error_tracking.get("consecutive_errors", 0) > 0:
            error_tracking["consecutive_errors"] = 0
            state["error_tracking"] = error_tracking
            self._save_state(state)
            self.logger.info("Cleared consecutive error count")
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get operational statistics.
        
        Returns:
            Dictionary containing statistics
        """
        state = self._load_state()
        return state.get("statistics", {})
    
    def get_full_state(self) -> Dict[str, Any]:
        """
        Get the complete state for debugging or monitoring.
        
        Returns:
            Complete state dictionary
        """
        return self._load_state()
    
    def reset_state(self) -> None:
        """
        Reset the state to initial values (use with caution).
        """
        self.logger.warning("Resetting state to initial values")
        
        # Backup current state
        current_state = self._load_state()
        backup_path = f"{self.state_file_path}.backup.{int(datetime.now().timestamp())}"
        
        with open(backup_path, 'w') as f:
            json.dump(current_state, f, indent=2)
        
        self.logger.info(f"Backed up current state to {backup_path}")
        
        # Initialize new state
        self._initialize_state()