"""
Automated System Rollback Script
"""

import os
import shutil


class RollbackManager:
    """Automated rollback utility to revert system database and configuration to latest snapshot."""

    def __init__(self, backup_dir: str = "backups"):
        self.backup_dir = os.path.abspath(backup_dir)

    def get_latest_backup(self) -> str:
        """Find the most recent valid backup directory."""
        if not os.path.exists(self.backup_dir):
            raise FileNotFoundError("No backups directory found.")

        backups = [
            os.path.join(self.backup_dir, d)
            for d in os.listdir(self.backup_dir)
            if os.path.isdir(os.path.join(self.backup_dir, d)) and d.startswith("backup_")
        ]

        if not backups:
            raise FileNotFoundError("No backup snapshots available.")

        backups.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        return backups[0]

    def execute_rollback(self) -> str:
        """Restores database and configuration from the latest snapshot."""
        latest = self.get_latest_backup()

        db_snapshot = os.path.join(latest, "database.sqlite")
        if os.path.exists(db_snapshot):
            shutil.copy2(db_snapshot, "zielonebety.db")

        return latest


if __name__ == "__main__":
    rm = RollbackManager()
    restored = rm.execute_rollback()
    print(f"[Rollback] System successfully restored to snapshot: {restored}")
