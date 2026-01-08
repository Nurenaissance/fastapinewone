"""
Service Client for Authenticated Service-to-Service API Calls
Handles making HTTP requests with service authentication
"""

import os
import httpx
import logging
from typing import Optional, Dict, Any, Union

logger = logging.getLogger(__name__)


class ServiceClient:
    """Client for making authenticated service-to-service API calls"""

    def __init__(self, service_name: str):
        """
        Initialize service client

        Args:
            service_name: Name of the calling service ('django', 'fastapi', 'nodejs')
        """
        self.service_name = service_name
        self.service_key = os.getenv(f"{service_name.upper()}_SERVICE_KEY")

        if not self.service_key:
            logger.error(f"Missing service key for {service_name}")
            raise ValueError(
                f"Missing environment variable: {service_name.upper()}_SERVICE_KEY"
            )

        logger.info(f"✅ ServiceClient initialized for {service_name}")

    def get_headers(self, tenant_id: Optional[str] = None) -> Dict[str, str]:
        """
        Get headers for service request

        Args:
            tenant_id: Tenant ID for tenant-specific operations

        Returns:
            Dictionary of headers including service key
        """
        headers = {
            'X-Service-Key': self.service_key,
            'Content-Type': 'application/json',
        }

        if tenant_id:
            headers['X-Tenant-Id'] = tenant_id

        return headers

    async def get(
        self,
        url: str,
        tenant_id: Optional[str] = None,
        params: Optional[Dict] = None,
        timeout: float = 30.0
    ) -> Union[Dict[str, Any], list]:
        """
        Make GET request to another service

        Args:
            url: Full URL to request
            tenant_id: Tenant ID for tenant-specific operations
            params: Query parameters
            timeout: Request timeout in seconds

        Returns:
            Response data (dict or list)

        Raises:
            httpx.HTTPError: If request fails
        """
        try:
            logger.info(f"🔄 Service GET: {url} (tenant: {tenant_id})")

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    headers=self.get_headers(tenant_id),
                    params=params,
                    timeout=timeout
                )
                response.raise_for_status()

                logger.info(f"✅ Service GET success: {url} ({response.status_code})")
                return response.json()

        except httpx.HTTPError as e:
            logger.error(f"❌ Service GET failed: {url} - {str(e)}")
            raise

    async def post(
        self,
        url: str,
        data: Dict[str, Any],
        tenant_id: Optional[str] = None,
        timeout: float = 30.0
    ) -> Union[Dict[str, Any], list]:
        """
        Make POST request to another service

        Args:
            url: Full URL to request
            data: JSON data to send
            tenant_id: Tenant ID for tenant-specific operations
            timeout: Request timeout in seconds

        Returns:
            Response data (dict or list)

        Raises:
            httpx.HTTPError: If request fails
        """
        try:
            logger.info(f"🔄 Service POST: {url} (tenant: {tenant_id})")

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers=self.get_headers(tenant_id),
                    json=data,
                    timeout=timeout
                )
                response.raise_for_status()

                logger.info(f"✅ Service POST success: {url} ({response.status_code})")
                return response.json() if response.content else {}

        except httpx.HTTPError as e:
            logger.error(f"❌ Service POST failed: {url} - {str(e)}")
            raise

    async def patch(
        self,
        url: str,
        data: Dict[str, Any],
        tenant_id: Optional[str] = None,
        timeout: float = 30.0
    ) -> Union[Dict[str, Any], list]:
        """
        Make PATCH request to another service

        Args:
            url: Full URL to request
            data: JSON data to send
            tenant_id: Tenant ID for tenant-specific operations
            timeout: Request timeout in seconds

        Returns:
            Response data (dict or list)

        Raises:
            httpx.HTTPError: If request fails
        """
        try:
            logger.info(f"🔄 Service PATCH: {url} (tenant: {tenant_id})")

            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    url,
                    headers=self.get_headers(tenant_id),
                    json=data,
                    timeout=timeout
                )
                response.raise_for_status()

                logger.info(f"✅ Service PATCH success: {url} ({response.status_code})")
                return response.json() if response.content else {}

        except httpx.HTTPError as e:
            logger.error(f"❌ Service PATCH failed: {url} - {str(e)}")
            raise

    async def put(
        self,
        url: str,
        data: Dict[str, Any],
        tenant_id: Optional[str] = None,
        timeout: float = 30.0
    ) -> Union[Dict[str, Any], list]:
        """
        Make PUT request to another service

        Args:
            url: Full URL to request
            data: JSON data to send
            tenant_id: Tenant ID for tenant-specific operations
            timeout: Request timeout in seconds

        Returns:
            Response data (dict or list)

        Raises:
            httpx.HTTPError: If request fails
        """
        try:
            logger.info(f"🔄 Service PUT: {url} (tenant: {tenant_id})")

            async with httpx.AsyncClient() as client:
                response = await client.put(
                    url,
                    headers=self.get_headers(tenant_id),
                    json=data,
                    timeout=timeout
                )
                response.raise_for_status()

                logger.info(f"✅ Service PUT success: {url} ({response.status_code})")
                return response.json() if response.content else {}

        except httpx.HTTPError as e:
            logger.error(f"❌ Service PUT failed: {url} - {str(e)}")
            raise

    async def delete(
        self,
        url: str,
        tenant_id: Optional[str] = None,
        timeout: float = 30.0
    ) -> Union[Dict[str, Any], list]:
        """
        Make DELETE request to another service

        Args:
            url: Full URL to request
            tenant_id: Tenant ID for tenant-specific operations
            timeout: Request timeout in seconds

        Returns:
            Response data (dict or list, empty if no content)

        Raises:
            httpx.HTTPError: If request fails
        """
        try:
            logger.info(f"🔄 Service DELETE: {url} (tenant: {tenant_id})")

            async with httpx.AsyncClient() as client:
                response = await client.delete(
                    url,
                    headers=self.get_headers(tenant_id),
                    timeout=timeout
                )
                response.raise_for_status()

                logger.info(f"✅ Service DELETE success: {url} ({response.status_code})")
                return response.json() if response.content else {}

        except httpx.HTTPError as e:
            logger.error(f"❌ Service DELETE failed: {url} - {str(e)}")
            raise


# Synchronous version for Django (doesn't support async natively in views)
class SyncServiceClient:
    """Synchronous service client for Django"""

    def __init__(self, service_name: str):
        """
        Initialize synchronous service client

        Args:
            service_name: Name of the calling service ('django', 'fastapi', 'nodejs')
        """
        self.service_name = service_name
        self.service_key = os.getenv(f"{service_name.upper()}_SERVICE_KEY")

        if not self.service_key:
            logger.error(f"Missing service key for {service_name}")
            raise ValueError(
                f"Missing environment variable: {service_name.upper()}_SERVICE_KEY"
            )

        logger.info(f"✅ SyncServiceClient initialized for {service_name}")

    def get_headers(self, tenant_id: Optional[str] = None) -> Dict[str, str]:
        """Get headers for service request"""
        headers = {
            'X-Service-Key': self.service_key,
            'Content-Type': 'application/json',
        }

        if tenant_id:
            headers['X-Tenant-Id'] = tenant_id

        return headers

    def get(
        self,
        url: str,
        tenant_id: Optional[str] = None,
        params: Optional[Dict] = None,
        timeout: float = 30.0
    ) -> Union[Dict[str, Any], list]:
        """Make synchronous GET request"""
        try:
            logger.info(f"🔄 Sync Service GET: {url} (tenant: {tenant_id})")

            with httpx.Client() as client:
                response = client.get(
                    url,
                    headers=self.get_headers(tenant_id),
                    params=params,
                    timeout=timeout
                )
                response.raise_for_status()

                logger.info(f"✅ Sync Service GET success: {url}")
                return response.json()

        except httpx.HTTPError as e:
            logger.error(f"❌ Sync Service GET failed: {url} - {str(e)}")
            raise

    def post(
        self,
        url: str,
        data: Dict[str, Any],
        tenant_id: Optional[str] = None,
        timeout: float = 30.0
    ) -> Union[Dict[str, Any], list]:
        """Make synchronous POST request"""
        try:
            logger.info(f"🔄 Sync Service POST: {url} (tenant: {tenant_id})")

            with httpx.Client() as client:
                response = client.post(
                    url,
                    headers=self.get_headers(tenant_id),
                    json=data,
                    timeout=timeout
                )
                response.raise_for_status()

                logger.info(f"✅ Sync Service POST success: {url}")
                return response.json() if response.content else {}

        except httpx.HTTPError as e:
            logger.error(f"❌ Sync Service POST failed: {url} - {str(e)}")
            raise

    def patch(
        self,
        url: str,
        data: Dict[str, Any],
        tenant_id: Optional[str] = None,
        timeout: float = 30.0
    ) -> Union[Dict[str, Any], list]:
        """Make synchronous PATCH request"""
        try:
            logger.info(f"🔄 Sync Service PATCH: {url} (tenant: {tenant_id})")

            with httpx.Client() as client:
                response = client.patch(
                    url,
                    headers=self.get_headers(tenant_id),
                    json=data,
                    timeout=timeout
                )
                response.raise_for_status()

                logger.info(f"✅ Sync Service PATCH success: {url}")
                return response.json() if response.content else {}

        except httpx.HTTPError as e:
            logger.error(f"❌ Sync Service PATCH failed: {url} - {str(e)}")
            raise

    def delete(
        self,
        url: str,
        tenant_id: Optional[str] = None,
        timeout: float = 30.0
    ) -> Union[Dict[str, Any], list]:
        """Make synchronous DELETE request"""
        try:
            logger.info(f"🔄 Sync Service DELETE: {url} (tenant: {tenant_id})")

            with httpx.Client() as client:
                response = client.delete(
                    url,
                    headers=self.get_headers(tenant_id),
                    timeout=timeout
                )
                response.raise_for_status()

                logger.info(f"✅ Sync Service DELETE success: {url}")
                return response.json() if response.content else {}

        except httpx.HTTPError as e:
            logger.error(f"❌ Sync Service DELETE failed: {url} - {str(e)}")
            raise
