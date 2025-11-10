#!/usr/bin/env python3
"""
Byteplus ALB QPS-based AutoScaling FaaS Function

This function monitors ALB QPS metrics and automatically scales an AutoScaling Group
based on configurable thresholds and cooldown periods.

Author: AI Assistant
Version: 1.0
"""

# from dotenv import load_dotenv
# load_dotenv()

import json
import logging
import time
import traceback
from typing import Dict, Any, Optional
from datetime import datetime, timezone

# Import our custom modules
from config import ScalingConfig, setup_logging, load_config
from byteplus_api_client import BytePlusAPIClient
from cloudmonitor_client import CloudMonitorClient
from autoscaling_client import AutoScalingClient
from state_manager import StateManager
from scaling_engine import ScalingEngine
from db_manager import DBManager
from concurrent.futures import ThreadPoolExecutor, as_completed


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main FaaS handler function for ALB QPS-based autoscaling.
    
    Args:
        event: Event data from the FaaS trigger
        context: Runtime context information
        
    Returns:
        Dictionary containing execution result
    """
    # Ensure a logger is available even if configuration fails later
    logger = logging.getLogger(__name__)

    # Load configuration first to get the initial delay
    config = None
    try:
        config = load_config()
        
        # Apply initial delay for staggered execution (if configured)
        if config.initial_delay_seconds > 0:
            print(f"Applying initial delay of {config.initial_delay_seconds} seconds for staggered execution...")
            time.sleep(config.initial_delay_seconds)
            
    except Exception as e:
        # If config loading fails, continue without delay but log the error
        print(f"Warning: Could not load config for initial delay: {e}")
    
    execution_start = datetime.now(timezone.utc)
    
    # Initialize response structure
    response = {
        "statusCode": 200,
        "timestamp": execution_start.isoformat(),
        "execution_id": getattr(context, 'request_id', 'unknown'),
        "version": "1.0",
        "result": {
            "action": "none",
            "status": "success",
            "message": "No action needed"
        },
        "metrics": {},
        "execution_time_ms": 0,
        "error": None
    }
    
    try:
        # Step 1: Load and validate configuration (reuse if already loaded)
        if config is None:
            config = load_config()
        setup_logging(config)
        logger = logging.getLogger(__name__)
        
        logger.info(f"Starting autoscaling evaluation - Execution ID: {response['execution_id']}")
        logger.info(f"Configuration: ALB={config.alb_id}, ASG={config.autoscaling_group_id}, DryRun={config.dry_run_mode}")
        
        # Step 2: Initialize clients and components
        api_client = BytePlusAPIClient(
            access_key=config.access_key_id,
            secret_key=config.secret_access_key,
            region=config.region
        )
        
        cloudmonitor_client = CloudMonitorClient(api_client)
        autoscaling_client = AutoScalingClient(api_client)
        db_manager = DBManager(dsn=config.db_dsn)
        state_manager = StateManager(db_manager)

        # Step 3: Handle different event types
        event_type = event.get('type', 'scaling_evaluation')

        if event_type == 'validation':
            # Validation might need to be re-thought in a multi-group context
            # For now, we can validate the first group found or a specific one if provided
            result = handle_validation_for_all(db_manager, config, state_manager, cloudmonitor_client, autoscaling_client)
        elif event_type == 'status':
            result = handle_status_check_for_all(db_manager, config, state_manager, cloudmonitor_client, autoscaling_client)
        elif event_type in ['scaling_evaluation', 'faas.timer.event']:
            result = handle_batch_scaling_evaluation(db_manager, config, state_manager, cloudmonitor_client, autoscaling_client)
        else:
            result = {
                "action": "error",
                "status": "error",
                "message": f"Unknown event type: {event_type}"
            }
        
        response["result"] = result
        
        # Step 4: Log execution summary
        execution_end = datetime.now(timezone.utc)
        execution_time = (execution_end - execution_start).total_seconds() * 1000
        response["execution_time_ms"] = round(execution_time, 2)
        
        logger.info(f"Execution completed - Action: {result.get('action', 'unknown')}, Status: {result.get('status', 'unknown')}, Message: {result.get('message', 'unknown')}, Time: {execution_time:.2f}ms")
        
        # Step 5: Send alerts if configured
        if config.alert_webhook_url and result.get('action') not in ['none', 'status', 'validation']:
            try:
                send_alert(config.alert_webhook_url, result, config)
            except Exception as e:
                logger.warning(f"Failed to send alert: {e}")
        
        # Print detailed execution result
        print("\n=== Autoscaling Function Execution Result ===")
        print(json.dumps(response, indent=2, ensure_ascii=False))
        print("\n=== Execution Completed ===")

        return response

    except Exception as e:
        # Handle any unexpected errors
        error_msg = f"Unexpected error in handler: {str(e)}"
        logger.error(error_msg)
        logger.error(f"Traceback: {traceback.format_exc()}")

        response["statusCode"] = 500
        response["result"] = {
            "action": "error",
            "status": "error",
            "message": error_msg
        }
        response["error"] = {
            "type": type(e).__name__,
            "message": str(e),
            "traceback": traceback.format_exc()
        }

        # Try to record error in state if possible
        try:
            if 'state_manager' in locals():
                state_manager.record_error(
                    group_id=0,  # General error not specific to a group
                    source="handler",
                    message=error_msg,
                    context={"event": event, "traceback": traceback.format_exc()}
                )
        except:
            pass  # Don't let error recording cause additional failures

        # Print result
        print("\n=== Autoscaling Function Execution Result ===")
        print(json.dumps(response, indent=2, ensure_ascii=False))
        print("\n=== Execution Completed ===")

        return response


def handle_batch_scaling_evaluation(db_manager: DBManager, config: ScalingConfig, state_manager: StateManager, cloudmonitor_client: CloudMonitorClient, autoscaling_client: AutoScalingClient) -> Dict[str, Any]:
    """
    Handles scaling evaluation for all enabled resource groups concurrently.
    """
    logger = logging.getLogger(__name__)
    logger.info("Starting batch scaling evaluation for all enabled resource groups.")

    try:
        resource_groups = db_manager.get_enabled_resource_groups()
        if not resource_groups:
            return {"action": "none", "status": "success", "message": "No enabled resource groups found."}

        alb_ids = [rg['alb_id'] for rg in resource_groups]
        avg_qps_map = cloudmonitor_client.get_average_qps_batch(alb_ids, period_seconds=config.metric_period)

        results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_group = {executor.submit(evaluate_single_group, rg, avg_qps_map.get(rg['alb_id']), config, state_manager, cloudmonitor_client, autoscaling_client): rg for rg in resource_groups}
            for future in as_completed(future_to_group):
                group = future_to_group[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as exc:
                    logger.error(f"Error processing resource group {group['id']}: {exc}")
                    results.append({"group_id": group['id'], "action": "error", "status": "error", "message": str(exc)})

        return {"action": "batch_evaluation", "status": "success", "results": results}

    except Exception as e:
        logger.error(f"Failed during batch scaling evaluation: {e}")
        return {"action": "error", "status": "error", "message": str(e)}

def evaluate_single_group(group: Dict[str, Any], current_qps: Optional[float], config: ScalingConfig, state_manager: StateManager, cloudmonitor_client: CloudMonitorClient, autoscaling_client: AutoScalingClient) -> Dict[str, Any]:
    """
    Evaluates scaling for a single resource group.
    """
    # Create a new ScalingEngine for each group to isolate state and config
    group_config = config.copy_with_group(group)
    engine = ScalingEngine(
        config=group_config,
        state_manager=state_manager, # State manager can be shared if it handles state per group
        cloudmonitor_client=cloudmonitor_client,
        autoscaling_client=autoscaling_client
    )
    return engine.evaluate_scaling_decision(prefetched_qps=current_qps)


def handle_scaling_evaluation(scaling_engine: ScalingEngine) -> Dict[str, Any]:
    """
    Handle the main scaling evaluation logic.
    
    Args:
        scaling_engine: Configured scaling engine instance
        
    Returns:
        Scaling evaluation result
    """
    logger = logging.getLogger(__name__)
    
    try:
        logger.info("Starting scaling evaluation")
        
        # Perform scaling evaluation
        decision_result = scaling_engine.evaluate_scaling_decision()
        
        # Format response
        result = {
            "action": decision_result.get("action", "none"),
            "status": "success" if not decision_result.get("error") else "error",
            "message": decision_result.get("reason", "Evaluation completed"),
            "details": {
                "current_qps": decision_result.get("current_qps"),
                "current_instances": decision_result.get("current_instances"),
                "qps_per_instance": decision_result.get("qps_per_instance"),
                "target_qps_per_instance": decision_result.get("target_qps_per_instance"),
                "scaling_amount": decision_result.get("scaling_amount"),
                "dry_run": decision_result.get("dry_run", False),
                "execution_result": decision_result.get("execution_result")
            }
        }
        
        if decision_result.get("error"):
            result["error"] = decision_result["error"]
        
        if decision_result.get("cooldown_remaining"):
            result["details"]["cooldown_remaining_seconds"] = decision_result["cooldown_remaining"]
        
        # Add dynamic scaling specific fields if available
        if decision_result.get("optimal_instances") is not None:
            result["details"]["optimal_instances"] = decision_result["optimal_instances"]
        if decision_result.get("required_change") is not None:
            result["details"]["required_change"] = decision_result["required_change"]
        if decision_result.get("limited_by_safety") is not None:
            result["details"]["limited_by_safety"] = decision_result["limited_by_safety"]
        
        logger.info(f"Scaling evaluation completed: {result['action']} - {result['message']}")
        return result
        
    except Exception as e:
        error_msg = f"Scaling evaluation failed: {str(e)}"
        logger.error(error_msg)
        return {
            "action": "error",
            "status": "error",
            "message": error_msg,
            "error": str(e)
        }


def handle_status_check_for_all(db_manager: DBManager, config: ScalingConfig, state_manager: StateManager, cloudmonitor_client: CloudMonitorClient, autoscaling_client: AutoScalingClient) -> Dict[str, Any]:
    return {"status": "success", "message": "Status check for all groups not fully implemented yet."}


def handle_validation_for_all(db_manager: DBManager, config: ScalingConfig, state_manager: StateManager, cloudmonitor_client: CloudMonitorClient, autoscaling_client: AutoScalingClient) -> Dict[str, Any]:
    return {"status": "success", "message": "Validation for all groups not fully implemented yet."}


def handle_status_check(scaling_engine: ScalingEngine) -> Dict[str, Any]:
    """
    Handle a status check for the configured scaling group.
    
    Args:
        scaling_engine: Configured scaling engine instance
        
    Returns:
        Current system status
    """
    logger = logging.getLogger(__name__)
    
    try:
        logger.info("Performing status check")
        
        status = scaling_engine.get_current_status()
        
        return {
            "action": "status",
            "status": "success",
            "message": "Status check completed",
            "details": status
        }
        
    except Exception as e:
        error_msg = f"Status check failed: {str(e)}"
        logger.error(error_msg)
        return {
            "action": "status",
            "status": "error",
            "message": error_msg,
            "error": str(e)
        }


def handle_validation(scaling_engine: ScalingEngine) -> Dict[str, Any]:
    """
    Handle configuration validation requests.
    
    Args:
        scaling_engine: Configured scaling engine instance
        
    Returns:
        Validation result
    """
    logger = logging.getLogger(__name__)
    
    try:
        logger.info("Performing configuration validation")
        
        validation_result = scaling_engine.validate_configuration()
        
        return {
            "action": "validation",
            "status": validation_result.get("overall_status", "error"),
            "message": "Validation completed",
            "details": validation_result
        }
        
    except Exception as e:
        error_msg = f"Validation failed: {str(e)}"
        logger.error(error_msg)
        return {
            "action": "validation",
            "status": "error",
            "message": error_msg,
            "error": str(e)
        }


def send_alert(webhook_url: str, result: Dict[str, Any], config: ScalingConfig) -> None:
    """
    Send alert notification via webhook.
    
    Args:
        webhook_url: Webhook URL for alerts
        result: Scaling result to include in alert
        config: Configuration object
    """
    import requests
    
    logger = logging.getLogger(__name__)
    
    try:
        alert_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": "byteplus-alb-autoscaling",
            "alb_id": config.alb_id,
            "autoscaling_group_id": config.autoscaling_group_id,
            "action": result.get("action"),
            "status": result.get("status"),
            "message": result.get("message"),
            "details": result.get("details", {}),
            "dry_run": config.dry_run_mode
        }
        
        response = requests.post(
            webhook_url,
            json=alert_payload,
            timeout=10,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code == 200:
            logger.info("Alert sent successfully")
        else:
            logger.warning(f"Alert webhook returned status {response.status_code}")
            
    except Exception as e:
        logger.error(f"Failed to send alert: {e}")
        raise


def main():
    """
    Main function for local testing.
    """
    # Simulate FaaS event and context for local testing
    class MockContext:
        def __init__(self):
            self.request_id = "local-test-" + str(int(datetime.now().timestamp()))
    
    # Test event
    test_event = {
        "type": "status",
        # "type": "validation",
        # "type": "scaling_evaluation",
        "source": "local-test"
    }
    
    # Run the handler
    result = handler(test_event, MockContext())
    
    # Print result
    print("\n=== Autoscaling Function Test Result ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("\n=== Test Completed ===")


if __name__ == "__main__":
    main()
