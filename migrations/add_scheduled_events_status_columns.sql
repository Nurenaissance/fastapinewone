-- Migration: Add status tracking columns to scheduled_events table
-- Run this SQL on your PostgreSQL database to add the new columns

-- Add status column with default 'pending'
ALTER TABLE scheduled_events
ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'pending' NOT NULL;

-- Add retry tracking columns
ALTER TABLE scheduled_events
ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0 NOT NULL;

ALTER TABLE scheduled_events
ADD COLUMN IF NOT EXISTS max_retries INTEGER DEFAULT 3 NOT NULL;

-- Add error tracking column
ALTER TABLE scheduled_events
ADD COLUMN IF NOT EXISTS last_error TEXT;

-- Add timestamp columns for auditing
ALTER TABLE scheduled_events
ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;

ALTER TABLE scheduled_events
ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL;

ALTER TABLE scheduled_events
ADD COLUMN IF NOT EXISTS executed_at TIMESTAMP;

-- Update existing rows to have 'pending' status (if they don't already have one)
UPDATE scheduled_events SET status = 'pending' WHERE status IS NULL;

-- Create index on status for faster queries
CREATE INDEX IF NOT EXISTS idx_scheduled_events_status ON scheduled_events(status);

-- Create index on date + status for the scheduler query
CREATE INDEX IF NOT EXISTS idx_scheduled_events_date_status ON scheduled_events(date, status);

-- Verify the changes
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'scheduled_events'
ORDER BY ordinal_position;
