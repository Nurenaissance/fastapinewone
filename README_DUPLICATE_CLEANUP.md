# Duplicate Contact Cleanup - Quick Start Guide

## 🎯 What This Does

Automatically removes duplicate contacts from Nuren AI while keeping the contact with the most complete data.

## 🚀 Quick Usage

### Option 1: Using Python Test Script (Recommended)

```bash
# Preview what would be deleted
python test_duplicate_cleanup.py --preview

# Safe cleanup workflow (preview + confirm + execute)
python test_duplicate_cleanup.py --safe

# Cleanup specific tenant
python test_duplicate_cleanup.py --safe --tenant-id tenant_abc123
```

### Option 2: Using Shell Script

```bash
# Make executable (first time only)
chmod +x test_cleanup.sh

# Preview what would be deleted
./test_cleanup.sh preview

# Safe cleanup workflow
./test_cleanup.sh safe

# Cleanup specific tenant
./test_cleanup.sh safe tenant_abc123
```

### Option 3: Direct cURL

```bash
# Preview (dry run)
curl -X POST "http://localhost:8001/contacts/cleanup-duplicates?dry_run=true"

# Execute cleanup
curl -X POST "http://localhost:8001/contacts/cleanup-duplicates"
```

## 📋 Files Included

| File | Purpose |
|------|---------|
| `DUPLICATE_CLEANUP_API.md` | Complete API documentation |
| `test_duplicate_cleanup.py` | Python test script with safety features |
| `test_cleanup.sh` | Bash script for quick testing |
| `contacts/router.py` | FastAPI endpoint implementation |

## 🔍 How It Works

1. **Groups** contacts by tenant + phone number
2. **Calculates** richness score for each contact:
   - Basic fields (name, email, etc.): 1 point each
   - Timestamps (last_seen, etc.): 2 points each
   - Custom fields: 1 point per populated field
3. **Keeps** the contact with highest score
4. **Deletes** all duplicates

## ⚡ Quick Commands

```bash
# 1. Check FastAPI is running
curl http://localhost:8001/health

# 2. Preview duplicates (safe)
python test_duplicate_cleanup.py --preview

# 3. See detailed help
python test_duplicate_cleanup.py --help

# 4. Run safe cleanup
python test_duplicate_cleanup.py --safe
```

## 🛡️ Safety Features

- ✅ **Dry run mode** - Preview before deleting
- ✅ **Confirmation prompts** - Prevents accidents
- ✅ **Transaction rollback** - Automatic on errors
- ✅ **Tenant filtering** - Process one at a time
- ✅ **Detailed logging** - Full audit trail

## 📊 Example Output

```
📊 STATISTICS
----------------------------------------------------------------------
  Total Contacts Scanned:      1,250
  Tenants Processed:           5
  Phone Numbers with Dupes:    15
  Duplicate Contacts Found:    15
  Contacts Kept (Unique):      15
  Would Delete:                15
  Execution Time:              2.34s
```

## 🔧 Configuration

### Change Base URL

```bash
# Python script
python test_duplicate_cleanup.py --base-url https://your-api.com --preview

# Shell script
BASE_URL="https://your-api.com" ./test_cleanup.sh preview
```

### Filter by Tenant

```bash
# Python
python test_duplicate_cleanup.py --preview --tenant-id tenant_abc123

# Shell
./test_cleanup.sh preview tenant_abc123

# cURL
curl -X POST "http://localhost:8001/contacts/cleanup-duplicates?tenant_id=tenant_abc123&dry_run=true"
```

## 📚 Full Documentation

See `DUPLICATE_CLEANUP_API.md` for:
- Complete API reference
- Response format details
- Integration examples
- Best practices
- Performance considerations

## 🐛 Troubleshooting

### Endpoint returns 401 Unauthorized
✅ This is a **public endpoint** - no authentication required. Check if the path is correct.

### Connection refused
✅ Make sure FastAPI is running on port 8001:
```bash
# Check if running
curl http://localhost:8001/health

# Start FastAPI
cd fastAPIWhatsapp_withclaude
uvicorn main:app --reload --port 8001
```

### No duplicates found but you know they exist
✅ Duplicates are matched by exact phone number + tenant_id. Check if phone numbers are formatted consistently.

### Script doesn't run
✅ Install dependencies:
```bash
pip install requests
```

## 📞 Support

For detailed documentation, see:
- API Docs: `DUPLICATE_CLEANUP_API.md`
- Endpoint Code: `contacts/router.py:calculate_contact_richness()`
- Main Config: `main.py:PUBLIC_PATHS`

---

**Created by:** Senior Software Architecture Team
**Date:** 2026-02-04
**Version:** 1.0.0
