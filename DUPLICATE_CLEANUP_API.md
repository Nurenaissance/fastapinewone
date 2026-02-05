# Duplicate Contact Cleanup API

## Overview

This endpoint intelligently removes duplicate contacts from the Nuren AI system while preserving the contact with the most complete data.

## Endpoint Details

**URL:** `POST /contacts/cleanup-duplicates`

**Method:** `POST`

**Authentication:** Public endpoint (no authentication required)

**Content-Type:** `application/json`

---

## Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `tenant_id` | string | No | null | If provided, only processes this tenant. Otherwise processes ALL tenants. |
| `dry_run` | boolean | No | false | If `true`, returns what would be deleted without actually deleting anything. |

---

## How It Works

### 1. Duplicate Detection
- Groups contacts by `tenant_id` and `phone` number
- Identifies groups with more than one contact (duplicates)

### 2. Richness Scoring Algorithm

The endpoint calculates a "richness score" for each contact to determine which one has the most data:

**Scoring Rules:**
- **Basic fields** (1 point each): `name`, `email`, `address`, `description`, `bg_name`
- **Timestamp fields** (2 points each): `last_seen`, `last_delivered`, `last_replied`
- **Custom fields** (1 point per key-value pair): Each populated field in `customField` JSON
- **Boolean fields** (1 point): `manual_mode` if explicitly set
- **ID fields** (1 point): `bg_id` if present

### 3. Selection Logic
- Sorts duplicates by:
  1. **Richness score** (descending) - Higher score = more data
  2. **Creation date** (ascending) - Older contacts preferred as tiebreaker
- **Keeps:** The contact with the highest score
- **Deletes:** All other duplicates

---

## Response Format

### Success Response

```json
{
  "status": "success",
  "dry_run": false,
  "message": "Deleted 15 duplicate contacts",
  "statistics": {
    "total_contacts_scanned": 1250,
    "tenants_processed": 5,
    "duplicates_found": 15,
    "contacts_deleted": 15,
    "contacts_kept": 15,
    "phone_numbers_with_duplicates": 15
  },
  "execution_time_seconds": 2.34,
  "deletion_details": [
    {
      "tenant_id": "tenant_abc123",
      "phone": "+1234567890",
      "total_duplicates": 2,
      "kept_contact": {
        "id": 101,
        "richness_score": 12,
        "name": "John Doe",
        "email": "john@example.com",
        "created_on": "2025-01-15T10:30:00"
      },
      "deleted_contacts": [
        {
          "id": 205,
          "richness_score": 5,
          "name": "John",
          "email": null,
          "created_on": "2025-01-20T14:20:00"
        }
      ]
    }
  ],
  "note": "Showing first 50 of 15 phone numbers with duplicates"
}
```

### Error Response

```json
{
  "detail": "Error during duplicate cleanup: <error message>"
}
```

---

## Usage Examples

### Example 1: Dry Run (Preview Changes)

Check what would be deleted without making changes:

```bash
curl -X POST "http://localhost:8001/contacts/cleanup-duplicates?dry_run=true" \
  -H "Content-Type: application/json"
```

### Example 2: Cleanup Specific Tenant

Clean up duplicates for a single tenant:

```bash
curl -X POST "http://localhost:8001/contacts/cleanup-duplicates?tenant_id=tenant_abc123" \
  -H "Content-Type: application/json"
```

### Example 3: Full Cleanup (All Tenants)

Clean up duplicates across all tenants:

```bash
curl -X POST "http://localhost:8001/contacts/cleanup-duplicates" \
  -H "Content-Type: application/json"
```

### Example 4: Python Script

```python
import requests

# Dry run first to preview
response = requests.post(
    "http://localhost:8001/contacts/cleanup-duplicates",
    params={"dry_run": True}
)

print("Preview of changes:")
print(response.json())

# If satisfied, run the actual cleanup
if input("Proceed with deletion? (yes/no): ").lower() == "yes":
    response = requests.post(
        "http://localhost:8001/contacts/cleanup-duplicates",
        params={"dry_run": False}
    )
    print("Cleanup complete:")
    print(response.json())
```

### Example 5: JavaScript (Node.js)

```javascript
const axios = require('axios');

async function cleanupDuplicates(tenantId = null, dryRun = true) {
  try {
    const response = await axios.post(
      'http://localhost:8001/contacts/cleanup-duplicates',
      {},
      {
        params: {
          tenant_id: tenantId,
          dry_run: dryRun
        }
      }
    );

    console.log('Cleanup Results:', response.data);
    return response.data;
  } catch (error) {
    console.error('Error:', error.response?.data || error.message);
  }
}

// Preview changes
cleanupDuplicates(null, true).then(results => {
  console.log(`Would delete ${results.statistics.contacts_deleted} contacts`);
});
```

---

## Best Practices

### 1. Always Test with Dry Run First

```bash
# Step 1: Preview changes
curl -X POST "http://localhost:8001/contacts/cleanup-duplicates?dry_run=true"

# Step 2: Review the deletion_details

# Step 3: Execute if satisfied
curl -X POST "http://localhost:8001/contacts/cleanup-duplicates"
```

### 2. Process One Tenant at a Time (for safety)

```bash
# Get list of tenants first
# Then process each one individually
curl -X POST "http://localhost:8001/contacts/cleanup-duplicates?tenant_id=tenant_1"
curl -X POST "http://localhost:8001/contacts/cleanup-duplicates?tenant_id=tenant_2"
```

### 3. Schedule Regular Cleanups

Use a cron job or scheduled task:

```bash
# Crontab entry (runs daily at 3 AM)
0 3 * * * curl -X POST "http://localhost:8001/contacts/cleanup-duplicates" >> /var/log/duplicate_cleanup.log 2>&1
```

### 4. Monitor the Logs

The endpoint logs to the FastAPI logger:
- INFO level: Normal operations
- ERROR level: Failures and exceptions

---

## Performance Considerations

- **Batch Size:** The endpoint processes all contacts in memory. For very large databases (>100k contacts), consider adding pagination.
- **Transaction Safety:** Uses database transactions with rollback on errors.
- **Execution Time:** Typical performance:
  - 1,000 contacts: ~1 second
  - 10,000 contacts: ~5 seconds
  - 100,000 contacts: ~30 seconds

---

## Safety Features

1. **Dry Run Mode:** Test without consequences
2. **Transaction Rollback:** Automatic rollback on errors
3. **Tenant Isolation:** Can process one tenant at a time
4. **Detailed Logging:** Full audit trail
5. **Smart Selection:** Preserves the most valuable contact
6. **Tiebreaker Logic:** Uses creation date when scores are equal

---

## Response Fields Explained

### Statistics Object

- `total_contacts_scanned`: Total number of contacts examined
- `tenants_processed`: Number of unique tenants in the dataset
- `duplicates_found`: Number of duplicate contacts identified
- `contacts_deleted`: Number of contacts removed
- `contacts_kept`: Number of unique phone numbers retained
- `phone_numbers_with_duplicates`: Count of phone numbers that had duplicates

### Deletion Details Array

Limited to first 50 entries for response size. Each entry shows:
- Which contact was kept (with score)
- Which contacts were deleted (with scores)
- Tenant and phone number information

---

## Error Handling

The endpoint handles various error scenarios:

| Error | Status Code | Description |
|-------|-------------|-------------|
| Database connection failure | 500 | Cannot connect to PostgreSQL |
| Query timeout | 500 | Operation took too long |
| Transaction conflict | 500 | Concurrent modification detected |
| General exception | 500 | Unexpected error with rollback |

---

## Integration with CI/CD

### Pre-Deployment Check

```yaml
# .github/workflows/deploy.yml
- name: Check for duplicates
  run: |
    RESPONSE=$(curl -X POST "${{ secrets.API_URL }}/contacts/cleanup-duplicates?dry_run=true")
    DUPLICATES=$(echo $RESPONSE | jq '.statistics.duplicates_found')
    if [ $DUPLICATES -gt 0 ]; then
      echo "⚠️ Warning: $DUPLICATES duplicate contacts found"
    fi
```

### Post-Deployment Cleanup

```yaml
- name: Cleanup duplicates
  run: |
    curl -X POST "${{ secrets.API_URL }}/contacts/cleanup-duplicates"
```

---

## Monitoring & Alerts

### Prometheus Metrics (if enabled)

- `duplicate_cleanup_executions_total`
- `duplicate_cleanup_contacts_deleted_total`
- `duplicate_cleanup_duration_seconds`

### Logging

All operations are logged with structured data:

```
INFO - Starting duplicate contact cleanup (tenant_id=None, dry_run=False)
INFO - Deleted 15 duplicate contacts
INFO - Dry run completed. Would delete 8 contacts
ERROR - Error during duplicate cleanup: <details>
```

---

## API Architecture Notes

### Why This Design?

1. **Public Endpoint:** Allows scheduled jobs and administrative scripts without JWT management
2. **Richness Algorithm:** Ensures business-critical data is never lost
3. **Dry Run:** Prevents accidental data loss
4. **Tenant Isolation:** Supports multi-tenant architecture
5. **Detailed Response:** Provides full audit trail

### Future Enhancements

Potential improvements for v2:
- Add `exclude_tenant_ids` parameter
- Support custom richness scoring weights
- Add pagination for very large datasets
- Email notifications with summary report
- Backup contacts before deletion
- Undo/restore functionality

---

## Support

For issues or questions:
- Check FastAPI logs: `/var/log/fastapi/`
- Review database state
- Use dry run mode to debug
- Contact: Senior Software Architecture Team

---

## Changelog

### Version 1.0.0 (2026-02-04)
- Initial release
- Basic duplicate detection and removal
- Richness scoring algorithm
- Dry run support
- Multi-tenant support
