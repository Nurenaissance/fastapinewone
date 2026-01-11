"""
Diagnostic script to check smart groups database state
Run this to verify if auto_rules are being saved correctly
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, inspect, text
from config.database import get_db, DATABASE_URL
from whatsapp_tenant.models import BroadcastGroups

def check_database_state():
    """Check if auto_rules column exists and what data is stored"""

    print("=" * 80)
    print("SMART GROUPS DATABASE DIAGNOSTIC")
    print("=" * 80)

    # Create engine
    engine = create_engine(DATABASE_URL)

    # Check if auto_rules column exists
    print("\n1. Checking if 'auto_rules' column exists in broadcast_groups table...")
    inspector = inspect(engine)
    columns = inspector.get_columns('broadcast_groups')

    column_names = [col['name'] for col in columns]
    print(f"   Columns found: {column_names}")

    if 'auto_rules' in column_names:
        print("   [OK] auto_rules column EXISTS")
    else:
        print("   [ERROR] auto_rules column MISSING - Migration not applied!")
        print("   \nFIX: Run the migration:")
        print("   cd fastAPIWhatsapp_withclaude")
        print("   alembic upgrade head")
        return False

    # Check existing smart groups
    print("\n2. Checking existing broadcast groups...")
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT id, name, auto_rules
            FROM broadcast_groups
            LIMIT 10
        """))

        groups = result.fetchall()

        if not groups:
            print("   No groups found in database")
        else:
            print(f"   Found {len(groups)} groups:\n")
            for group in groups:
                group_id, name, auto_rules = group
                print(f"   Group: {name} (ID: {group_id})")
                print(f"   auto_rules: {auto_rules}")

                if auto_rules:
                    if isinstance(auto_rules, dict):
                        enabled = auto_rules.get('enabled', 'KEY MISSING')
                        print(f"   enabled: {enabled} (type: {type(enabled).__name__})")

                        if enabled is True:
                            print("   [OK] Rules are properly enabled")
                        elif enabled == 'true':
                            print("   [WARNING]  WARNING: enabled is STRING 'true', not boolean True")
                        elif enabled == 'KEY MISSING':
                            print("   [ERROR] ERROR: 'enabled' key is missing from auto_rules")
                        else:
                            print(f"   [ERROR] ERROR: enabled = {enabled}")
                    else:
                        print(f"   [ERROR] ERROR: auto_rules is not a dict, it's {type(auto_rules).__name__}")
                else:
                    print("   [INFO]  No auto_rules (regular group)")
                print()

    print("\n3. Testing with SQLAlchemy ORM...")
    db = next(get_db())
    try:
        orm_groups = db.query(BroadcastGroups).limit(5).all()

        for group in orm_groups:
            print(f"\n   Group: {group.name}")
            print(f"   auto_rules type: {type(group.auto_rules)}")
            print(f"   auto_rules value: {group.auto_rules}")

            if group.auto_rules:
                enabled = group.auto_rules.get('enabled')
                print(f"   enabled value: {enabled} (type: {type(enabled).__name__})")
                print(f"   Condition check: {not group.auto_rules or not group.auto_rules.get('enabled')}")

                if enabled is True:
                    print("   [OK] Would PASS the sync check")
                else:
                    print("   [ERROR] Would FAIL the sync check - This is your problem!")
    finally:
        db.close()

    print("\n" + "=" * 80)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 80)

    return True

if __name__ == "__main__":
    try:
        check_database_state()
    except Exception as e:
        print(f"\n[ERROR] ERROR: {e}")
        import traceback
        traceback.print_exc()
