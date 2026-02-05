"""
Test Script for Duplicate Contact Cleanup Endpoint
Author: Senior Software Architecture Team
Date: 2026-02-04

This script provides a safe way to test and execute the duplicate cleanup endpoint.
"""

import requests
import json
from typing import Optional
from datetime import datetime


class DuplicateCleanupTester:
    def __init__(self, base_url: str = "http://localhost:8001"):
        """
        Initialize the tester with the FastAPI base URL.

        Args:
            base_url: Base URL of the FastAPI server (default: http://localhost:8001)
        """
        self.base_url = base_url.rstrip('/')
        self.endpoint = f"{self.base_url}/contacts/cleanup-duplicates"

    def preview_cleanup(self, tenant_id: Optional[str] = None) -> dict:
        """
        Run a dry run to preview what would be deleted.

        Args:
            tenant_id: Optional tenant ID to filter by

        Returns:
            Response dictionary with preview results
        """
        print("\n" + "="*70)
        print("🔍 PREVIEW MODE (Dry Run)")
        print("="*70)

        params = {"dry_run": True}
        if tenant_id:
            params["tenant_id"] = tenant_id
            print(f"Target: Tenant '{tenant_id}'")
        else:
            print("Target: ALL TENANTS")

        try:
            response = requests.post(self.endpoint, params=params)
            response.raise_for_status()

            data = response.json()
            self._print_results(data, is_preview=True)
            return data

        except requests.exceptions.RequestException as e:
            print(f"\n❌ Error: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Response: {e.response.text}")
            return None

    def execute_cleanup(self, tenant_id: Optional[str] = None, confirm: bool = True) -> dict:
        """
        Execute the actual cleanup (deletes duplicates).

        Args:
            tenant_id: Optional tenant ID to filter by
            confirm: If True, requires user confirmation

        Returns:
            Response dictionary with cleanup results
        """
        print("\n" + "="*70)
        print("⚠️  EXECUTION MODE (Will Delete Data)")
        print("="*70)

        if confirm:
            print("\n⚠️  WARNING: This will permanently delete duplicate contacts!")
            confirmation = input("Type 'YES' to proceed: ")
            if confirmation != "YES":
                print("❌ Cancelled by user")
                return None

        params = {"dry_run": False}
        if tenant_id:
            params["tenant_id"] = tenant_id
            print(f"Target: Tenant '{tenant_id}'")
        else:
            print("Target: ALL TENANTS")

        try:
            response = requests.post(self.endpoint, params=params)
            response.raise_for_status()

            data = response.json()
            self._print_results(data, is_preview=False)
            return data

        except requests.exceptions.RequestException as e:
            print(f"\n❌ Error: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Response: {e.response.text}")
            return None

    def _print_results(self, data: dict, is_preview: bool):
        """Print formatted results from the API response."""
        if not data:
            return

        stats = data.get("statistics", {})

        print(f"\n📊 STATISTICS")
        print("-" * 70)
        print(f"  Total Contacts Scanned:      {stats.get('total_contacts_scanned', 0):,}")
        print(f"  Tenants Processed:           {stats.get('tenants_processed', 0)}")
        print(f"  Phone Numbers with Dupes:    {stats.get('phone_numbers_with_duplicates', 0)}")
        print(f"  Duplicate Contacts Found:    {stats.get('duplicates_found', 0)}")
        print(f"  Contacts Kept (Unique):      {stats.get('contacts_kept', 0)}")

        if is_preview:
            print(f"  Would Delete:                {stats.get('contacts_deleted', 0)}")
        else:
            print(f"  Contacts Deleted:            {stats.get('contacts_deleted', 0)}")

        print(f"  Execution Time:              {data.get('execution_time_seconds', 0):.2f}s")

        # Print detailed deletion info
        details = data.get("deletion_details", [])
        if details:
            print(f"\n📋 DETAILED BREAKDOWN (Showing {len(details)} entries)")
            print("-" * 70)

            for idx, detail in enumerate(details[:10], 1):  # Show first 10
                print(f"\n  {idx}. Phone: {detail['phone']} (Tenant: {detail['tenant_id']})")
                print(f"     Total Duplicates: {detail['total_duplicates']}")

                kept = detail['kept_contact']
                print(f"     ✅ KEPT:    ID={kept['id']}, Score={kept['richness_score']}, "
                      f"Name='{kept['name']}', Email='{kept['email']}'")

                for deleted in detail['deleted_contacts']:
                    action = "WOULD DELETE" if is_preview else "DELETED"
                    print(f"     ❌ {action}: ID={deleted['id']}, Score={deleted['richness_score']}, "
                          f"Name='{deleted['name']}', Email='{deleted['email']}'")

            if len(details) > 10:
                print(f"\n  ... and {len(details) - 10} more duplicates")

        print("\n" + "="*70)
        if is_preview:
            print("✅ Preview complete. No data was deleted.")
        else:
            print("✅ Cleanup complete. Duplicates have been removed.")
        print("="*70 + "\n")

    def run_safe_cleanup(self, tenant_id: Optional[str] = None):
        """
        Run a complete safe cleanup workflow: preview then execute.

        Args:
            tenant_id: Optional tenant ID to filter by
        """
        print("\n" + "="*70)
        print("🛡️  SAFE CLEANUP WORKFLOW")
        print("="*70)
        print("\nStep 1: Preview what will be deleted...")

        preview_results = self.preview_cleanup(tenant_id)

        if not preview_results:
            print("❌ Preview failed. Aborting.")
            return

        stats = preview_results.get("statistics", {})
        if stats.get("duplicates_found", 0) == 0:
            print("\n✅ No duplicates found. Nothing to clean up.")
            return

        print(f"\nStep 2: Execute cleanup...")
        print(f"   Will delete {stats.get('contacts_deleted', 0)} duplicate contacts")

        self.execute_cleanup(tenant_id, confirm=True)


def main():
    """Main entry point for the test script."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Test and execute duplicate contact cleanup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview what would be deleted (all tenants)
  python test_duplicate_cleanup.py --preview

  # Preview for specific tenant
  python test_duplicate_cleanup.py --preview --tenant-id tenant_abc123

  # Safe cleanup workflow (preview + confirm + execute)
  python test_duplicate_cleanup.py --safe

  # Execute cleanup (with confirmation)
  python test_duplicate_cleanup.py --execute

  # Execute for specific tenant without confirmation (dangerous!)
  python test_duplicate_cleanup.py --execute --tenant-id tenant_xyz --no-confirm
        """
    )

    parser.add_argument(
        "--base-url",
        default="http://localhost:8001",
        help="Base URL of the FastAPI server (default: http://localhost:8001)"
    )
    parser.add_argument(
        "--tenant-id",
        help="Filter by specific tenant ID (optional)"
    )

    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument(
        "--preview",
        action="store_true",
        help="Run in preview mode (dry run, no deletion)"
    )
    action_group.add_argument(
        "--execute",
        action="store_true",
        help="Execute actual cleanup (deletes duplicates)"
    )
    action_group.add_argument(
        "--safe",
        action="store_true",
        help="Run safe workflow: preview then execute with confirmation"
    )

    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip confirmation prompt (use with caution!)"
    )

    args = parser.parse_args()

    tester = DuplicateCleanupTester(args.base_url)

    if args.preview:
        tester.preview_cleanup(args.tenant_id)
    elif args.execute:
        tester.execute_cleanup(args.tenant_id, confirm=not args.no_confirm)
    elif args.safe:
        tester.run_safe_cleanup(args.tenant_id)


if __name__ == "__main__":
    main()
