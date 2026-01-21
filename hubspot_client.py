"""
HubSpot API Client for syncing Lemlist metrics to HubSpot contacts.

Provides batch update functionality for contact properties with
rate limiting and error handling.
"""

import requests
import time
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class HubSpotError(Exception):
    """Base exception for HubSpot API errors"""
    pass


class HubSpotRateLimitError(HubSpotError):
    """Raised when HubSpot rate limit is exceeded"""
    def __init__(self, retry_after: int = 10):
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded. Retry after {retry_after}s")


class HubSpotUnauthorizedError(HubSpotError):
    """Raised when HubSpot token is invalid"""
    pass


class HubSpotNotFoundError(HubSpotError):
    """Raised when contact is not found"""
    pass


class HubSpotClient:
    """Client for HubSpot API interactions.

    Handles contact property updates with rate limiting and error handling.

    Rate Limits:
    - Standard: 100 requests/10 seconds
    - Batch APIs: 4 requests/second
    """

    BASE_URL = "https://api.hubapi.com"

    # Default timeout for API requests (seconds)
    DEFAULT_TIMEOUT = 30

    def __init__(self, api_token: str):
        """Initialize HubSpot client.

        Args:
            api_token: HubSpot Private App access token
        """
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {api_token}',
            'Content-Type': 'application/json'
        })

    def _make_request(self, method: str, endpoint: str,
                      max_retries: int = 3, **kwargs) -> requests.Response:
        """Make HTTP request with retry logic and error handling.

        Args:
            method: HTTP method (GET, POST, PATCH, etc.)
            endpoint: API endpoint (without base URL)
            max_retries: Maximum retry attempts for rate limits
            **kwargs: Additional arguments for requests

        Returns:
            Response object

        Raises:
            HubSpotUnauthorizedError: Invalid token
            HubSpotNotFoundError: Contact not found
            HubSpotRateLimitError: Rate limit exceeded after retries
            HubSpotError: Other API errors
        """
        url = f"{self.BASE_URL}{endpoint}"

        # Set default timeout if not specified
        if 'timeout' not in kwargs:
            kwargs['timeout'] = self.DEFAULT_TIMEOUT

        for attempt in range(max_retries):
            try:
                response = self.session.request(method, url, **kwargs)

                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 10))
                    logger.warning(f"HubSpot rate limit hit. Waiting {retry_after}s...")

                    if attempt < max_retries - 1:
                        time.sleep(retry_after)
                        continue
                    else:
                        raise HubSpotRateLimitError(retry_after)

                # Handle auth errors
                if response.status_code == 401:
                    raise HubSpotUnauthorizedError("Invalid HubSpot API token")

                # Handle not found
                if response.status_code == 404:
                    raise HubSpotNotFoundError("Contact not found in HubSpot")

                # Handle other errors
                if response.status_code >= 400:
                    error_msg = response.text
                    try:
                        error_data = response.json()
                        error_msg = error_data.get('message', error_msg)
                    except Exception:
                        pass
                    raise HubSpotError(f"HubSpot API error ({response.status_code}): {error_msg}")

                return response

            except requests.exceptions.Timeout:
                logger.warning(f"HubSpot request timeout (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                    continue
                raise HubSpotError("Request timeout after retries")

            except requests.exceptions.RequestException as e:
                logger.error(f"HubSpot request error: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise HubSpotError(f"Request failed: {e}")

        raise HubSpotError("Max retries exceeded")

    def update_contact_properties(self, hubspot_id: str,
                                   properties: Dict[str, Any]) -> Dict:
        """Update properties for a single contact.

        Args:
            hubspot_id: HubSpot contact ID
            properties: Dict of property names and values

        Returns:
            Updated contact data

        Example:
            client.update_contact_properties("12345", {
                "lemlist_total_activities": 15,
                "lemlist_engagement_score": 42
            })
        """
        endpoint = f"/crm/v3/objects/contacts/{hubspot_id}"

        response = self._make_request(
            'PATCH',
            endpoint,
            json={"properties": properties}
        )

        return response.json()

    def batch_update_contacts(self, updates: List[Dict]) -> Dict:
        """Batch update properties for multiple contacts.

        Args:
            updates: List of dicts with 'id' and 'properties' keys
                     Maximum 100 contacts per batch

        Returns:
            Batch update response with results and errors

        Example:
            client.batch_update_contacts([
                {
                    "id": "12345",
                    "properties": {"lemlist_total_activities": 15}
                },
                {
                    "id": "67890",
                    "properties": {"lemlist_total_activities": 8}
                }
            ])

        Note:
            Rate limit for batch API: 4 requests/second
            Use 0.25s delay between batch calls
        """
        if not updates:
            return {"results": [], "errors": []}

        if len(updates) > 100:
            raise ValueError("Maximum 100 contacts per batch")

        endpoint = "/crm/v3/objects/contacts/batch/update"

        response = self._make_request(
            'POST',
            endpoint,
            json={"inputs": updates}
        )

        return response.json()

    def get_contact(self, hubspot_id: str) -> Optional[Dict]:
        """Get contact by ID (for verification).

        Args:
            hubspot_id: HubSpot contact ID

        Returns:
            Contact data or None if not found
        """
        try:
            endpoint = f"/crm/v3/objects/contacts/{hubspot_id}"
            response = self._make_request('GET', endpoint)
            return response.json()
        except HubSpotNotFoundError:
            return None

    def verify_token(self) -> bool:
        """Verify that the API token is valid.

        Returns:
            True if token is valid, False otherwise

        Raises:
            HubSpotError: For non-auth related errors (network, rate limit, etc.)
        """
        try:
            # Simple API call to verify auth
            endpoint = "/crm/v3/objects/contacts?limit=1"
            self._make_request('GET', endpoint)
            return True
        except HubSpotUnauthorizedError:
            return False
        # Let other errors propagate - they indicate issues that should be handled

    def get_all_contacts(self, properties: List[str], limit: int = 100) -> List[Dict]:
        """Fetch all contacts with specified properties using cursor-based pagination.

        Args:
            properties: List of property names to fetch (e.g., ['jobtitle', 'hs_seniority'])
            limit: Number of contacts per page (max 100)

        Returns:
            List of contact dicts with 'id' and 'properties' keys

        Example:
            contacts = client.get_all_contacts(['jobtitle', 'hs_seniority'])
            for contact in contacts:
                print(contact['id'], contact['properties'].get('jobtitle'))
        """
        all_contacts = []
        after = None

        # Build properties query string
        properties_param = ','.join(properties)

        while True:
            endpoint = f"/crm/v3/objects/contacts?limit={limit}&properties={properties_param}"
            if after:
                endpoint += f"&after={after}"

            response = self._make_request('GET', endpoint)
            data = response.json()

            contacts = data.get('results', [])
            all_contacts.extend(contacts)

            # Check for next page (cursor-based pagination)
            paging = data.get('paging', {})
            if paging.get('next', {}).get('after'):
                after = paging['next']['after']
            else:
                break

        return all_contacts

    def get_all_contacts_with_companies(self, contact_properties: List[str],
                                         limit: int = 100) -> List[Dict]:
        """Fetch all contacts with their company associations.

        Args:
            contact_properties: List of contact property names to fetch
            limit: Number of contacts per page (max 100)

        Returns:
            List of contact dicts with 'id', 'properties', and 'associations' keys.
            The 'associations' dict contains 'companies' with list of associated company IDs.

        Example:
            contacts = client.get_all_contacts_with_companies(['industry_fit'])
            for contact in contacts:
                companies = contact.get('associations', {}).get('companies', {}).get('results', [])
                for assoc in companies:
                    print(f"Contact {contact['id']} → Company {assoc['id']}")
        """
        all_contacts = []
        after = None

        # Build properties query string
        properties_param = ','.join(contact_properties)

        while True:
            endpoint = f"/crm/v3/objects/contacts?limit={limit}&properties={properties_param}&associations=companies"
            if after:
                endpoint += f"&after={after}"

            response = self._make_request('GET', endpoint)
            data = response.json()

            contacts = data.get('results', [])
            all_contacts.extend(contacts)

            # Check for next page (cursor-based pagination)
            paging = data.get('paging', {})
            if paging.get('next', {}).get('after'):
                after = paging['next']['after']
            else:
                break

        return all_contacts

    def batch_get_companies(self, company_ids: List[str],
                            properties: List[str]) -> Dict[str, Dict]:
        """Fetch multiple companies by ID in a single batch request.

        Args:
            company_ids: List of company IDs to fetch (max 100 per batch)
            properties: List of property names to fetch (e.g., ['industry', 'name'])

        Returns:
            Dict mapping company_id to company data: {id: {properties: {...}}}

        Example:
            companies = client.batch_get_companies(['123', '456'], ['industry'])
            for cid, data in companies.items():
                print(f"Company {cid}: {data['properties'].get('industry')}")
        """
        if not company_ids:
            return {}

        result = {}

        # Process in batches of 100
        for i in range(0, len(company_ids), 100):
            batch_ids = company_ids[i:i + 100]

            endpoint = "/crm/v3/objects/companies/batch/read"
            payload = {
                "inputs": [{"id": cid} for cid in batch_ids],
                "properties": properties
            }

            response = self._make_request('POST', endpoint, json=payload)
            data = response.json()

            for company in data.get('results', []):
                result[company['id']] = company

        return result

    # =========================================================================
    # Notes API Methods
    # =========================================================================

    def get_notes_for_contact(self, contact_id: str) -> List[Dict]:
        """Fetch all notes associated with a contact.

        Args:
            contact_id: HubSpot contact ID

        Returns:
            List of note objects with properties and associations

        Note:
            Uses the associations endpoint to get notes linked to a contact.
            Handles pagination automatically.
        """
        all_notes = []
        after = None

        while True:
            # First get the note IDs associated with this contact
            endpoint = f"/crm/v4/objects/contacts/{contact_id}/associations/notes"
            params = {"limit": 100}
            if after:
                params["after"] = after

            try:
                response = self._make_request('GET', endpoint, params=params)
                data = response.json()
            except HubSpotNotFoundError:
                # Contact has no notes
                return []

            note_ids = [r['toObjectId'] for r in data.get('results', [])]

            if not note_ids:
                break

            # Now fetch the actual note details
            for note_id in note_ids:
                note = self.get_note(note_id)
                if note:
                    note['associated_contact_id'] = contact_id
                    all_notes.append(note)

            # Check for pagination
            paging = data.get('paging', {})
            if paging.get('next', {}).get('after'):
                after = paging['next']['after']
            else:
                break

        return all_notes

    def get_note(self, note_id: str) -> Optional[Dict]:
        """Get a single note by ID.

        Args:
            note_id: HubSpot note ID

        Returns:
            Note object with properties, or None if not found
        """
        try:
            endpoint = f"/crm/v3/objects/notes/{note_id}"
            params = {"properties": "hs_note_body,hs_timestamp,hs_createdate"}
            response = self._make_request('GET', endpoint, params=params)
            return response.json()
        except HubSpotNotFoundError:
            return None

    def delete_note(self, note_id: str) -> bool:
        """Delete a single note.

        Args:
            note_id: HubSpot note ID to delete

        Returns:
            True if deleted successfully
        """
        endpoint = f"/crm/v3/objects/notes/{note_id}"
        response = self._make_request('DELETE', endpoint)
        return response.status_code == 204

    def batch_delete_notes(self, note_ids: List[str]) -> Dict:
        """Batch delete multiple notes.

        Args:
            note_ids: List of note IDs to delete (max 100)

        Returns:
            Dict with deletion results

        Note:
            Rate limit for batch API: 4 requests/second
        """
        if not note_ids:
            return {"deleted": 0, "errors": []}

        if len(note_ids) > 100:
            raise ValueError("Maximum 100 notes per batch delete")

        endpoint = "/crm/v3/objects/notes/batch/archive"
        payload = {"inputs": [{"id": str(nid)} for nid in note_ids]}

        response = self._make_request('POST', endpoint, json=payload)

        # Archive endpoint returns 204 No Content on success
        if response.status_code == 204:
            return {"deleted": len(note_ids), "errors": []}

        return response.json()
