"""
Manual migration script to add manual_mode and auto_rules columns
Run this with: python run_migrations.py
"""
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get database URL from environment or construct it
DATABASE_URL = os.getenv('DATABASE_URL') or f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME')}"

print(f"Connecting to database...")
engine = create_engine(DATABASE_URL)

migrations = [
    {
        'name': 'Add auto_rules to broadcast_groups',
        'sql': 'ALTER TABLE broadcast_groups ADD COLUMN IF NOT EXISTS auto_rules JSON NULL;',
        'check': "SELECT column_name FROM information_schema.columns WHERE table_name='broadcast_groups' AND column_name='auto_rules';"
    },
    {
        'name': 'Add manual_mode to contacts_contact',
        'sql': 'ALTER TABLE contacts_contact ADD COLUMN IF NOT EXISTS manual_mode BOOLEAN DEFAULT FALSE NULL;',
        'check': "SELECT column_name FROM information_schema.columns WHERE table_name='contacts_contact' AND column_name='manual_mode';"
    }
]

with engine.connect() as conn:
    for migration in migrations:
        print(f"\n{'='*60}")
        print(f"Migration: {migration['name']}")
        print(f"{'='*60}")

        # Check if column already exists
        result = conn.execute(text(migration['check']))
        exists = result.fetchone() is not None

        if exists:
            print(f"✓ Column already exists, skipping...")
            continue

        try:
            # Run migration
            print(f"Running: {migration['sql']}")
            conn.execute(text(migration['sql']))
            conn.commit()
            print(f"✓ Migration successful!")
        except Exception as e:
            print(f"✗ Migration failed: {str(e)}")
            conn.rollback()

print(f"\n{'='*60}")
print("All migrations completed!")
print(f"{'='*60}\n")
