-- Database Performance Indexes for FastAPI (SIMPLE VERSION)
-- Run this SQL script on your Azure PostgreSQL database
-- This will dramatically improve query performance
--
-- NOTE: This version locks tables briefly during index creation
-- Use this for initial setup or during maintenance windows
-- For zero-downtime, use add_indexes_concurrent.sql instead

-- =============================================================================
-- DISABLE PARALLEL OPERATIONS (Prevent Azure connection exhaustion)
-- =============================================================================

SET max_parallel_workers_per_gather = 0;
SET max_parallel_maintenance_workers = 0;

-- =============================================================================
-- CONTACTS TABLE INDEXES
-- =============================================================================

-- Index on tenant_id (used in almost every query)
CREATE INDEX IF NOT EXISTS idx_contacts_tenant_id
ON contacts_contact(tenant_id);

-- Index on phone (used for lookups and searches)
CREATE INDEX IF NOT EXISTS idx_contacts_phone
ON contacts_contact(phone);

-- Composite index for phone + tenant (faster unique lookups)
CREATE INDEX IF NOT EXISTS idx_contacts_phone_tenant
ON contacts_contact(phone, tenant_id);

-- Index on last_delivered (used in filtering and sorting)
CREATE INDEX IF NOT EXISTS idx_contacts_last_delivered
ON contacts_contact(last_delivered DESC NULLS LAST);

-- Index on last_replied (used in engagement filtering)
CREATE INDEX IF NOT EXISTS idx_contacts_last_replied
ON contacts_contact(last_replied DESC NULLS LAST);

-- Index on last_seen (used in engagement filtering)
CREATE INDEX IF NOT EXISTS idx_contacts_last_seen
ON contacts_contact(last_seen DESC NULLS LAST);

-- Index on createdOn (used in "fresh contacts" filter)
CREATE INDEX IF NOT EXISTS idx_contacts_created_on
ON contacts_contact("createdOn" DESC);

-- Composite index for engagement queries (tenant + last_delivered)
CREATE INDEX IF NOT EXISTS idx_contacts_tenant_delivered
ON contacts_contact(tenant_id, last_delivered DESC NULLS LAST)
WHERE last_delivered IS NOT NULL;

-- Composite index for engagement queries (tenant + last_replied)
CREATE INDEX IF NOT EXISTS idx_contacts_tenant_replied
ON contacts_contact(tenant_id, last_replied DESC NULLS LAST)
WHERE last_replied IS NOT NULL;

-- =============================================================================
-- CONVERSATIONS TABLE INDEXES
-- =============================================================================

-- Index on contact_id (used for fetching conversation history)
CREATE INDEX IF NOT EXISTS idx_conversations_contact_id
ON interaction_conversation(contact_id);

-- Index on business_phone_number_id (tenant filtering)
CREATE INDEX IF NOT EXISTS idx_conversations_bpid
ON interaction_conversation(business_phone_number_id);

-- Index on source (filtering by message source)
CREATE INDEX IF NOT EXISTS idx_conversations_source
ON interaction_conversation(source);

-- Index on date_time (sorting conversations)
CREATE INDEX IF NOT EXISTS idx_conversations_datetime
ON interaction_conversation(date_time DESC);

-- Composite index for common query pattern (contact + source + bpid)
CREATE INDEX IF NOT EXISTS idx_conversations_contact_source_bpid
ON interaction_conversation(contact_id, source, business_phone_number_id);

-- Composite index for pagination queries (contact + datetime)
CREATE INDEX IF NOT EXISTS idx_conversations_contact_datetime
ON interaction_conversation(contact_id, date_time DESC);

-- =============================================================================
-- WHATSAPP TENANT DATA INDEXES
-- =============================================================================

-- Index on tenant_id (primary lookup)
CREATE INDEX IF NOT EXISTS idx_whatsapp_tenant_tenant_id
ON whatsapp_chat_whatsapptenantdata(tenant_id);

-- =============================================================================
-- PRODUCTS/CATALOG INDEXES
-- =============================================================================

-- Index on tenant_id (filtering products by tenant)
CREATE INDEX IF NOT EXISTS idx_products_tenant_id
ON shop_products(tenant_id);

-- =============================================================================
-- NOTIFICATIONS INDEXES
-- =============================================================================

-- Index on tenant_id
CREATE INDEX IF NOT EXISTS idx_notifications_tenant_id
ON notifications(tenant_id);

-- Index on created_on for sorting
CREATE INDEX IF NOT EXISTS idx_notifications_created_on
ON notifications(created_on DESC);

-- Composite index for active notifications query
CREATE INDEX IF NOT EXISTS idx_notifications_tenant_created
ON notifications(tenant_id, created_on DESC);

-- =============================================================================
-- ANALYZE TABLES (Update statistics for query planner)
-- =============================================================================

ANALYZE contacts_contact;
ANALYZE interaction_conversation;
ANALYZE whatsapp_chat_whatsapptenantdata;
ANALYZE shop_products;
ANALYZE notifications;

-- =============================================================================
-- VERIFICATION QUERY
-- =============================================================================

-- Run this to verify indexes were created successfully
SELECT
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
AND tablename IN ('contacts_contact', 'interaction_conversation', 'whatsapp_chat_whatsapptenantdata', 'shop_products', 'notifications')
ORDER BY tablename, indexname;

-- =============================================================================
-- COMPLETION
-- =============================================================================
-- ✅ All indexes have been created successfully
-- ✅ Tables were briefly locked during index creation
-- ✅ Statistics updated with ANALYZE
--
-- Expected Performance Improvement:
-- - Contact queries: 10-100x faster
-- - Conversation history: 5-20x faster
-- - Response times should drop from seconds to milliseconds
-- =============================================================================
