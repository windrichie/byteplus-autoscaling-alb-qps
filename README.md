# BytePlus ALB QPS-based AutoScaling Solution

A FaaS-based solution that automatically scales BytePlus AutoScaling Groups based on Application Load Balancer (ALB) QPS metrics.

## Overview

This solution provides QPS-based scaling for GPU instances. Instead of relying on traditional CPU/GPU/Memory utilization metrics, it uses ALB request-per-second data to make scaling decisions. The QPS per instance is calculated by dividing the ALB QPS by the number of instances in the AutoScaling Group.

## ⚠️ Important Note

**This solution only works for simple 1:1:1 configurations:**
- **1 Server Group** per ALB
- **1 ALB** per AutoScaling Group  
- **1 AutoScaling Group** being managed

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   ALB           │    │  CloudMonitor    │    │  AutoScaling    │
│  (Load Balancer)│    │   (Metrics)      │    │    Group        │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌─────────────────────────┐
                    │     FaaS Function       │
                    │  (Autoscaling Logic)    │
                    └─────────────────────────┘
                                 │
                    ┌─────────────────────────┐
                    │   TOS Object Storage    │
                    │   (State Persistence)   │
                    └─────────────────────────┘
```

## Features

- **QPS-based Scaling**: Scale based on actual request load rather than resource utilization
- **Configurable Thresholds**: Customizable scale-up/scale-down thresholds with hysteresis
- **Cooldown Management**: Prevents oscillation with configurable cooldown periods
- **State Persistence**: Uses TOS mount for reliable state management
- **Safety Mechanisms**: Min/max limits automatically enforced from AutoScaling Group configuration
- **Comprehensive Logging**: Detailed logging and audit trail
- **Dry Run Mode**: Test scaling decisions without actual execution
- **Error Handling**: Robust error handling and validation

## Files Structure

```
autoscaling-alb-solution/
├── main.py                    # Main FaaS handler function
├── config.py                  # Configuration management
├── byteplus_api_client.py     # BytePlus API client with signing
├── cloudmonitor_client.py     # CloudMonitor API integration
├── autoscaling_client.py      # AutoScaling API integration
├── state_manager.py           # State persistence using TOS
├── scaling_engine.py          # Core scaling decision logic
├── requirements.txt           # Python dependencies
├── README.md                  # This file
└── .env.example              # Environment variables template
```

## Configuration

### Required Environment Variables

```bash
# BytePlus Cloud Configuration (Required)
AUTOSCALING_GROUP_ID=asg-xxxxx
ALB_ID=alb-xxxxx
ACCESS_KEY_ID=your_access_key
SECRET_ACCESS_KEY=your_secret_key

# Core Scaling Parameters (Required)
TARGET_QPS_PER_INSTANCE=50
SCALE_UP_THRESHOLD=0.8
SCALE_DOWN_THRESHOLD=0.6

# Scaling Behavior (Required - must be > 0)
SCALE_UP_INCREMENT=1
SCALE_DOWN_DECREMENT=1

# Metric Collection (Required - must be > 0)
METRIC_PERIOD=300
```

**Note**: Instance limits (min/max/desired) are automatically fetched from the AutoScaling Group configuration at runtime.

### Optional Configuration (with defaults)

```bash
# BytePlus Cloud Configuration
REGION=ap-southeast-1                    # Default: ap-southeast-1

# Cooldown Periods (seconds)
SCALE_UP_COOLDOWN=300                    # Default: 300 (5 minutes)
SCALE_DOWN_COOLDOWN=600                  # Default: 600 (10 minutes)
GENERAL_COOLDOWN=180                     # Default: 180 (3 minutes)

# Storage Configuration
TOS_MOUNT_PATH=/tosmount                 # Default: /tosmount
TOS_STATE_FILE=scaling_state.json        # Default: scaling_state.json

# Safety & Monitoring
DRY_RUN_MODE=false                       # Default: false
ALERT_WEBHOOK_URL=                       # Default: empty (no alerts)

# Function Behavior
LOG_LEVEL=INFO                           # Default: INFO
ENABLE_DETAILED_LOGGING=false            # Default: false
```

## Metric Period Configuration

The `METRIC_PERIOD` setting controls how far back the function looks when collecting QPS metrics from the ALB. This is a critical parameter that affects scaling responsiveness and system stability.

### **Recommended Values:**

```bash
# Production (Recommended)
METRIC_PERIOD=300    # 5 minutes - Stable, cost-effective, filters noise

# Responsive (Good for dynamic workloads)
METRIC_PERIOD=60     # 1 minute - Balanced responsiveness and stability

# Aggressive (Use with caution)
METRIC_PERIOD=30     # 30 seconds - Very responsive, monitor for issues
```

### **Technical Notes:**

- **Minimum supported**: 10 seconds (with warnings)
- **API Optimization**: The system automatically selects appropriate CloudMonitor intervals:
  - ≤30s range → 10s intervals
  - ≤2min range → 30s intervals  
  - ≤10min range → 1min intervals
  - >10min range → 5min intervals
- **Rate Limiting**: Very short periods may hit BytePlus CloudMonitor API limits
- **Data Availability**: ALB metrics may not be available at sub-minute granularity

### **Warning System:**

The configuration validation will warn you about potentially problematic settings:
- `METRIC_PERIOD < 30`: Strong warning about instability risks
- `METRIC_PERIOD < 60`: Informational notice to monitor for issues

## Prerequisites

Before deploying this autoscaling solution, ensure you have the following BytePlus resources already created:

### Required Resources

1. **AutoScaling Group (ASG)**
   - Configured with Launch Configuration using **L20 or H20 GPU instances**
   - Set appropriate min/max capacity limits
   - Ensure the ASG is in "Active" state

2. **Application Load Balancer (ALB)**
   - Configured with appropriate listener configurations
   - Connected to the target server group that corresponds to your ASG
   - Generating traffic metrics (QPS data)

3. **TOS Bucket** (for state persistence)
   - Bucket for storing autoscaling state
   - Appropriate read/write permissions

4. **BytePlus Access Credentials**
   - Access Key ID and Secret Access Key with permissions for:
     - CloudMonitor: `GetMetricData`
     - AutoScaling: `DescribeScalingGroups`, `ModifyScalingGroup`, `DescribeScalingActivities`
     - TOS: Read/Write access to the state bucket

**Important**: Ensure your ALB → Server Group → ASG configuration follows the 1:1:1 relationship mentioned in the limitation section above.

## Deployment

### 1. Prepare the Function Package

```bash
# Create deployment package
./deploy.sh
```

### 2. Deploy to BytePlus FaaS

1. Create a new FaaS function in BytePlus Console
2. Upload the function package
3. Configure environment variables
4. Set up TOS mount for state persistence
5. Configure trigger (timer trigger for periodic execution)

### 3. Configure TOS Mount

1. Create a TOS bucket for state storage
2. Mount the bucket to `/tosmount` in the FaaS function
3. Ensure the function has read/write permissions

### 4. Set Up Permissions

Ensure the AK/SK configured in the function has the following permissions:
- CloudMonitor: `GetMetricData`
- AutoScaling: `DescribeScalingGroups`, `ModifyScalingGroup`, `DescribeScalingActivities`
- TOS: Read/Write access to the state bucket

## Monitoring

### Function Response Format

```json
{
  "statusCode": 200,
  "timestamp": "2024-01-15T10:30:00Z",
  "execution_id": "req-12345",
  "version": "1.0",
  "result": {
    "action": "scale_up",
    "status": "success",
    "message": "qps_above_threshold",
    "details": {
      "current_qps": 120.5,
      "current_instances": 2,
      "qps_per_instance": 60.25,
      "target_qps_per_instance": 50,
      "scale_up_threshold": 40,
      "scale_down_threshold": 30,
      "dry_run": false
    }
  },
  "execution_time_ms": 1250
}
```

### Key Metrics to Monitor

- Function execution success rate
- Scaling action frequency
- QPS per instance trends
- Error rates and types
- Cooldown violations