import time
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
from byteplus_api_client import BytePlusAPIClient


class CloudMonitorClient:
    """
    BytePlus CloudMonitor client for fetching ALB metrics.
    """
    
    def __init__(self, api_client: BytePlusAPIClient):
        self.api_client = api_client
        self.logger = logging.getLogger(__name__)
        self.service = "volc_observe"
        self.version = "2018-01-01"
    
    def get_alb_qps_metrics(self, alb_id: str, 
                           start_time: Optional[datetime] = None,
                           end_time: Optional[datetime] = None,
                           period: Optional[str] = None) -> Dict[str, Any]:
        """
        Get ALB QPS metrics from CloudMonitor.
        
        Args:
            alb_id: ALB resource ID
            start_time: Start time for metrics (defaults to 10 minutes ago)
            end_time: End time for metrics (defaults to now)
            period: Aggregation period (e.g., '1m', '5m', '1h')
            
        Returns:
            Dictionary containing QPS metrics data
        """
        if end_time is None:
            end_time = datetime.now(timezone.utc)
        if start_time is None:
            start_time = end_time - timedelta(minutes=10)
        
        # Calculate appropriate period if not provided
        if period is None:
            time_range_seconds = (end_time - start_time).total_seconds()
            time_range_minutes = time_range_seconds / 60
            
            if time_range_seconds <= 30:
                period = "15s"  # For very short ranges (≤30s), use 15-second intervals
            elif time_range_seconds <= 120:
                period = "30s"  # For ranges ≤2 minutes, use 30-second intervals
            elif time_range_minutes <= 10:
                period = "1m"   # For ranges up to 10 minutes, use 1-minute intervals
            elif time_range_minutes <= 60:
                period = "5m"   # For ranges up to 1 hour, use 5-minute intervals
            else:
                period = "5m"   # For longer ranges, use 5-minute intervals
        
        # Convert to Unix timestamps
        start_timestamp = int(start_time.timestamp())
        end_timestamp = int(end_time.timestamp())
        
        # Prepare request body according to the API documentation
        request_body = {
            "MetricName": "load_balancer_qps",
            "StartTime": start_timestamp,
            "EndTime": end_timestamp,
            "Namespace": "VCM_ALB",
            "Instances": [
                {
                    "Dimensions": [
                        {
                            "Name": "ResourceID",
                            "Value": alb_id
                        }
                    ]
                }
            ],
            "GroupBy": [],
            "SubNamespace": "loadbalancer",
            "Region": self.api_client.region,
            "Period": period
        }
        
        try:
            time_range_minutes = (end_time - start_time).total_seconds() / 60
            self.logger.info(f"Fetching ALB QPS metrics for {alb_id} from {start_time} to {end_time} (range: {time_range_minutes:.1f}m, period: {period})")
            
            response = self.api_client.make_json_request(
                method="POST",
                service=self.service,
                version=self.version,
                action="GetMetricData",
                json_body=request_body
            )
            
            self.logger.debug(f"CloudMonitor response: {response}")
            return response
            
        except Exception as e:
            self.logger.error(f"Failed to fetch ALB QPS metrics: {e}")
            raise
    
    def get_latest_qps(self, alb_id: str, period_minutes: int = 5) -> Optional[float]:
        """
        Get the latest QPS value for an ALB.
        
        Args:
            alb_id: ALB resource ID
            period_minutes: Period to look back for metrics
            
        Returns:
            Latest QPS value or None if no data available
        """
        try:
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(minutes=period_minutes)
            
            metrics_data = self.get_alb_qps_metrics(alb_id, start_time, end_time)
            
            # Debug: Print full metrics_data response for troubleshooting
            self.logger.info(f"Full metrics_data response for ALB {alb_id} (latest): {metrics_data}")
            
            # Parse the response to extract QPS value
            if 'Result' in metrics_data and 'Data' in metrics_data['Result']:
                data_response = metrics_data['Result']['Data']
                
                # Extract data points from the nested structure
                if 'MetricDataResults' in data_response and data_response['MetricDataResults']:
                    metric_results = data_response['MetricDataResults'][0]  # Get first metric result
                    if 'DataPoints' in metric_results and metric_results['DataPoints']:
                        data_points = metric_results['DataPoints']
                        self.logger.info(f"Found {len(data_points)} data points (latest): {data_points}")
                        # Get the most recent data point
                        latest_point = max(data_points, key=lambda x: x.get('Timestamp', 0))
                        qps_value = latest_point.get('Value', 0)
                        
                        self.logger.info(f"Latest QPS for ALB {alb_id}: {qps_value}")
                        return float(qps_value)
                    else:
                        self.logger.warning(f"No data points found in metric results for ALB {alb_id}")
                        self.logger.warning(f"Metric results content (latest): {metric_results}")
                        return None
                else:
                    self.logger.warning(f"No metric data results found for ALB {alb_id}")
                    self.logger.warning(f"Data response content (latest): {data_response}")
                    return None
            else:
                self.logger.warning(f"Invalid response format for ALB QPS metrics")
                self.logger.warning(f"Full metrics_data (latest): {metrics_data}")
                return None
                
        except Exception as e:
            self.logger.error(f"Failed to get latest QPS for ALB {alb_id}: {e}")
            return None
    
    def get_average_qps(self, alb_id: str, period_seconds: int = 600) -> Optional[float]:
        """
        Get the average QPS over a specified period.
        
        Args:
            alb_id: ALB resource ID
            period_seconds: Period to calculate average over (in seconds)
            
        Returns:
            Average QPS value or None if no data available
        """
        try:
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(seconds=period_seconds)
            period_description = f"{period_seconds}s"
            
            # Call get_alb_qps_metrics without specifying period to let it auto-calculate
            # based on the time range (this will trigger the granular period logic)
            metrics_data = self.get_alb_qps_metrics(alb_id, start_time, end_time)
            
            # Debug: Print full metrics_data response for troubleshooting
            self.logger.info(f"Full metrics_data response for ALB {alb_id} (period: {period_description}): {metrics_data}")
            
            # Parse the response to calculate average
            if 'Result' in metrics_data and 'Data' in metrics_data['Result']:
                data_response = metrics_data['Result']['Data']
                
                self.logger.debug(f"CloudMonitor response: {data_response}")
                
                # Extract data points from the nested structure
                if 'MetricDataResults' in data_response and data_response['MetricDataResults']:
                    metric_results = data_response['MetricDataResults'][0]  # Get first metric result
                    self.logger.info(f"Metric results structure: {metric_results}")
                    
                    if 'DataPoints' in metric_results and metric_results['DataPoints']:
                        data_points = metric_results['DataPoints']
                        self.logger.info(f"Found {len(data_points)} data points: {data_points}")
                        qps_values = [float(point.get('Value', 0)) for point in data_points]
                        average_qps = sum(qps_values) / len(qps_values)
                        
                        self.logger.info(f"Average QPS for ALB {alb_id} over {period_description}: {average_qps:.2f}")
                        return average_qps
                    else:
                        self.logger.warning(f"No data points found in metric results for ALB {alb_id}")
                        self.logger.warning(f"Metric results content: {metric_results}")
                        return None
                else:
                    self.logger.warning(f"No metric data results found for ALB {alb_id}")
                    self.logger.warning(f"Data response content: {data_response}")
                    return None
            else:
                self.logger.warning(f"Invalid response format for ALB QPS metrics")
                self.logger.warning(f"Full metrics_data: {metrics_data}")
                return None
                
        except Exception as e:
            self.logger.error(f"Failed to get average QPS for ALB {alb_id}: {e}")
            return None
    
    def check_metric_availability(self, alb_id: str) -> bool:
        """
        Check if metrics are available for the specified ALB.
        
        Args:
            alb_id: ALB resource ID
            
        Returns:
            True if metrics are available, False otherwise
        """
        try:
            # Try to get metrics for the last 5 minutes
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(minutes=5)
            
            metrics_data = self.get_alb_qps_metrics(alb_id, start_time, end_time)
            
            # Check if we got valid data
            if 'Result' in metrics_data:
                self.logger.info(f"Metrics are available for ALB {alb_id}")
                return True
            else:
                self.logger.warning(f"No metrics available for ALB {alb_id}")
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to check metric availability for ALB {alb_id}: {e}")
            return False
    
    def get_qps_trend(self, alb_id: str, period_minutes: int = 30) -> Dict[str, Any]:
        """
        Get QPS trend analysis over a specified period.
        
        Args:
            alb_id: ALB resource ID
            period_minutes: Period to analyze trend over
            
        Returns:
            Dictionary containing trend analysis
        """
        try:
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(minutes=period_minutes)
            
            metrics_data = self.get_alb_qps_metrics(alb_id, start_time, end_time)
            
            if 'Result' in metrics_data and 'Data' in metrics_data['Result']:
                data_points = metrics_data['Result']['Data']
                
                if len(data_points) >= 2:
                    qps_values = [float(point.get('Value', 0)) for point in data_points]
                    timestamps = [point.get('Timestamp', 0) for point in data_points]
                    
                    # Sort by timestamp
                    sorted_data = sorted(zip(timestamps, qps_values))
                    sorted_qps = [qps for _, qps in sorted_data]
                    
                    # Calculate trend metrics
                    current_qps = sorted_qps[-1]
                    previous_qps = sorted_qps[0]
                    max_qps = max(sorted_qps)
                    min_qps = min(sorted_qps)
                    avg_qps = sum(sorted_qps) / len(sorted_qps)
                    
                    # Calculate trend direction
                    if len(sorted_qps) >= 3:
                        recent_avg = sum(sorted_qps[-3:]) / 3
                        earlier_avg = sum(sorted_qps[:3]) / 3
                        trend = "increasing" if recent_avg > earlier_avg else "decreasing" if recent_avg < earlier_avg else "stable"
                    else:
                        trend = "increasing" if current_qps > previous_qps else "decreasing" if current_qps < previous_qps else "stable"
                    
                    trend_analysis = {
                        "current_qps": current_qps,
                        "average_qps": avg_qps,
                        "max_qps": max_qps,
                        "min_qps": min_qps,
                        "trend": trend,
                        "data_points_count": len(sorted_qps),
                        "period_minutes": period_minutes
                    }
                    
                    self.logger.info(f"QPS trend for ALB {alb_id}: {trend_analysis}")
                    return trend_analysis
                else:
                    self.logger.warning(f"Insufficient data points for trend analysis: {len(data_points)}")
                    return {"error": "Insufficient data for trend analysis"}
            else:
                self.logger.warning(f"No trend data available for ALB {alb_id}")
                return {"error": "No data available"}
                
        except Exception as e:
            self.logger.error(f"Failed to get QPS trend for ALB {alb_id}: {e}")
            return {"error": str(e)}