"""
Lemlist API Client for campaign and lead operations.

Provides:
- Campaign listing
- Activity retrieval with pagination
- Lead details fetching
- Rate limit monitoring
"""

import logging
import time
from typing import Dict, List, Optional

from .base_client import BaseAPIClient, UnauthorizedError, NotFoundError
from .config import LemlistConfig

logger = logging.getLogger(__name__)


class LemlistClient(BaseAPIClient):
    """Client for Lemlist API interactions.

    Handles:
    - Campaign listing
    - Activity fetching with offset-based pagination
    - Lead details retrieval
    - Rate limit monitoring via X-RateLimit headers

    Rate Limits:
    - 20 requests per 2 seconds per API key
    - Monitor X-RateLimit-Remaining header
    """

    def __init__(self, config: LemlistConfig):
        """Initialize Lemlist client.

        Args:
            config: LemlistConfig with API key and settings
        """
        super().__init__(config.base_url, config.default_timeout)
        self.config = config

        # Basic Auth: empty username, API key as password
        self.session.auth = ('', config.api_key)
        self.session.headers.update({
            'User-Agent': 'Lemlist-API-Client/1.0'
        })

    def verify_token(self) -> bool:
        """Verify that the API key is valid.

        Returns:
            True if API key is valid, False otherwise

        Raises:
            APIError: For non-auth related errors
        """
        try:
            endpoint = "/campaigns?limit=1"
            self._make_request('GET', endpoint)
            return True
        except UnauthorizedError:
            return False

    def _make_request(self, method: str, endpoint: str,
                     params: Optional[Dict] = None,
                     json: Optional[Dict] = None,
                     max_retries: int = None,
                     **kwargs):
        """Override to add rate limit monitoring.

        Monitors X-RateLimit-Remaining header and notifies when low.
        """
        if max_retries is None:
            max_retries = self.config.max_retries

        response = super()._make_request(
            method, endpoint, params, json, max_retries, **kwargs
        )

        # Check rate limit headers
        rate_limit_remaining = response.headers.get('X-RateLimit-Remaining')
        if rate_limit_remaining and int(rate_limit_remaining) < self.config.rate_limit_threshold:
            reset_time = response.headers.get('X-RateLimit-Reset')
            if reset_time:
                wait_time = max(0, int(reset_time) - time.time())
                if wait_time > 0:
                    self._notify_low_rate_limit(int(rate_limit_remaining), wait_time)

        return response

    def _notify_low_rate_limit(self, remaining: int, reset_in: int):
        """Notify about low rate limit. Override for custom behavior."""
        logger.warning(f"Rate limit low: {remaining} remaining, resets in {reset_in}s")

    def _parse_json_response(self, response) -> List[Dict]:
        """Safely parse JSON response.

        Args:
            response: Response object

        Returns:
            List of data (empty list if empty response or error)
        """
        try:
            if not response.content:
                return []

            data = response.json()

            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                return [data]
            else:
                logger.error(f"Unexpected response format: {type(data)}")
                return []

        except Exception as e:
            logger.error(f"Error parsing JSON response: {e}")
            return []

    # =========================================================================
    # Campaign Operations
    # =========================================================================

    def get_all_campaigns(self, status: Optional[str] = None) -> List[Dict]:
        """Fetch all campaigns with pagination.

        Args:
            status: Filter by status ('running', 'draft', 'archived', 'ended', 'paused', 'errors')

        Returns:
            List of campaign dictionaries with _id, name, status, etc.

        Example:
            campaigns = client.get_all_campaigns(status='running')
            for campaign in campaigns:
                print(campaign['_id'], campaign['name'])
        """
        all_campaigns = []
        offset = 0
        limit = self.config.pagination_limit

        while True:
            params = {'limit': limit, 'offset': offset}
            if status:
                params['status'] = status

            response = self._make_request('GET', "/campaigns", params=params)
            campaigns = self._parse_json_response(response)

            if not campaigns:
                break

            all_campaigns.extend(campaigns)

            # Check if we've reached the end
            if len(campaigns) < limit:
                break

            offset += limit
            time.sleep(self.config.pagination_delay)

        return all_campaigns

    # =========================================================================
    # Activity Operations
    # =========================================================================

    def get_all_activities(self, campaign_id: str) -> List[Dict]:
        """Fetch all activities for a campaign with pagination.

        Args:
            campaign_id: Campaign ID

        Returns:
            List of activity dictionaries

        Example:
            activities = client.get_all_activities('cmp_123')
            for activity in activities:
                print(activity['type'], activity['leadEmail'])

        Note:
            Raw activities returned - filtering and deduplication should be
            done by the application layer.
        """
        all_activities = []
        offset = 0
        limit = self.config.pagination_limit

        while True:
            params = {'campaignId': campaign_id, 'limit': limit, 'offset': offset}
            response = self._make_request('GET', "/activities", params=params)
            activities = self._parse_json_response(response)

            if not activities:
                break

            all_activities.extend(activities)

            # Check if we've reached the end
            if len(activities) < limit:
                break

            offset += limit
            time.sleep(self.config.pagination_delay)

        return all_activities

    # =========================================================================
    # Lead Operations
    # =========================================================================

    def get_lead_details(self, email: str) -> Dict:
        """Fetch detailed information for a specific lead by email.

        Args:
            email: Lead email address

        Returns:
            Dict with lead details including hubspotLeadId, linkedinUrl, etc.
            Empty dict if not found.

        Example:
            lead = client.get_lead_details('john@example.com')
            hubspot_id = lead.get('hubspotLeadId')
        """
        try:
            response = self._make_request('GET', f"/leads/{email}")
            leads = self._parse_json_response(response)

            # API returns a list with a single lead
            if leads and len(leads) > 0:
                return leads[0]
            return {}
        except NotFoundError:
            return {}
