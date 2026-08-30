"""
Unit & Integration Tests for Stage 11 Deployment Artifacts and Automation Utilities
"""

import os
import shutil
import unittest
from scripts.backup import BackupManager
from scripts.rollback import RollbackManager
from scripts.deploy import run_deployment_pipeline


class TestDeploymentArtifacts(unittest.TestCase):
    """Test suite validating Dockerfiles, docker-compose, scripts, and backup/rollback utilities."""

    def setUp(self):
        self.test_backup_dir = "test_backups_tmp"
        if os.path.exists(self.test_backup_dir):
            shutil.rmtree(self.test_backup_dir, ignore_errors=True)

    def tearDown(self):
        if os.path.exists(self.test_backup_dir):
            shutil.rmtree(self.test_backup_dir, ignore_errors=True)

    def test_dockerfile_and_compose_exist(self):
        self.assertTrue(os.path.exists("Dockerfile"))
        self.assertTrue(os.path.exists("docker-compose.yml"))
        self.assertTrue(os.path.exists(".dockerignore"))
        self.assertTrue(os.path.exists(".env.example"))
        self.assertTrue(os.path.exists("docker/nginx/nginx.conf"))

    def test_backup_and_rollback_utilities(self):
        bm = BackupManager(backup_dir=self.test_backup_dir)
        path = bm.create_backup()

        self.assertTrue(os.path.exists(path))
        self.assertTrue(os.path.exists(os.path.join(path, "manifest.json")))

        rm = RollbackManager(backup_dir=self.test_backup_dir)
        latest = rm.get_latest_backup()
        self.assertEqual(os.path.abspath(latest), os.path.abspath(path))

    def test_deployment_pipeline_execution(self):
        result = run_deployment_pipeline(run_tests=False)
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
