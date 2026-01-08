"""
Service Authentication Key Management
Generates and validates service-to-service API keys
"""

import secrets
import hashlib
import os
from typing import Tuple, Optional


class ServiceAuthManager:
    """Manage service-to-service authentication"""

    # Service API keys (loaded from environment variables)
    SERVICE_KEYS = {
        'django': None,
        'fastapi': None,
        'nodejs': None,
    }

    @classmethod
    def load_from_env(cls):
        """Load service keys from environment variables"""
        cls.SERVICE_KEYS['django'] = os.getenv('DJANGO_SERVICE_KEY')
        cls.SERVICE_KEYS['fastapi'] = os.getenv('FASTAPI_SERVICE_KEY')
        cls.SERVICE_KEYS['nodejs'] = os.getenv('NODEJS_SERVICE_KEY')

        # Validate that all keys are loaded
        missing = [name for name, key in cls.SERVICE_KEYS.items() if not key]
        if missing:
            print(f"⚠️  Warning: Missing service keys for: {', '.join(missing)}")

    @classmethod
    def generate_service_key(cls, service_name: str) -> str:
        """
        Generate a new service API key

        Args:
            service_name: Name of the service (django, fastapi, nodejs)

        Returns:
            Generated service key in format: sk_{service}_{random}
        """
        random_part = secrets.token_urlsafe(32)
        key = f"sk_{service_name}_{random_part}"
        return key

    @classmethod
    def hash_key(cls, key: str) -> str:
        """
        Hash a service key for secure storage

        Args:
            key: Service key to hash

        Returns:
            SHA256 hash of the key
        """
        return hashlib.sha256(key.encode()).hexdigest()

    @classmethod
    def verify_service_key(cls, provided_key: str) -> Tuple[bool, Optional[str]]:
        """
        Verify if provided key is valid for any service

        Args:
            provided_key: The API key to verify

        Returns:
            Tuple of (is_valid, service_name)
        """
        for service_name, stored_key in cls.SERVICE_KEYS.items():
            if stored_key and provided_key == stored_key:
                return True, service_name

        return False, None


def generate_all_keys():
    """Generate service keys for all services"""
    print("=" * 70)
    print("SERVICE AUTHENTICATION KEYS")
    print("=" * 70)
    print("\nGenerate these keys and add to .env files in ALL services:")
    print("(Django, FastAPI, and Node.js must all have the same keys)\n")
    print("-" * 70)

    keys = {}
    for service in ['django', 'fastapi', 'nodejs']:
        key = ServiceAuthManager.generate_service_key(service)
        keys[service] = key
        print(f"{service.upper()}_SERVICE_KEY={key}")

    print("-" * 70)
    print("\nIMPORTANT:")
    print("1. Copy ALL three keys to .env file in each service")
    print("2. All services need all keys to validate each other")
    print("3. Keep these keys secret - do NOT commit to git")
    print("4. Add .env to .gitignore if not already present")
    print("\n" + "=" * 70)

    return keys


if __name__ == "__main__":
    generate_all_keys()
