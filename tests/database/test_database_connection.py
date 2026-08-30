"""
Unit Tests for Database Engine & Health Checks
"""

import unittest
from database.connection import DatabaseManager
from database.config import DatabaseConfig


class TestDatabaseConnection(unittest.TestCase):
    def setUp(self):
        self.config = DatabaseConfig.default_sqlite_in_memory()
        self.db_manager = DatabaseManager(self.config)

    def test_database_tables_creation(self):
        self.db_manager.create_tables()
        self.assertTrue(self.db_manager.check_health())

    def test_session_creation(self):
        self.db_manager.create_tables()
        with self.db_manager.get_session() as session:
            self.assertIsNotNone(session)


if __name__ == "__main__":
    unittest.main()
