import os
import sys
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class ScalingConfig:
    """
    Configuration class for autoscaling parameters.
    All values can be set via environment variables.
    """
    
    # Core Scaling Parameters
    target_qps_per_instance: float = 50.0
    scale_up_threshold: float = 0.8  # Scale up when at 80% of target
    scale_down_threshold: float = 0.6  # Scale down when below 60% of target
    
    # Instance Limits (fetched from ASG at runtime)
    # min_instances, max_instances, desired_instances removed - using ASG as source of truth
    
    # Scaling Behavior
    scale_up_increment: int = 1
    scale_down_decrement: int = 1
    
    # Dynamic Scaling Configuration
    enable_dynamic_scaling: bool = True
    max_scale_up_per_action: int = 0  # 0 = no limit, rely on ASG max
    max_scale_down_per_action: int = 0  # 0 = no limit, rely on ASG min
    
    # Cooldown Periods (seconds)
    scale_up_cooldown: int = 300  # 5 minutes
    scale_down_cooldown: int = 600  # 10 minutes
    general_cooldown: int = 180  # 3 minutes
    
    # Metric Collection
    metric_period: int = 300  # 5 minutes
    
    # BytePlus Cloud Configuration
    autoscaling_group_id: str = ""
    alb_id: str = ""
    region: str = "ap-southeast-1"
    
    # Storage Configuration
    tos_mount_path: str = "/tosmount"
    tos_state_file: str = "scaling_state.json"
    
    # API Configuration
    access_key_id: str = ""
    secret_access_key: str = ""
    
    # Safety & Monitoring
    dry_run_mode: bool = False
    alert_webhook_url: str = ""
    
    # Function Behavior
    log_level: str = "INFO"
    enable_detailed_logging: bool = False
    initial_delay_seconds: int = 0  # Sleep delay at function start (for staggered execution)
    
    @classmethod
    def from_environment(cls) -> 'ScalingConfig':
        """
        Create configuration from environment variables.
        
        Returns:
            ScalingConfig instance with values from environment
        """
        def get_env_float(key: str, default: float) -> float:
            try:
                return float(os.getenv(key, default))
            except (ValueError, TypeError):
                logging.warning(f"Invalid float value for {key}, using default: {default}")
                return default
        
        def get_env_int(key: str, default: int) -> int:
            try:
                return int(os.getenv(key, default))
            except (ValueError, TypeError):
                logging.warning(f"Invalid int value for {key}, using default: {default}")
                return default
        
        def get_env_bool(key: str, default: bool) -> bool:
            value = os.getenv(key, str(default)).lower()
            return value in ('true', '1', 'yes', 'on')
        
        return cls(
            # Core Scaling Parameters
            target_qps_per_instance=get_env_float('TARGET_QPS_PER_INSTANCE', 50.0),
            scale_up_threshold=get_env_float('SCALE_UP_THRESHOLD', 0.8),
            scale_down_threshold=get_env_float('SCALE_DOWN_THRESHOLD', 0.6),
            
            # Instance Limits (fetched from ASG at runtime)
            # min_instances, max_instances, desired_instances removed
            
            # Scaling Behavior
            scale_up_increment=get_env_int('SCALE_UP_INCREMENT', 1),
            scale_down_decrement=get_env_int('SCALE_DOWN_DECREMENT', 1),
            
            # Dynamic Scaling Configuration
            enable_dynamic_scaling=get_env_bool('ENABLE_DYNAMIC_SCALING', True),
            max_scale_up_per_action=get_env_int('MAX_SCALE_UP_PER_ACTION', 0),
            max_scale_down_per_action=get_env_int('MAX_SCALE_DOWN_PER_ACTION', 0),
            
            # Cooldown Periods
            scale_up_cooldown=get_env_int('SCALE_UP_COOLDOWN', 300),
            scale_down_cooldown=get_env_int('SCALE_DOWN_COOLDOWN', 600),
            general_cooldown=get_env_int('GENERAL_COOLDOWN', 180),
            
            # Metric Collection
            metric_period=get_env_int('METRIC_PERIOD', 300),
            
            # BytePlus Cloud Configuration
            autoscaling_group_id=os.getenv('AUTOSCALING_GROUP_ID', ''),
            alb_id=os.getenv('ALB_ID', ''),
            region=os.getenv('REGION', 'ap-southeast-1'),
            
            # Storage Configuration
            tos_mount_path=os.getenv('TOS_MOUNT_PATH', '/tosmount'),
            tos_state_file=os.getenv('TOS_STATE_FILE', 'scaling_state.json'),
            
            # API Configuration
            access_key_id=os.getenv('ACCESS_KEY_ID', ''),
            secret_access_key=os.getenv('SECRET_ACCESS_KEY', ''),
            
            # Safety & Monitoring
            dry_run_mode=get_env_bool('DRY_RUN_MODE', False),
            alert_webhook_url=os.getenv('ALERT_WEBHOOK_URL', ''),
            
            # Function Behavior
            log_level=os.getenv('LOG_LEVEL', 'INFO'),
            enable_detailed_logging=get_env_bool('ENABLE_DETAILED_LOGGING', False),
            initial_delay_seconds=get_env_int('INITIAL_DELAY_SECONDS', 0),
        )
    
    def validate(self) -> None:
        """
        Validate configuration values.
        
        Raises:
            ValueError: If configuration is invalid
        """
        errors = []
        
        # Validate required fields
        if not self.autoscaling_group_id:
            errors.append("AUTOSCALING_GROUP_ID is required")
        
        if not self.alb_id:
            errors.append("ALB_ID is required")
        
        if not self.access_key_id:
            errors.append("ACCESS_KEY_ID is required")
        
        if not self.secret_access_key:
            errors.append("SECRET_ACCESS_KEY is required")
        
        # Validate thresholds
        if not 0 < self.scale_up_threshold <= 1:
            errors.append("SCALE_UP_THRESHOLD must be between 0 and 1")
        
        if not 0 < self.scale_down_threshold <= 1:
            errors.append("SCALE_DOWN_THRESHOLD must be between 0 and 1")
        
        if self.scale_down_threshold >= self.scale_up_threshold:
            errors.append("SCALE_DOWN_THRESHOLD must be less than SCALE_UP_THRESHOLD")
        
        # Instance limits validation removed - using ASG as source of truth
        
        # Validate scaling behavior
        if self.scale_up_increment <= 0:
            errors.append("SCALE_UP_INCREMENT must be > 0")
        
        if self.scale_down_decrement <= 0:
            errors.append("SCALE_DOWN_DECREMENT must be > 0")
        
        # Validate cooldown periods
        if self.scale_up_cooldown < 0:
            errors.append("SCALE_UP_COOLDOWN must be >= 0")
        
        if self.scale_down_cooldown < 0:
            errors.append("SCALE_DOWN_COOLDOWN must be >= 0")
        
        if self.general_cooldown < 0:
            errors.append("GENERAL_COOLDOWN must be >= 0")
        
        # Validate other parameters
        if self.target_qps_per_instance <= 0:
            errors.append("TARGET_QPS_PER_INSTANCE must be > 0")
        
        # Validate metric period with warnings for very short periods
        if self.metric_period <= 0:
            errors.append("METRIC_PERIOD must be > 0")
        elif self.metric_period < 30:
            logger = logging.getLogger(__name__)
            logger.warning(f"METRIC_PERIOD={self.metric_period}s is very short. This may cause:")
            logger.warning("- Insufficient data points from CloudMonitor API")
            logger.warning("- Increased API rate limiting risk")
            logger.warning("- Potential scaling oscillation")
            logger.warning("- Recommended minimum: 60 seconds")
        elif self.metric_period < 60:
            logger = logging.getLogger(__name__)
            logger.info(f"METRIC_PERIOD={self.metric_period}s is short. Monitor for API rate limits and scaling stability.")
        
        if errors:
            raise ValueError("Configuration validation failed:\n" + "\n".join(f"- {error}" for error in errors))
    
    def get_scale_up_qps_threshold(self) -> float:
        """
        Calculate the QPS threshold for scaling up.
        
        Returns:
            QPS threshold for scale-up decision
        """
        return self.target_qps_per_instance * self.scale_up_threshold
    
    def get_scale_down_qps_threshold(self) -> float:
        """
        Calculate the QPS threshold for scaling down.
        
        Returns:
            QPS threshold for scale-down decision
        """
        return self.target_qps_per_instance * self.scale_down_threshold
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert configuration to dictionary.
        
        Returns:
            Dictionary representation of configuration
        """
        return {
            'target_qps_per_instance': self.target_qps_per_instance,
            'scale_up_threshold': self.scale_up_threshold,
            'scale_down_threshold': self.scale_down_threshold,
            # min_instances, max_instances, desired_instances removed - using ASG
            'scale_up_increment': self.scale_up_increment,
            'scale_down_decrement': self.scale_down_decrement,
            'enable_dynamic_scaling': self.enable_dynamic_scaling,
            'max_scale_up_per_action': self.max_scale_up_per_action,
            'max_scale_down_per_action': self.max_scale_down_per_action,
            'scale_up_cooldown': self.scale_up_cooldown,
            'scale_down_cooldown': self.scale_down_cooldown,
            'general_cooldown': self.general_cooldown,
            'metric_period': self.metric_period,
            # metric_evaluation_periods, health_check_grace_period removed
            'autoscaling_group_id': self.autoscaling_group_id,
            'alb_id': self.alb_id,
            'region': self.region,
            'tos_mount_path': self.tos_mount_path,
            'tos_state_file': self.tos_state_file,
            'dry_run_mode': self.dry_run_mode,
            'alert_webhook_url': self.alert_webhook_url,
            # function_timeout removed
            'log_level': self.log_level,
            'enable_detailed_logging': self.enable_detailed_logging,
            'initial_delay_seconds': self.initial_delay_seconds
        }
    
    def __str__(self) -> str:
        """
        String representation of configuration (without sensitive data).
        
        Returns:
            String representation
        """
        config_dict = self.to_dict()
        # Remove sensitive information
        config_dict.pop('access_key_id', None)
        config_dict.pop('secret_access_key', None)
        
        return f"ScalingConfig({config_dict})"


def setup_logging(config: ScalingConfig) -> None:
    """
    Setup logging configuration based on config.
    
    Args:
        config: ScalingConfig instance
    """
    log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    
    # Configure logging format
    if config.enable_detailed_logging:
        log_format = '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
    else:
        log_format = '%(asctime)s - %(levelname)s - %(message)s'
    
    # Clear any existing handlers to avoid duplicates
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Create a StreamHandler that explicitly outputs to stdout
    # This ensures logs are captured by FaaS log collection systems
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(log_level)
    
    # Create formatter and add it to the handler
    formatter = logging.Formatter(
        fmt=log_format,
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    stream_handler.setFormatter(formatter)
    
    # Configure root logger
    root_logger.setLevel(log_level)
    root_logger.addHandler(stream_handler)
    
    # Set specific logger levels
    if not config.enable_detailed_logging:
        # Reduce noise from requests library
        logging.getLogger('requests').setLevel(logging.WARNING)
        logging.getLogger('urllib3').setLevel(logging.WARNING)
    
    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured with level: {config.log_level}")
    
    # Test log to verify output is working
    print(f"[STDOUT TEST] Logging setup complete - Level: {config.log_level}")
    sys.stdout.flush()  # Ensure immediate output


def load_config() -> ScalingConfig:
    """
    Load and validate configuration from environment variables.
    
    Returns:
        Validated ScalingConfig instance
        
    Raises:
        ValueError: If configuration is invalid
    """
    config = ScalingConfig.from_environment()
    config.validate()
    return config


# Environment variable template for documentation
ENV_TEMPLATE = """
# Core Scaling Parameters
TARGET_QPS_PER_INSTANCE=50
SCALE_UP_THRESHOLD=0.8
SCALE_DOWN_THRESHOLD=0.6

# Instance Limits
MIN_INSTANCES=1
MAX_INSTANCES=10
DESIRED_INSTANCES=2

# Scaling Behavior
SCALE_UP_INCREMENT=1
SCALE_DOWN_DECREMENT=1

# Dynamic Scaling Configuration
ENABLE_DYNAMIC_SCALING=true
MAX_SCALE_UP_PER_ACTION=0
MAX_SCALE_DOWN_PER_ACTION=0

# Cooldown Periods (seconds)
SCALE_UP_COOLDOWN=300
SCALE_DOWN_COOLDOWN=600
GENERAL_COOLDOWN=180

# Metric Collection
METRIC_PERIOD=300

# BytePlus Cloud Configuration
AUTOSCALING_GROUP_ID=asg-xxxxx
ALB_ID=alb-xxxxx
REGION=ap-southeast-1

# Storage Configuration
TOS_MOUNT_PATH=/tosmount
TOS_STATE_FILE=scaling_state.json

# API Configuration
ACCESS_KEY_ID=your_access_key
SECRET_ACCESS_KEY=your_secret_key

# Safety & Monitoring
DRY_RUN_MODE=false
ALERT_WEBHOOK_URL=

# Function Behavior
LOG_LEVEL=INFO
ENABLE_DETAILED_LOGGING=false
INITIAL_DELAY_SECONDS=0
"""