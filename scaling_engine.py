import logging
import math
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
from config import ScalingConfig
from state_manager import StateManager
from cloudmonitor_client import CloudMonitorClient
from autoscaling_client import AutoScalingClient


class ScalingEngine:
    """
    Core scaling decision engine with cooldown and safety checks.
    Implements the business logic for autoscaling based on ALB QPS metrics.
    """
    
    def __init__(self, config: ScalingConfig, state_manager: StateManager,
                 cloudmonitor_client: CloudMonitorClient, autoscaling_client: AutoScalingClient):
        self.config = config
        self.state_manager = state_manager
        self.cloudmonitor_client = cloudmonitor_client
        self.autoscaling_client = autoscaling_client
        self.logger = logging.getLogger(__name__)
    
    def evaluate_scaling_decision(self) -> Dict[str, Any]:
        """
        Main method to evaluate and execute scaling decisions.
        
        Returns:
            Dictionary containing the scaling decision and execution result
        """
        decision_result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "none",
            "reason": "no_action_needed",
            "current_qps": None,
            "current_instances": None,
            "qps_per_instance": None,
            "target_qps_per_instance": self.config.target_qps_per_instance,
            "scale_up_threshold": self.config.get_scale_up_qps_threshold(),
            "scale_down_threshold": self.config.get_scale_down_qps_threshold(),
            "dry_run": self.config.dry_run_mode,
            "error": None,
            "execution_result": None
        }
        
        try:
            # Step 1: Check if scaling is already in progress
            if self._is_scaling_in_progress():
                decision_result["reason"] = "scaling_in_progress"
                self.logger.info("Scaling activity already in progress, skipping evaluation")
                return decision_result
            
            # Step 2: Get current metrics
            current_qps, current_instances = self._get_current_metrics()
            if current_qps is None or current_instances is None:
                decision_result["reason"] = "metrics_unavailable"
                decision_result["error"] = "Failed to retrieve required metrics"
                return decision_result
            
            decision_result["current_qps"] = current_qps
            decision_result["current_instances"] = current_instances
            
            # Step 3: Calculate QPS per instance (handle cold start)
            if current_instances == 0:
                # Cold start scenario: avoid division by zero
                qps_per_instance = 0
                self.logger.info("Cold start detected: 0 instances, setting QPS per instance to 0")
            else:
                qps_per_instance = current_qps / current_instances
            
            decision_result["qps_per_instance"] = qps_per_instance
            
            # Step 4: Evaluate scaling decision
            if self.config.enable_dynamic_scaling:
                # Use dynamic scaling - calculate exact instances needed
                dynamic_scaling = self._calculate_dynamic_scaling_amount(current_qps, current_instances)
                
                # Check if dynamic scaling suggests an action
                if dynamic_scaling["action"] != "none":
                    # Pure dynamic scaling: always execute optimal scaling (no threshold checks)
                    decision_result["action"] = dynamic_scaling["action"]
                    decision_result["scaling_amount"] = dynamic_scaling["amount"]
                    decision_result["optimal_instances"] = dynamic_scaling["optimal_instances"]
                    decision_result["required_change"] = dynamic_scaling["required_change"]
                    decision_result["limited_by_safety"] = dynamic_scaling["limited_by_safety"]
                    
                    if dynamic_scaling["limited_by_safety"]:
                        decision_result["reason"] = f"dynamic_scaling_limited_{dynamic_scaling['action']}"
                    else:
                        decision_result["reason"] = f"dynamic_scaling_{dynamic_scaling['action']}"
                else:
                    # Dynamic scaling suggests no action
                    decision_result["action"] = "none"
                    # Check if we're at optimal count or constrained by ASG
                    if dynamic_scaling.get("limited_by_asg", False):
                        asg_limit_type = dynamic_scaling.get("asg_limit_type")
                        if asg_limit_type == "min":
                            decision_result["reason"] = "at_asg_min_capacity"
                        elif asg_limit_type == "max":
                            decision_result["reason"] = "at_asg_max_capacity"
                        else:
                            decision_result["reason"] = "constrained_by_asg_limits"
                    else:
                        decision_result["reason"] = "optimal_instance_count_reached"
            else:
                # Use traditional threshold-based scaling with static increments
                scaling_decision = self._evaluate_scaling_need(qps_per_instance, current_instances)
                decision_result["action"] = scaling_decision["action"]
                decision_result["reason"] = scaling_decision["reason"]
                # For static scaling, use configured increments
                if decision_result["action"] == "scale_up":
                    decision_result["scaling_amount"] = self.config.scale_up_increment
                elif decision_result["action"] == "scale_down":
                    decision_result["scaling_amount"] = self.config.scale_down_decrement
            
            # Step 5: Check cooldown periods
            if decision_result["action"] != "none":
                cooldown_check = self._check_cooldown_periods(decision_result["action"])
                if not cooldown_check["allowed"]:
                    decision_result["action"] = "none"
                    decision_result["reason"] = f"cooldown_{cooldown_check['type']}"
                    decision_result["cooldown_remaining"] = cooldown_check["remaining_seconds"]
                    return decision_result
            
            # Step 6: Execute scaling action if needed
            if decision_result["action"] != "none" and not self.config.dry_run_mode:
                scaling_amount = decision_result.get("scaling_amount", 1)  # Default to 1 for backward compatibility
                execution_result = self._execute_scaling_action(decision_result["action"], scaling_amount)
                decision_result["execution_result"] = execution_result
            
            # Step 7: Update state and metrics cache
            self.state_manager.update_metrics_cache(current_qps, current_instances)
            
            if decision_result["action"] != "none":
                activity_data = {
                    "action": decision_result["action"],
                    "reason": decision_result["reason"],
                    "qps_per_instance": qps_per_instance,
                    "current_qps": current_qps,
                    "current_instances": current_instances,
                    "scaling_amount": decision_result.get("scaling_amount", 1),
                    "dry_run": self.config.dry_run_mode,
                    "execution_result": decision_result.get("execution_result")
                }
                
                # Add dynamic scaling specific fields if available
                if "optimal_instances" in decision_result:
                    activity_data["optimal_instances"] = decision_result["optimal_instances"]
                    activity_data["required_change"] = decision_result["required_change"]
                    activity_data["limited_by_safety"] = decision_result["limited_by_safety"]
                
                self.state_manager.add_scaling_activity(activity_data)
            
            return decision_result
            
        except Exception as e:
            error_msg = f"Error during scaling evaluation: {str(e)}"
            self.logger.error(error_msg)
            decision_result["error"] = error_msg
            decision_result["reason"] = "evaluation_error"
            self.state_manager.record_error(error_msg, "scaling_evaluation")
            return decision_result
    
    def _get_current_metrics(self) -> Tuple[Optional[float], Optional[int]]:
        """
        Get current QPS and instance count.
        
        Returns:
            Tuple of (current_qps, current_instances)
        """
        try:
            # Get QPS metrics using metric_period directly in seconds
            current_qps = self.cloudmonitor_client.get_average_qps(
                self.config.alb_id, 
                period_seconds=self.config.metric_period
            )
            
            # Get current instance count
            current_instances = self.autoscaling_client.get_healthy_instance_count(
                self.config.autoscaling_group_id
            )
            
            self.logger.info(f"Current metrics - QPS: {current_qps}, Instances: {current_instances}")
            return current_qps, current_instances
            
        except Exception as e:
            self.logger.error(f"Failed to get current metrics: {e}")
            return None, None
    
    def _calculate_dynamic_scaling_amount(self, current_qps: float, current_instances: int) -> Dict[str, Any]:
        """
        Calculate the required scaling amount to achieve target QPS per instance.
        
        Args:
            current_qps: Current QPS from ALB
            current_instances: Current number of instances
            
        Returns:
            Dictionary with scaling calculation details
        """
        # Calculate optimal instance count
        # Defensive check: prevent division by zero
        if self.config.target_qps_per_instance <= 0:
            self.logger.error(f"Invalid target_qps_per_instance: {self.config.target_qps_per_instance}. Must be > 0.")
            # Fallback: assume 1 instance is needed (conservative approach)
            optimal_instances = 1
        else:
            optimal_instances = math.ceil(current_qps / self.config.target_qps_per_instance)
        
        # Enforce ASG limits on optimal instances
        asg_limited = False
        asg_limit_type = None
        try:
            asg_status = self.autoscaling_client.get_scaling_group_status(self.config.autoscaling_group_id)
            asg_min = asg_status['min_instances']
            asg_max = asg_status['max_instances']
            
            # Cap optimal instances to ASG limits
            optimal_instances_capped = max(asg_min, min(optimal_instances, asg_max))
            
            if optimal_instances_capped != optimal_instances:
                if optimal_instances < asg_min:
                    asg_limit_type = "min"
                    self.logger.info(f"Optimal instances capped by ASG min limit: {optimal_instances} → {optimal_instances_capped} (ASG min: {asg_min})")
                elif optimal_instances > asg_max:
                    asg_limit_type = "max"
                    self.logger.info(f"Optimal instances capped by ASG max limit: {optimal_instances} → {optimal_instances_capped} (ASG max: {asg_max})")
                
                asg_limited = True
                optimal_instances = optimal_instances_capped
                
        except Exception as e:
            self.logger.error(f"Failed to get ASG status for limit enforcement: {e}")
            # Continue without ASG limit enforcement if API call fails
        
        # Calculate required change
        required_change = optimal_instances - current_instances
        
        # Determine action type
        if required_change > 0:
            action = "scale_up"
            amount = required_change
            # Apply safety limit if configured (0 = no limit)
            if self.config.max_scale_up_per_action > 0:
                amount = min(amount, self.config.max_scale_up_per_action)
        elif required_change < 0:
            action = "scale_down"
            amount = abs(required_change)
            # Apply safety limit if configured (0 = no limit)
            if self.config.max_scale_down_per_action > 0:
                amount = min(amount, self.config.max_scale_down_per_action)
        else:
            action = "none"
            amount = 0
        
        return {
            "action": action,
            "amount": amount,
            "optimal_instances": optimal_instances,
            "required_change": required_change,
            "limited_by_safety": (
                (action == "scale_up" and self.config.max_scale_up_per_action > 0 and amount < abs(required_change)) or
                (action == "scale_down" and self.config.max_scale_down_per_action > 0 and amount < abs(required_change))
            ),
            "limited_by_asg": asg_limited,
            "asg_limit_type": asg_limit_type
        }

    def _evaluate_scaling_need(self, qps_per_instance: float, current_instances: int) -> Dict[str, str]:
        """
        Evaluate if scaling is needed based on QPS per instance.
        
        Args:
            qps_per_instance: Current QPS per instance
            current_instances: Current number of instances
            
        Returns:
            Dictionary with action and reason
        """
        scale_up_threshold = self.config.get_scale_up_qps_threshold()
        scale_down_threshold = self.config.get_scale_down_qps_threshold()
        
        self.logger.info(f"QPS per instance: {qps_per_instance:.2f}, Scale-up threshold: {scale_up_threshold:.2f}, Scale-down threshold: {scale_down_threshold:.2f}")
        
        # Check for scale-up
        if qps_per_instance > scale_up_threshold:
            # Get current ASG status to check max capacity
            try:
                asg_status = self.autoscaling_client.get_scaling_group_status(self.config.autoscaling_group_id)
                if current_instances >= asg_status['max_instances']:
                    return {"action": "none", "reason": "at_max_capacity"}
            except Exception as e:
                self.logger.error(f"Failed to get ASG status for max capacity check: {e}")
                return {"action": "none", "reason": "asg_status_error"}
            return {"action": "scale_up", "reason": "qps_above_threshold"}
        
        # Check for scale-down
        elif qps_per_instance < scale_down_threshold:
            # Get current ASG status to check min capacity
            try:
                asg_status = self.autoscaling_client.get_scaling_group_status(self.config.autoscaling_group_id)
                if current_instances <= asg_status['min_instances']:
                    return {"action": "none", "reason": "at_min_capacity"}
            except Exception as e:
                self.logger.error(f"Failed to get ASG status for min capacity check: {e}")
                return {"action": "none", "reason": "asg_status_error"}
            return {"action": "scale_down", "reason": "qps_below_threshold"}
        
        # No scaling needed
        return {"action": "none", "reason": "qps_within_thresholds"}
    

    
    def _check_cooldown_periods(self, action: str) -> Dict[str, Any]:
        """
        Check if the action is allowed based on cooldown periods.
        
        Args:
            action: Scaling action to check
            
        Returns:
            Dictionary with cooldown check result
        """
        cooldown_checks = {
            "scale_up": self.config.scale_up_cooldown,
            "scale_down": self.config.scale_down_cooldown
        }
        
        # Check general cooldown first
        if self.state_manager.is_in_cooldown("general", self.config.general_cooldown):
            return {
                "allowed": False,
                "type": "general",
                "remaining_seconds": self._get_remaining_cooldown("general", self.config.general_cooldown)
            }
        
        # Check specific action cooldown
        if action in cooldown_checks:
            cooldown_period = cooldown_checks[action]
            if cooldown_period > 0 and self.state_manager.is_in_cooldown(action, cooldown_period):
                return {
                    "allowed": False,
                    "type": action,
                    "remaining_seconds": self._get_remaining_cooldown(action, cooldown_period)
                }
        
        return {"allowed": True, "type": None, "remaining_seconds": 0}
    
    def _get_remaining_cooldown(self, action_type: str, cooldown_seconds: int) -> int:
        """
        Get remaining cooldown time in seconds.
        
        Args:
            action_type: Type of action
            cooldown_seconds: Cooldown period in seconds
            
        Returns:
            Remaining cooldown time in seconds
        """
        try:
            cooldown_state = self.state_manager.get_cooldown_state()
            timestamp_key = {
                "scale_up": "last_scale_up",
                "scale_down": "last_scale_down",
                "general": "last_general_action"
            }.get(action_type)
            
            if timestamp_key and cooldown_state.get(timestamp_key):
                last_action_time = datetime.fromisoformat(cooldown_state[timestamp_key])
                elapsed = (datetime.now(timezone.utc) - last_action_time).total_seconds()
                return max(0, int(cooldown_seconds - elapsed))
            
            return 0
        except Exception:
            return 0
    
    def _is_scaling_in_progress(self) -> bool:
        """
        Check if there's currently a scaling activity in progress.
        
        Returns:
            True if scaling is in progress
        """
        try:
            return self.autoscaling_client.is_scaling_in_progress(self.config.autoscaling_group_id)
        except Exception as e:
            self.logger.error(f"Failed to check scaling progress: {e}")
            return False
    
    def _execute_scaling_action(self, action: str, scaling_amount: int = 1) -> Dict[str, Any]:
        """
        Execute the scaling action.
        
        Args:
            action: Scaling action to execute
            scaling_amount: Number of instances to scale by
            
        Returns:
            Execution result
        """
        try:
            if action == "scale_up":
                return self._execute_scale_up(scaling_amount)
            elif action == "scale_down":
                return self._execute_scale_down(scaling_amount)
            else:
                return {"status": "error", "message": f"Unknown action: {action}"}
                
        except Exception as e:
            error_msg = f"Failed to execute {action}: {str(e)}"
            self.logger.error(error_msg)
            self.state_manager.record_error(error_msg, "scaling_execution")
            return {"status": "error", "message": error_msg}
    
    def _execute_scale_up(self, scaling_amount: int = None) -> Dict[str, Any]:
        """
        Execute scale-up action.
        
        Args:
            scaling_amount: Number of instances to add (defaults to config increment)
        
        Returns:
            Execution result
        """
        if scaling_amount is None:
            scaling_amount = self.config.scale_up_increment
            
        try:
            self.logger.info(f"Executing scale-up by {scaling_amount} instances")
            
            result = self.autoscaling_client.scale_out(
                self.config.autoscaling_group_id,
                scaling_amount
            )
            
            # Update cooldown state
            self.state_manager.update_cooldown_state("scale_up")
            
            # Clear error count on successful operation
            self.state_manager.clear_error_count()
            
            self.logger.info("Scale-up executed successfully")
            return {"status": "success", "result": result}
            
        except Exception as e:
            error_msg = f"Scale-up execution failed: {str(e)}"
            self.logger.error(error_msg)
            self.state_manager.record_error(error_msg, "scale_up_execution")
            return {"status": "error", "message": error_msg}
    
    def _execute_scale_down(self, scaling_amount: int = None) -> Dict[str, Any]:
        """
        Execute scale-down action.
        
        Args:
            scaling_amount: Number of instances to remove (defaults to config decrement)
        
        Returns:
            Execution result
        """
        if scaling_amount is None:
            scaling_amount = self.config.scale_down_decrement
            
        try:
            self.logger.info(f"Executing scale-down by {scaling_amount} instances")
            
            result = self.autoscaling_client.scale_in(
                self.config.autoscaling_group_id,
                scaling_amount
            )
            
            # Update cooldown state
            self.state_manager.update_cooldown_state("scale_down")
            
            # Clear error count on successful operation
            self.state_manager.clear_error_count()
            
            self.logger.info("Scale-down executed successfully")
            return {"status": "success", "result": result}
            
        except Exception as e:
            error_msg = f"Scale-down execution failed: {str(e)}"
            self.logger.error(error_msg)
            self.state_manager.record_error(error_msg, "scale_down_execution")
            return {"status": "error", "message": error_msg}
    
    def get_current_status(self) -> Dict[str, Any]:
        """
        Get current status of the scaling system.
        
        Returns:
            Dictionary containing current status information
        """
        try:
            # Get current metrics
            current_qps, current_instances = self._get_current_metrics()
            
            # Get scaling group status
            asg_status = self.autoscaling_client.get_scaling_group_status(self.config.autoscaling_group_id)
            
            # Get state information
            cooldown_state = self.state_manager.get_cooldown_state()
            metrics_cache = self.state_manager.get_metrics_cache()
            statistics = self.state_manager.get_statistics()
            recent_history = self.state_manager.get_scaling_history(5)
            
            # Calculate QPS per instance
            qps_per_instance = None
            if current_qps is not None and current_instances and current_instances > 0:
                qps_per_instance = current_qps / current_instances
            
            status = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "config": {
                    "target_qps_per_instance": self.config.target_qps_per_instance,
                    "scale_up_threshold": self.config.get_scale_up_qps_threshold(),
                    "scale_down_threshold": self.config.get_scale_down_qps_threshold(),
                    "dry_run_mode": self.config.dry_run_mode
                },
                "current_metrics": {
                    "qps": current_qps,
                    "instances": current_instances,
                    "qps_per_instance": qps_per_instance
                },
                "scaling_group": asg_status,
                "cooldown_state": cooldown_state,
                "metrics_cache": metrics_cache,
                "statistics": statistics,
                "recent_history": recent_history,
                "scaling_in_progress": self._is_scaling_in_progress()
            }
            
            return status
            
        except Exception as e:
            self.logger.error(f"Failed to get current status: {e}")
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": str(e),
                "status": "error"
            }
    
    def validate_configuration(self) -> Dict[str, Any]:
        """
        Validate the current configuration and connectivity.
        
        Returns:
            Validation result
        """
        validation_result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config_valid": True,
            "connectivity_checks": {},
            "errors": [],
            "warnings": []
        }
        
        try:
            # Validate configuration
            self.config.validate()
            
        except ValueError as e:
            validation_result["config_valid"] = False
            validation_result["errors"].append(f"Configuration validation failed: {str(e)}")
        
        # Test ALB metrics connectivity
        try:
            metrics_available = self.cloudmonitor_client.check_metric_availability(self.config.alb_id)
            validation_result["connectivity_checks"]["alb_metrics"] = {
                "status": "success" if metrics_available else "warning",
                "message": "ALB metrics available" if metrics_available else "ALB metrics not available"
            }
            if not metrics_available:
                validation_result["warnings"].append("ALB metrics not available - check ALB ID and permissions")
                
        except Exception as e:
            validation_result["connectivity_checks"]["alb_metrics"] = {
                "status": "error",
                "message": str(e)
            }
            validation_result["errors"].append(f"ALB metrics check failed: {str(e)}")
        
        # Test AutoScaling Group connectivity
        try:
            asg_status = self.autoscaling_client.get_scaling_group_status(self.config.autoscaling_group_id)
            validation_result["connectivity_checks"]["autoscaling_group"] = {
                "status": "success",
                "message": f"AutoScaling Group found with {asg_status['current_instances']} instances"
            }
            
        except Exception as e:
            validation_result["connectivity_checks"]["autoscaling_group"] = {
                "status": "error",
                "message": str(e)
            }
            validation_result["errors"].append(f"AutoScaling Group check failed: {str(e)}")
        
        # Test state management
        try:
            state = self.state_manager.get_full_state()
            validation_result["connectivity_checks"]["state_management"] = {
                "status": "success",
                "message": "State management working"
            }
            
        except Exception as e:
            validation_result["connectivity_checks"]["state_management"] = {
                "status": "error",
                "message": str(e)
            }
            validation_result["errors"].append(f"State management check failed: {str(e)}")
        
        # Overall validation status
        validation_result["overall_status"] = "success" if not validation_result["errors"] else "error"
        
        return validation_result