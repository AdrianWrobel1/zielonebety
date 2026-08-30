"""
Single-Command Automated Production Deployment Runner
"""

import sys
import unittest
from api.app import create_api_app
from api.services import PlatformAPIService
from scripts.backup import BackupManager


def run_deployment_pipeline(run_tests: bool = True) -> bool:
    """Runs automated pre-deployment validation, unit test verification, and health check."""
    print("=== [ZIELONE BETY DEPLOYMENT PIPELINE START] ===")

    # 1. Automated Pre-Deployment Backup
    print("Step 1/4: Creating pre-deployment backup snapshot...")
    bm = BackupManager()
    backup_path = bm.create_backup()
    print(f" -> Backup created at: {backup_path}")

    # 2. Execute Full Test Suite
    if run_tests:
        print("Step 2/4: Executing automated test suite...")
        loader = unittest.TestLoader()
        suite = loader.discover("tests", pattern="test_*.py")
        runner = unittest.TextTestRunner(verbosity=0)
        result = runner.run(suite)

        if not result.wasSuccessful():
            print(" -> ERROR: Test suite failed! Deployment aborted.")
            return False
        print(" -> All unit & integration tests PASSED!")
    else:
        print("Step 2/4: Test suite execution skipped (in-test run).")

    # 3. Validate REST API Application
    print("Step 3/4: Verifying FastAPI REST endpoints health...")
    service = PlatformAPIService()
    router = create_api_app(service=service)
    health = router.handle_get_health()

    if health.status_code != 200 or health.data.get("overall_status") not in ["HEALTHY", "DEGRADED"]:
        print(" -> ERROR: System health check failed!")
        return False
    print(" -> REST API Endpoint health check PASSED!")

    # 4. Final Deployment Signoff
    print("Step 4/4: Final deployment sign-off...")
    print("=== [DEPLOYMENT COMPLETED SUCCESSFULLY] ===")
    return True


if __name__ == "__main__":
    success = run_deployment_pipeline()
    sys.exit(0 if success else 1)
