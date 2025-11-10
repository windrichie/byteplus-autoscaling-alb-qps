import os
import sys
import logging
from unittest.mock import MagicMock

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from index import handler


def run_local_test():
    """
    Runs a local test of the FaaS handler with real dependencies (DB + Cloud APIs).
    Ensure required environment variables are set in your shell or .env.
    """
    # No environment overrides here; expects DB_DSN, ACCESS_KEY_ID, SECRET_ACCESS_KEY, REGION to be set externally

    print("--- Running Local Test (Real DB + Cloud APIs) ---")
    # Mock context object
    mock_context = MagicMock()
    mock_context.request_id = "local-test-run"

    # Invoke the handler
    result = handler({}, mock_context)

    print("--- Test Result ---")
    import json
    print(json.dumps(result, indent=2))
    print("--- Test Complete ---")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    run_local_test()