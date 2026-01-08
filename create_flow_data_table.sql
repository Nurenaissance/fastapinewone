-- SQL Migration Script: Create flow_data table
-- SECURITY FIX: Migrate from insecure JSON file to PostgreSQL database
-- Created: 2026-01-06
-- Execute this in your PostgreSQL database

-- Create flow_data table with tenant isolation
CREATE TABLE IF NOT EXISTS flow_data (
    id SERIAL PRIMARY KEY,
    pan VARCHAR(50) NOT NULL,
    phone VARCHAR(20),
    name VARCHAR(255),
    password VARCHAR(255),
    questions JSONB,
    tenant_id VARCHAR(50) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_flow_data_pan_tenant ON flow_data(pan, tenant_id);
CREATE INDEX IF NOT EXISTS idx_flow_data_tenant ON flow_data(tenant_id);
CREATE INDEX IF NOT EXISTS idx_flow_data_created ON flow_data(created_at);

-- Add unique constraint: PAN must be unique within each tenant
CREATE UNIQUE INDEX IF NOT EXISTS idx_flow_data_pan_tenant_unique
    ON flow_data(pan, tenant_id);

-- Add comments for documentation
COMMENT ON TABLE flow_data IS 'WhatsApp Flow data with tenant isolation - migrated from JSON file';
COMMENT ON COLUMN flow_data.pan IS 'PAN number - unique within tenant';
COMMENT ON COLUMN flow_data.tenant_id IS 'Tenant ID for multi-tenant isolation';
COMMENT ON COLUMN flow_data.questions IS 'JSONB array of question/answer pairs';

-- Verify table creation
SELECT
    table_name,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_name = 'flow_data'
ORDER BY ordinal_position;

-- Verify indexes
SELECT
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename = 'flow_data';
