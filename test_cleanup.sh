#!/bin/bash
# Quick test script for duplicate contact cleanup endpoint
# Usage: ./test_cleanup.sh [preview|execute|safe] [tenant_id]

BASE_URL="http://localhost:8001"
ACTION=${1:-preview}
TENANT_ID=$2

echo "=========================================="
echo "Duplicate Contact Cleanup Test Script"
echo "=========================================="
echo "Action: $ACTION"
if [ -n "$TENANT_ID" ]; then
    echo "Tenant: $TENANT_ID"
else
    echo "Tenant: ALL TENANTS"
fi
echo "=========================================="
echo ""

case $ACTION in
    preview)
        echo "🔍 Running preview (dry run)..."
        if [ -n "$TENANT_ID" ]; then
            curl -X POST "${BASE_URL}/contacts/cleanup-duplicates?dry_run=true&tenant_id=${TENANT_ID}" \
                -H "Content-Type: application/json" | jq '.'
        else
            curl -X POST "${BASE_URL}/contacts/cleanup-duplicates?dry_run=true" \
                -H "Content-Type: application/json" | jq '.'
        fi
        ;;

    execute)
        echo "⚠️  WARNING: This will DELETE duplicate contacts!"
        read -p "Type 'YES' to confirm: " CONFIRM
        if [ "$CONFIRM" != "YES" ]; then
            echo "❌ Cancelled"
            exit 1
        fi

        echo "🗑️  Executing cleanup..."
        if [ -n "$TENANT_ID" ]; then
            curl -X POST "${BASE_URL}/contacts/cleanup-duplicates?tenant_id=${TENANT_ID}" \
                -H "Content-Type: application/json" | jq '.'
        else
            curl -X POST "${BASE_URL}/contacts/cleanup-duplicates" \
                -H "Content-Type: application/json" | jq '.'
        fi
        ;;

    safe)
        echo "🛡️  Running safe cleanup workflow..."
        echo ""
        echo "Step 1: Preview"
        echo "--------------------"

        if [ -n "$TENANT_ID" ]; then
            PREVIEW=$(curl -s -X POST "${BASE_URL}/contacts/cleanup-duplicates?dry_run=true&tenant_id=${TENANT_ID}" \
                -H "Content-Type: application/json")
        else
            PREVIEW=$(curl -s -X POST "${BASE_URL}/contacts/cleanup-duplicates?dry_run=true" \
                -H "Content-Type: application/json")
        fi

        echo "$PREVIEW" | jq '.'

        DUPLICATES=$(echo "$PREVIEW" | jq -r '.statistics.duplicates_found // 0')

        if [ "$DUPLICATES" -eq 0 ]; then
            echo ""
            echo "✅ No duplicates found. Nothing to clean up."
            exit 0
        fi

        echo ""
        echo "Step 2: Confirmation"
        echo "--------------------"
        echo "Found $DUPLICATES duplicate contacts."
        read -p "Proceed with deletion? Type 'YES': " CONFIRM

        if [ "$CONFIRM" != "YES" ]; then
            echo "❌ Cancelled"
            exit 1
        fi

        echo ""
        echo "Step 3: Execute"
        echo "--------------------"
        if [ -n "$TENANT_ID" ]; then
            curl -X POST "${BASE_URL}/contacts/cleanup-duplicates?tenant_id=${TENANT_ID}" \
                -H "Content-Type: application/json" | jq '.'
        else
            curl -X POST "${BASE_URL}/contacts/cleanup-duplicates" \
                -H "Content-Type: application/json" | jq '.'
        fi
        ;;

    *)
        echo "❌ Invalid action: $ACTION"
        echo ""
        echo "Usage: $0 [preview|execute|safe] [tenant_id]"
        echo ""
        echo "Actions:"
        echo "  preview  - Show what would be deleted (safe)"
        echo "  execute  - Delete duplicate contacts (requires confirmation)"
        echo "  safe     - Full workflow: preview, confirm, execute"
        echo ""
        echo "Examples:"
        echo "  $0 preview"
        echo "  $0 preview tenant_abc123"
        echo "  $0 safe"
        echo "  $0 execute tenant_abc123"
        exit 1
        ;;
esac

echo ""
echo "=========================================="
echo "✅ Complete"
echo "=========================================="
