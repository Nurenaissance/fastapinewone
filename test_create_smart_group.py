"""
Test script to create a smart group via API
This will help verify that auto_rules are being saved correctly
"""
import requests
import json
from datetime import datetime, timedelta

# Configuration
BASE_URL = "http://localhost:8000"  # Change to your FastAPI URL if different
TENANT_ID = "ai"  # Change to your tenant ID

def test_create_smart_group():
    """Test creating a smart group with auto rules"""

    print("=" * 80)
    print("TESTING SMART GROUP CREATION")
    print("=" * 80)

    # Define a smart group with rules
    # This will match contacts created in the last 60 days
    sixty_days_ago = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")

    smart_group_payload = {
        "name": f"Test Smart Group {datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "members": [],
        "auto_rules": {
            "enabled": True,
            "logic": "AND",
            "conditions": [
                {
                    "type": "date",
                    "field": "createdOn",
                    "operator": "greater_than",
                    "value": sixty_days_ago
                },
                {
                    "type": "date",
                    "field": "createdOn",
                    "operator": "less_than",
                    "value": tomorrow
                }
            ]
        }
    }

    print(f"\n1. Creating smart group with payload:")
    print(json.dumps(smart_group_payload, indent=2))

    try:
        # Create the smart group
        response = requests.post(
            f"{BASE_URL}/broadcast-groups/",
            headers={
                "X-Tenant-Id": TENANT_ID,
                "Content-Type": "application/json"
            },
            json=smart_group_payload
        )

        print(f"\n2. Response status: {response.status_code}")

        if response.status_code in [200, 201]:
            result = response.json()
            print(f"   SUCCESS! Group created:")
            print(f"   - ID: {result.get('id')}")
            print(f"   - Name: {result.get('name')}")
            print(f"   - Members: {len(result.get('members', []))}")
            print(f"   - auto_rules: {result.get('auto_rules')}")

            group_id = result.get('id')

            # Verify by fetching the group back
            print(f"\n3. Verifying by fetching group {group_id}...")
            verify_response = requests.get(
                f"{BASE_URL}/broadcast-groups/{group_id}/",
                headers={
                    "X-Tenant-Id": TENANT_ID
                }
            )

            if verify_response.status_code == 200:
                verified_group = verify_response.json()
                print(f"   Verified group data:")
                print(f"   - auto_rules: {verified_group.get('auto_rules')}")

                if verified_group.get('auto_rules'):
                    enabled = verified_group['auto_rules'].get('enabled')
                    print(f"   - enabled: {enabled} (type: {type(enabled).__name__})")

                    if enabled is True:
                        print("   [OK] auto_rules are properly saved!")

                        # Try syncing the group
                        print(f"\n4. Testing sync for group {group_id}...")
                        sync_response = requests.post(
                            f"{BASE_URL}/broadcast-groups/{group_id}/sync",
                            headers={
                                "X-Tenant-Id": TENANT_ID
                            }
                        )

                        if sync_response.status_code == 200:
                            sync_result = sync_response.json()
                            print(f"   Sync result: {sync_result}")

                            if sync_result.get('result', {}).get('synced') is False:
                                print(f"   [ERROR] Sync failed: {sync_result.get('result', {}).get('reason')}")
                                print(f"   THIS IS THE BUG YOU'RE SEEING!")
                            else:
                                print(f"   [OK] Sync successful!")
                                print(f"   Members after sync: {sync_result.get('result', {}).get('members_after', 0)}")
                        else:
                            print(f"   [ERROR] Sync request failed: {sync_response.status_code}")
                            print(f"   Response: {sync_response.text}")
                    else:
                        print(f"   [ERROR] enabled = {enabled}, expected True")
                        print(f"   THIS IS THE BUG!")
                else:
                    print("   [ERROR] auto_rules is None after creation!")
                    print("   THIS IS THE BUG!")
            else:
                print(f"   [ERROR] Failed to verify group: {verify_response.status_code}")
                print(f"   Response: {verify_response.text}")

        else:
            print(f"   [ERROR] Failed to create group: {response.status_code}")
            print(f"   Response: {response.text}")

    except requests.exceptions.ConnectionError:
        print("\n[ERROR] Could not connect to FastAPI server")
        print(f"Make sure the server is running at {BASE_URL}")
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)

if __name__ == "__main__":
    print("\nMake sure your FastAPI server is running before running this test!")
    print(f"Server URL: {BASE_URL}")
    print(f"Tenant ID: {TENANT_ID}\n")

    input("Press Enter to continue...")

    test_create_smart_group()
