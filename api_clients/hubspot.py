"""
HubSpot API Client for CRM operations.

Provides:
- Contact property updates (single and batch)
- Company data retrieval
- Notes management
- Cursor-based pagination
"""

import logging
from typing import Dict, List, Any, Optional

from .base_client import BaseAPIClient, UnauthorizedError, NotFoundError
from .config import HubSpotConfig

logger = logging.getLogger(__name__)


class HubSpotClient(BaseAPIClient):
    """Client for HubSpot CRM API interactions.

    Handles:
    - Contact and company CRUD operations
    - Batch updates with rate limiting
    - Notes management
    - Association handling

    Rate Limits:
    - Standard: 100 requests/10 seconds
    - Batch APIs: 4 requests/second
    """

    def __init__(self, config: HubSpotConfig):
        """Initialize HubSpot client.

        Args:
            config: HubSpotConfig with API token and settings
        """
        super().__init__(config.base_url, config.default_timeout)
        self.config = config

        # Set authorization header
        self.session.headers.update({
            'Authorization': f'Bearer {config.api_token}',
            'Content-Type': 'application/json'
        })

    def verify_token(self) -> bool:
        """Verify that the API token is valid.

        Returns:
            True if token is valid, False otherwise

        Raises:
            APIError: For non-auth related errors
        """
        try:
            endpoint = "/crm/v3/objects/contacts?limit=1"
            self._make_request('GET', endpoint)
            return True
        except UnauthorizedError:
            return False

    # =========================================================================
    # Contact Operations
    # =========================================================================

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
                "firstname": "John",
                "company": "Acme Inc"
            })
        """
        endpoint = f"/crm/v3/objects/contacts/{hubspot_id}"
        response = self._make_request(
            'PATCH',
            endpoint,
            json={"properties": properties},
            max_retries=self.config.max_retries
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
                    "properties": {"company": "Acme Inc"}
                },
                {
                    "id": "67890",
                    "properties": {"company": "Tech Corp"}
                }
            ])

        Note:
            Rate limit for batch API: 4 requests/second
            Use config.batch_delay between batch calls
        """
        if not updates:
            return {"results": [], "errors": []}

        if len(updates) > 100:
            raise ValueError("Maximum 100 contacts per batch")

        endpoint = "/crm/v3/objects/contacts/batch/update"
        response = self._make_request(
            'POST',
            endpoint,
            json={"inputs": updates},
            max_retries=self.config.max_retries
        )
        return response.json()

    def get_contact(self, hubspot_id: str) -> Optional[Dict]:
        """Get contact by ID.

        Args:
            hubspot_id: HubSpot contact ID

        Returns:
            Contact data or None if not found
        """
        try:
            endpoint = f"/crm/v3/objects/contacts/{hubspot_id}"
            response = self._make_request('GET', endpoint)
            return response.json()
        except NotFoundError:
            return None

    def get_all_contacts(self, properties: List[str], limit: int = 100) -> List[Dict]:
        """Fetch all contacts with specified properties using cursor-based pagination.

        Args:
            properties: List of property names to fetch
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
        properties_param = ','.join(properties)

        while True:
            endpoint = f"/crm/v3/objects/contacts?limit={limit}&properties={properties_param}"
            if after:
                endpoint += f"&after={after}"

            response = self._make_request('GET', endpoint)
            data = response.json()

            contacts = data.get('results', [])
            all_contacts.extend(contacts)

            # Check for next page
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
            List of contact dicts with 'id', 'properties', and 'associations' keys

        Example:
            contacts = client.get_all_contacts_with_companies(['email', 'company'])
            for contact in contacts:
                companies = contact.get('associations', {}).get('companies', {}).get('results', [])
        """
        all_contacts = []
        after = None
        properties_param = ','.join(contact_properties)

        while True:
            endpoint = f"/crm/v3/objects/contacts?limit={limit}&properties={properties_param}&associations=companies"
            if after:
                endpoint += f"&after={after}"

            response = self._make_request('GET', endpoint)
            data = response.json()

            contacts = data.get('results', [])
            all_contacts.extend(contacts)

            # Check for next page
            paging = data.get('paging', {})
            if paging.get('next', {}).get('after'):
                after = paging['next']['after']
            else:
                break

        return all_contacts

    # =========================================================================
    # Company Operations
    # =========================================================================

    def batch_get_companies(self, company_ids: List[str],
                            properties: List[str]) -> Dict[str, Dict]:
        """Fetch multiple companies by ID in a single batch request.

        Args:
            company_ids: List of company IDs to fetch (max 100 per batch)
            properties: List of property names to fetch (e.g., ['industry', 'name'])

        Returns:
            Dict mapping company_id to company data

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
    # Notes Operations
    # =========================================================================

    def get_notes_for_contact(self, contact_id: str) -> List[Dict]:
        """Fetch all notes associated with a contact.

        Args:
            contact_id: HubSpot contact ID

        Returns:
            List of note objects with properties

        Note:
            Uses the associations endpoint. Handles pagination automatically.
        """
        all_notes = []
        after = None

        while True:
            endpoint = f"/crm/v4/objects/contacts/{contact_id}/associations/notes"
            params = {"limit": 100}
            if after:
                params["after"] = after

            try:
                response = self._make_request('GET', endpoint, params=params)
                data = response.json()
            except NotFoundError:
                return []

            note_ids = [r['toObjectId'] for r in data.get('results', [])]

            if not note_ids:
                break

            # Fetch actual note details
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
        except NotFoundError:
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
