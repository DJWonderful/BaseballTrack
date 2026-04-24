-- ============================================================
-- 001_create_database.sql
-- Creates the 'baseball' database if it doesn't exist.
-- Run this connected to the default 'postgres' database.
-- ============================================================

-- Note: CREATE DATABASE cannot run inside a transaction block.
-- The setup_db.py script handles this with autocommit mode.
SELECT 'CREATE DATABASE baseball'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'baseball');

-- If running manually:
-- CREATE DATABASE baseball;
