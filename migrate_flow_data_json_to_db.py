"""
Data Migration Script: JSON to PostgreSQL for flowsAPI
SECURITY FIX: Migrate existing flow_data.json to database with tenant isolation

Usage:
    python migrate_flow_data_json_to_db.py --tenant <tenant_id>

Example:
    python migrate_flow_data_json_to_db.py --tenant ai
    python migrate_flow_data_json_to_db.py --tenant default --dry-run
"""
import json
import argparse
import sys
from pathlib import Path
from sqlalchemy.orm import Session
from config.database import SessionLocal, engine
from flowsAPI.models import FlowDataModel, Base

# File path to JSON data
JSON_FILE_PATH = Path("flowsAPI/flow_data.json")


def load_json_data():
    """Load existing flow data from JSON file"""
    if not JSON_FILE_PATH.exists():
        print(f"❌ JSON file not found: {JSON_FILE_PATH}")
        return []

    try:
        with open(JSON_FILE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"✅ Loaded {len(data)} records from JSON file")
        return data
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing JSON file: {e}")
        return []
    except Exception as e:
        print(f"❌ Error reading JSON file: {e}")
        return []


def migrate_to_database(tenant_id: str, dry_run: bool = False):
    """
    Migrate flow data from JSON to PostgreSQL database

    Args:
        tenant_id: Tenant ID to assign to migrated records
        dry_run: If True, don't actually insert data (just simulate)
    """
    print("\n" + "="*70)
    print("FlowAPI Data Migration: JSON → PostgreSQL")
    print("="*70)

    # Load JSON data
    json_data = load_json_data()

    if not json_data:
        print("⚠️ No data to migrate")
        return

    # Create database tables if they don't exist
    print("\n📋 Creating database tables if needed...")
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables ready")

    # Start migration
    db = SessionLocal()
    migrated_count = 0
    skipped_count = 0
    error_count = 0

    print(f"\n🔄 Starting migration for tenant: {tenant_id}")
    print(f"   Dry run mode: {'YES (no data will be written)' if dry_run else 'NO (data will be written)'}")
    print("-"*70)

    try:
        for idx, record in enumerate(json_data, 1):
            try:
                pan = record.get('PAN')
                if not pan:
                    print(f"⚠️ Record {idx}: Skipping - no PAN field")
                    skipped_count += 1
                    continue

                # Check if PAN already exists for this tenant
                existing = db.query(FlowDataModel).filter(
                    FlowDataModel.pan == pan,
                    FlowDataModel.tenant_id == tenant_id
                ).first()

                if existing:
                    print(f"⚠️ Record {idx}: PAN '{pan}' already exists in database - skipping")
                    skipped_count += 1
                    continue

                # Prepare new record
                db_record = FlowDataModel(
                    pan=pan,
                    phone=record.get('phone'),
                    name=record.get('name'),
                    password=record.get('password'),
                    questions=record.get('questions'),  # Already in dict format
                    tenant_id=tenant_id
                )

                if dry_run:
                    print(f"✅ Record {idx}: Would migrate PAN '{pan}' (dry run)")
                else:
                    db.add(db_record)
                    print(f"✅ Record {idx}: Migrated PAN '{pan}'")

                migrated_count += 1

            except Exception as e:
                print(f"❌ Record {idx}: Error - {str(e)}")
                error_count += 1
                continue

        # Commit if not dry run
        if not dry_run:
            db.commit()
            print("\n✅ Database transaction committed")
        else:
            print("\n⚠️ DRY RUN - No data written to database")

    except Exception as e:
        db.rollback()
        print(f"\n❌ Migration failed: {str(e)}")
        print("⚠️ Database transaction rolled back")
        sys.exit(1)

    finally:
        db.close()

    # Print summary
    print("\n" + "="*70)
    print("Migration Summary")
    print("="*70)
    print(f"Total records in JSON:  {len(json_data)}")
    print(f"Successfully migrated:  {migrated_count}")
    print(f"Skipped (duplicates):   {skipped_count}")
    print(f"Errors:                 {error_count}")
    print("="*70)

    if not dry_run and migrated_count > 0:
        print("\n✅ Migration completed successfully!")
        print(f"\n⚠️ IMPORTANT: Backup the JSON file before deleting:")
        print(f"   cp {JSON_FILE_PATH} {JSON_FILE_PATH}.backup")
    elif dry_run:
        print("\n✅ Dry run completed - run without --dry-run to actually migrate")


def verify_migration(tenant_id: str):
    """Verify migrated data in database"""
    print("\n" + "="*70)
    print("Verifying Migration")
    print("="*70)

    db = SessionLocal()
    try:
        # Count records for this tenant
        count = db.query(FlowDataModel).filter(
            FlowDataModel.tenant_id == tenant_id
        ).count()

        print(f"✅ Found {count} records in database for tenant: {tenant_id}")

        # Show sample records
        if count > 0:
            print("\nSample records:")
            sample_records = db.query(FlowDataModel).filter(
                FlowDataModel.tenant_id == tenant_id
            ).limit(5).all()

            for record in sample_records:
                print(f"   - PAN: {record.pan}, Name: {record.name}, Phone: {record.phone}")

    except Exception as e:
        print(f"❌ Error verifying migration: {str(e)}")
    finally:
        db.close()


def main():
    """Main function - parse arguments and run migration"""
    parser = argparse.ArgumentParser(
        description='Migrate flowsAPI data from JSON to PostgreSQL database'
    )
    parser.add_argument(
        '--tenant',
        required=True,
        help='Tenant ID to assign to migrated records (e.g., "ai", "default")'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Simulate migration without writing to database'
    )
    parser.add_argument(
        '--verify',
        action='store_true',
        help='Verify migration after completion'
    )

    args = parser.parse_args()

    print(f"\n🚀 FlowAPI Data Migration Tool")
    print(f"   Tenant ID: {args.tenant}")

    # Run migration
    migrate_to_database(tenant_id=args.tenant, dry_run=args.dry_run)

    # Verify if requested
    if args.verify and not args.dry_run:
        verify_migration(tenant_id=args.tenant)

    print("\n✅ Done!\n")


if __name__ == "__main__":
    main()
