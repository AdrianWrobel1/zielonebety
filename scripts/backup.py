"""
Automated Database & System Configuration Backup Script
"""

import os
import shutil
import time
from datetime import datetime, timezone


class BackupManager:
    """Automated backup utility taking snapshots of database files, configurations, and state."""

    def __init__(self, backup_dir: str = "backups", retention_days: int = 7):
        self.backup_dir = os.path.abspath(backup_dir)
        self.retention_days = retention_days
        os.makedirs(self.backup_dir, exist_ok=True)

    def create_backup(self, db_filepath: str = "zielonebety.db") -> str:
        """Create a timestamped snapshot of system database and configuration."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        target_folder = os.path.join(self.backup_dir, f"backup_{timestamp}")
        os.makedirs(target_folder, exist_ok=True)

        # 1. Copy database file if present
        if os.path.exists(db_filepath):
            shutil.copy2(db_filepath, os.path.join(target_folder, "database.sqlite"))

        # 2. Backup configuration files
        for cfg_file in [".env", ".env.example", "docker-compose.yml", "Dockerfile"]:
            if os.path.exists(cfg_file):
                shutil.copy2(cfg_file, os.path.join(target_folder, cfg_file))

        # 3. Write manifest
        manifest_path = os.path.join(target_folder, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(f'{{"timestamp": "{timestamp}", "status": "SUCCESS"}}\n')

        self.cleanup_old_backups()
        return target_folder

    def cleanup_old_backups(self):
        """Purge backups older than retention window."""
        now = time.time()
        retention_seconds = self.retention_days * 86400

        for entry in os.listdir(self.backup_dir):
            full_path = os.path.join(self.backup_dir, entry)
            if os.path.isdir(full_path) and entry.startswith("backup_"):
                mtime = os.path.getmtime(full_path)
                if (now - mtime) > retention_seconds:
                    shutil.rmtree(full_path, ignore_errors=True)


if __name__ == "__main__":
    bm = BackupManager()
    path = bm.create_backup()
    print(f"[Backup] Successfully created snapshot at: {path}")
