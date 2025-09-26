import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from byteplus_api_client import BytePlusAPIClient


class AutoScalingClient:
    """
    BytePlus AutoScaling client for managing scaling groups.
    """
    
    def __init__(self, api_client: BytePlusAPIClient):
        self.api_client = api_client
        self.logger = logging.getLogger(__name__)
        self.service = "auto_scaling"
        self.version = "2020-01-01"
    
    def describe_scaling_group(self, scaling_group_id: str) -> Dict[str, Any]:
        """
        Get detailed information about a scaling group.
        
        Args:
            scaling_group_id: The ID of the scaling group
            
        Returns:
            Dictionary containing scaling group information
        """
        try:
            query_params = {
                "ScalingGroupIds.1": scaling_group_id
            }
            
            response = self.api_client.make_json_request(
                method="GET",
                service=self.service,
                version=self.version,
                action="DescribeScalingGroups",
                query_params=query_params
            )
            
            if 'Result' in response and 'ScalingGroups' in response['Result']:
                scaling_groups = response['Result']['ScalingGroups']
                if scaling_groups:
                    scaling_group = scaling_groups[0]
                    # Only log once when first called
                    return scaling_group
                else:
                    raise ValueError(f"Scaling group {scaling_group_id} not found")
            else:
                raise ValueError(f"Invalid response format: {response}")
                
        except Exception as e:
            self.logger.error(f"Failed to describe scaling group {scaling_group_id}: {e}")
            raise
    
    def get_scaling_group_status(self, scaling_group_id: str) -> Dict[str, Any]:
        """
        Get current status and instance counts for a scaling group.
        
        Args:
            scaling_group_id: The ID of the scaling group
            
        Returns:
            Dictionary containing current status information
        """
        try:
            scaling_group = self.describe_scaling_group(scaling_group_id)
            
            status_info = {
                "scaling_group_id": scaling_group_id,
                "lifecycle_state": scaling_group.get('LifecycleState'),
                "current_instances": scaling_group.get('TotalInstanceCount', 0),
                "desired_instances": scaling_group.get('DesireInstanceNumber', 0),
                "min_instances": scaling_group.get('MinInstanceNumber', 0),
                "max_instances": scaling_group.get('MaxInstanceNumber', 0),
                "created_at": scaling_group.get('CreatedAt'),
                "updated_at": scaling_group.get('UpdatedAt')
            }
            
            # Only log essential status info, not the full dictionary
            return status_info
            
        except Exception as e:
            self.logger.error(f"Failed to get scaling group status: {e}")
            raise
    
    def get_healthy_instance_count(self, scaling_group_id: str) -> int:
        """
        Get the number of healthy instances in the scaling group.
        Note: This is a simplified implementation. In practice, you might need
        to call additional APIs to get detailed instance health status.
        
        Args:
            scaling_group_id: The ID of the scaling group
            
        Returns:
            Number of healthy instances
        """
        try:
            status = self.get_scaling_group_status(scaling_group_id)
            # For now, we assume all current instances are healthy
            # In a production environment, you might want to check instance health status
            healthy_count = status['current_instances']
            
            # Log consolidated scaling group info once
            self.logger.info(f"Scaling group {scaling_group_id}: {healthy_count}/{status['desired_instances']} instances (min:{status['min_instances']}, max:{status['max_instances']})")
            return healthy_count
            
        except Exception as e:
            self.logger.error(f"Failed to get healthy instance count: {e}")
            return 0
    
    def modify_scaling_group_capacity(self, scaling_group_id: str, 
                                    desired_capacity: Optional[int] = None,
                                    min_size: Optional[int] = None,
                                    max_size: Optional[int] = None) -> Dict[str, Any]:
        """
        Modify the capacity settings of a scaling group.
        
        Args:
            scaling_group_id: The ID of the scaling group
            desired_capacity: New desired capacity
            min_size: New minimum size
            max_size: New maximum size
            
        Returns:
            Response from the API
        """
        try:
            # First get current settings
            current_status = self.get_scaling_group_status(scaling_group_id)
            
            # Prepare the modification parameters
            query_params = {
                "ScalingGroupId": scaling_group_id
            }
            
            if desired_capacity is not None:
                query_params["DesireInstanceNumber"] = desired_capacity
                self.logger.info(f"Setting desired capacity to {desired_capacity}")
            
            if min_size is not None:
                query_params["MinInstanceNumber"] = min_size
                self.logger.info(f"Setting minimum size to {min_size}")
            
            if max_size is not None:
                query_params["MaxInstanceNumber"] = max_size
                self.logger.info(f"Setting maximum size to {max_size}")
            
            # Validate the parameters
            final_min = min_size if min_size is not None else current_status['min_instances']
            final_max = max_size if max_size is not None else current_status['max_instances']
            final_desired = desired_capacity if desired_capacity is not None else current_status['desired_instances']
            
            if final_desired < final_min or final_desired > final_max:
                raise ValueError(f"Desired capacity {final_desired} must be between min {final_min} and max {final_max}")
            
            response = self.api_client.make_json_request(
                method="GET",
                service=self.service,
                version=self.version,
                action="ModifyScalingGroup",
                query_params=query_params
            )
            
            self.logger.info("Successfully modified scaling group capacity")
            return response
            
        except Exception as e:
            self.logger.error(f"Failed to modify scaling group capacity: {e}")
            raise
    
    def scale_out(self, scaling_group_id: str, increment: int = 1) -> Dict[str, Any]:
        """
        Scale out the scaling group by increasing desired capacity.
        
        Args:
            scaling_group_id: The ID of the scaling group
            increment: Number of instances to add
            
        Returns:
            Response from the API
        """
        try:
            current_status = self.get_scaling_group_status(scaling_group_id)
            current_desired = current_status['desired_instances']
            max_instances = current_status['max_instances']
            
            new_desired = min(current_desired + increment, max_instances)
            
            if new_desired == current_desired:
                self.logger.warning(f"Cannot scale out: already at maximum capacity ({max_instances})")
                return {"status": "no_change", "reason": "at_maximum_capacity"}
            
            self.logger.info(f"Scaling out from {current_desired} to {new_desired} instances")
            
            return self.modify_scaling_group_capacity(
                scaling_group_id=scaling_group_id,
                desired_capacity=new_desired
            )
            
        except Exception as e:
            self.logger.error(f"Failed to scale out: {e}")
            raise
    
    def scale_in(self, scaling_group_id: str, decrement: int = 1) -> Dict[str, Any]:
        """
        Scale in the scaling group by decreasing desired capacity.
        
        Args:
            scaling_group_id: The ID of the scaling group
            decrement: Number of instances to remove
            
        Returns:
            Response from the API
        """
        try:
            current_status = self.get_scaling_group_status(scaling_group_id)
            current_desired = current_status['desired_instances']
            min_instances = current_status['min_instances']
            
            new_desired = max(current_desired - decrement, min_instances)
            
            if new_desired == current_desired:
                self.logger.warning(f"Cannot scale in: already at minimum capacity ({min_instances})")
                return {"status": "no_change", "reason": "at_minimum_capacity"}
            
            self.logger.info(f"Scaling in from {current_desired} to {new_desired} instances")
            
            return self.modify_scaling_group_capacity(
                scaling_group_id=scaling_group_id,
                desired_capacity=new_desired
            )
            
        except Exception as e:
            self.logger.error(f"Failed to scale in: {e}")
            raise
    
    def get_scaling_activities(self, scaling_group_id: str, 
                              start_time: Optional[datetime] = None,
                              end_time: Optional[datetime] = None,
                              page_size: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent scaling activities for a scaling group.
        
        Args:
            scaling_group_id: The ID of the scaling group
            start_time: Start time for activity query
            end_time: End time for activity query
            page_size: Number of activities to retrieve
            
        Returns:
            List of scaling activities
        """
        try:
            query_params = {
                "ScalingGroupId": scaling_group_id,
                "PageSize": page_size
            }
            
            if start_time:
                query_params["StartTime"] = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            
            if end_time:
                query_params["EndTime"] = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            
            response = self.api_client.make_json_request(
                method="GET",
                service=self.service,
                version=self.version,
                action="DescribeScalingActivities",
                query_params=query_params
            )
            
            if 'Result' in response and 'ScalingActivities' in response['Result']:
                activities = response['Result']['ScalingActivities']
                # Only log if there are recent activities
                if activities:
                    self.logger.info(f"Found {len(activities)} recent scaling activities")
                return activities
            else:
                self.logger.warning(f"No scaling activities found: {response}")
                return []
                
        except Exception as e:
            self.logger.error(f"Failed to get scaling activities: {e}")
            return []
    
    def get_last_scaling_activity(self, scaling_group_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the most recent scaling activity.
        
        Args:
            scaling_group_id: The ID of the scaling group
            
        Returns:
            Most recent scaling activity or None
        """
        try:
            activities = self.get_scaling_activities(scaling_group_id, page_size=1)
            
            if activities:
                last_activity = activities[0]
                self.logger.info(f"Last scaling activity: {last_activity.get('ActivityType')} at {last_activity.get('CreatedAt')}")
                return last_activity
            else:
                self.logger.info("No scaling activities found")
                return None
                
        except Exception as e:
            self.logger.error(f"Failed to get last scaling activity: {e}")
            return None
    
    def is_scaling_in_progress(self, scaling_group_id: str) -> bool:
        """
        Check if there's currently a scaling activity in progress.
        
        Args:
            scaling_group_id: The ID of the scaling group
            
        Returns:
            True if scaling is in progress, False otherwise
        """
        try:
            last_activity = self.get_last_scaling_activity(scaling_group_id)
            
            if last_activity:
                status = last_activity.get('StatusCode')
                if status in ['Init', 'Running']:
                    self.logger.info(f"Scaling activity in progress: {status}")
                    return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Failed to check scaling progress: {e}")
            return False