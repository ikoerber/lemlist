import streamlit as st
import pandas as pd
import requests
import re
from datetime import datetime
import time
import os
from typing import List, Dict, Optional
from dotenv import load_dotenv
from db import LemlistDB
from hubspot_client import (
    HubSpotClient, HubSpotError, HubSpotUnauthorizedError,
    HubSpotRateLimitError, HubSpotNotFoundError
)
from hubspot_notes_analyzer import NotesAnalyzer, LemlistNoteParser
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

# Activity types to filter out (not useful for analysis)
FILTERED_ACTIVITY_TYPES = {'hasEmailAddress', 'conditionChosen'}

# Activity types to deduplicate (keep only first occurrence per unique key)
# Key for emailsOpened: (leadEmail, emailTemplateId, sequenceStep)
DEDUPLICATE_ACTIVITY_TYPES = {'emailsOpened'}

# API Rate Limiting
API_RATE_LIMIT_DELAY = 0.15  # Seconds between API calls to respect rate limits
API_PAGINATION_DELAY = 0.1  # Seconds between pagination requests

# Lead Processing
MAX_LEADS_INITIAL_FETCH = 50  # Max leads to fetch HubSpot IDs for on initial load
LEAD_BATCH_SIZE = 50  # Batch size for lead details fetching
LEAD_BATCH_PAUSE = 2.0  # Seconds to pause between batches

# HubSpot Sync
HUBSPOT_BATCH_SIZE = 50  # Contacts per batch for HubSpot sync
HUBSPOT_BATCH_DELAY = 0.25  # Seconds between batch API calls (4 req/sec limit)

# Job Level Keywords for classification (matching HubSpot property values)
# Order matters: check from most senior to least senior
# Uses word-boundary matching to avoid false positives (e.g., 'cto' in 'director')
JOB_LEVEL_KEYWORDS = {
    'owner': [
        # C-Level (DE + EN) - exact abbreviations need word boundaries
        r'\bceo\b', r'\bcto\b', r'\bcfo\b', r'\bcmo\b', r'\bcoo\b', r'\bcio\b', r'\bcpo\b', r'\bcro\b',
        r'\bchief\b', r'\bpresident\b', r'\bowner\b', r'\bfounder\b', r'\bco-founder\b',
        r'\bpartner\b', r'\binhaber\b', r'\bgeschäftsführer\b', r'\bvorstand\b',
        r'\bmanaging director\b', r'\bgeneral manager\b',
        # German abbreviations - be careful with these
        r'\bgf\b', r'\bgm\b',
    ],
    'director': [
        # Director/VP level (DE + EN)
        r'\bdirector\b', r'\bdirektor\b', r'\bvp\b', r'\bvice president\b', r'\bsvp\b', r'\bevp\b',
        r'\bhead of\b',
        # German: High-level "leiter" roles (Bereichs-, Abteilungs-, Vertriebs-)
        r'\bbereichsleiter', r'\babteilungsleiter', r'\bvertriebsleiter',
        r'\bleiter\b', r'\bleiterin\b', r'\bleitung\b',  # Standalone "Leiter"
    ],
    'manager': [
        # Manager level (DE + EN)
        # Match "manager" at end of word for German compounds (Marketingmanager, Produktmanager)
        r'manager\b', r'managerin\b',
        # Team/Project leads are manager level, not director
        r'\bteam\s*lead', r'\bteamleiter', r'\bteamlead', r'\bteamleiterin\b',
        r'\bprojektleiter', r'\bgruppenleiter',
        r'\blead\b',  # "Lead" alone (e.g., "Marketing Lead")
        r'\bsupervisor\b', r'\bcoordinator\b',
    ],
    'senior': [
        # Senior individual contributor
        r'\bsenior\b', r'\bsr\.\b', r'\bsr\s', r'\bexpert\b', r'\bspecialist\b',
        r'\bprincipal\b', r'\bstaff\b',
    ],
    # 'employee' is the default, no keywords needed
}

# Pre-compile regex patterns for performance
_JOB_LEVEL_PATTERNS = {
    level: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    for level, patterns in JOB_LEVEL_KEYWORDS.items()
}


def calculate_job_level(job_title: Optional[str]) -> str:
    """Calculate job level from job title using word-boundary regex matching.

    Maps job titles to HubSpot-compatible levels:
    - owner: C-Level (CEO, CTO, etc.)
    - director: Director/VP/SVP
    - manager: Manager/Team Lead
    - senior: Senior roles
    - employee: Default for all other roles

    Args:
        job_title: The job title to classify

    Returns:
        Job level string (owner, director, manager, senior, employee)
    """
    if not job_title:
        return 'employee'

    # Check each level in order of seniority (owner > director > manager > senior)
    for level, patterns in _JOB_LEVEL_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(job_title):
                return level

    return 'employee'


def deduplicate_activities(activities: List[Dict]) -> List[Dict]:
    """Remove duplicate activities based on type-specific deduplication rules.

    For emailsOpened: Keep only first open per (leadEmail, emailTemplateId, sequenceStep).
    This removes duplicate tracking pixel loads from email clients.

    Args:
        activities: List of activity dicts from Lemlist API

    Returns:
        Deduplicated list of activities
    """
    seen_keys = set()
    result = []

    for activity in activities:
        activity_type = activity.get('type')

        if activity_type in DEDUPLICATE_ACTIVITY_TYPES:
            # Create unique key for deduplication
            key = (
                activity.get('leadEmail'),
                activity.get('emailTemplateId'),
                activity.get('sequenceStep')
            )

            if key in seen_keys:
                # Skip duplicate
                continue

            seen_keys.add(key)

        result.append(activity)

    return result


# Activity type mapping for German display names
ACTIVITY_TYPE_MAP = {
    'emailsSent': 'Email gesendet',
    'emailsOpened': 'Email geöffnet',
    'emailsClicked': 'Email geklickt',
    'emailsReplied': 'Email beantwortet',
    'emailsBounced': 'Email bounced',
    'emailsFailed': 'Email fehlgeschlagen',
    'emailsUnsubscribed': 'Abgemeldet',
    'linkedinVisitDone': 'LinkedIn Besuch',
    'linkedinSent': 'LinkedIn Nachricht gesendet',
    'linkedinOpened': 'LinkedIn geöffnet',
    'linkedinReplied': 'LinkedIn beantwortet',
    'linkedinInviteDone': 'LinkedIn Einladung gesendet',
    'linkedinInviteAccepted': 'LinkedIn Einladung akzeptiert',
    'outOfOffice': 'Abwesenheit',
    'skipped': 'Übersprungen',
}


# ============================================================================
# Custom Exceptions
# ============================================================================
class UnauthorizedError(Exception):
    pass


class NotFoundError(Exception):
    pass


class RateLimitError(Exception):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


# Lemlist API Client
class LemlistClient:
    def __init__(self, api_key: str):
        self.base_url = "https://api.lemlist.com/api"
        self.session = requests.Session()
        # Basic Auth: empty username, API key as password (format: ":YourApiKey")
        self.session.auth = ('', api_key)
        self.session.headers.update({
            'User-Agent': 'Lemlist-Streamlit-App/1.0'
        })

    def _make_request(self, endpoint: str, params: Optional[Dict] = None, max_retries: int = 3) -> requests.Response:
        """Make HTTP request with rate limit handling and retry logic"""
        url = f"{self.base_url}{endpoint}"

        for attempt in range(max_retries):
            try:
                response = self.session.get(url, params=params, timeout=30)

                # Check rate limit headers
                rate_limit_remaining = response.headers.get('X-RateLimit-Remaining')
                if rate_limit_remaining and int(rate_limit_remaining) < 5:
                    reset_time = response.headers.get('X-RateLimit-Reset')
                    if reset_time:
                        wait_time = max(0, int(reset_time) - time.time())
                        if wait_time > 0:
                            st.warning(f"⏳ Rate limit niedrig. Warte {wait_time}s...")
                            time.sleep(wait_time)

                # Handle HTTP errors
                if response.status_code == 401:
                    raise UnauthorizedError("Ungültiger API Key")
                elif response.status_code == 404:
                    raise NotFoundError("Ressource nicht gefunden")
                elif response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 2 ** attempt))
                    if attempt < max_retries - 1:
                        st.warning(f"⏳ Rate limit erreicht. Retry in {retry_after}s... (Versuch {attempt + 1}/{max_retries})")
                        time.sleep(retry_after)
                        continue
                    else:
                        raise RateLimitError("Rate limit erreicht", retry_after)

                response.raise_for_status()
                return response

            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    st.warning(f"⏳ Timeout. Retry in {wait_time}s... (Versuch {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                else:
                    raise
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    st.warning(f"⏳ Netzwerkfehler. Retry in {wait_time}s... (Versuch {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                else:
                    raise

        raise Exception("Max retries erreicht")

    def _parse_json_response(self, response: requests.Response, endpoint: str) -> List[Dict]:
        """Safely parse JSON response with error handling"""
        try:
            # Check if response is empty
            if not response.content:
                return []

            # Try to parse JSON
            data = response.json()

            # Ensure we return a list
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                # Some endpoints might return a dict with a data field
                return [data]
            else:
                st.error(f"❌ Unerwartete Response-Format von {endpoint}: {type(data)}")
                return []

        except requests.exceptions.JSONDecodeError as e:
            # Debug info
            st.error(f"❌ Fehler beim Parsen der JSON Response von {endpoint}")
            st.error(f"Status Code: {response.status_code}")
            st.error(f"Content-Type: {response.headers.get('Content-Type', 'unknown')}")

            # Show first 500 chars of response for debugging
            content_preview = response.text[:500] if response.text else "(leer)"
            with st.expander("🔍 Response Details (für Debugging)"):
                st.code(content_preview)

            raise Exception(f"Ungültige JSON Response von {endpoint}: {str(e)}")

    def get_all_activities(self, campaign_id: str) -> List[Dict]:
        """Fetch all activities for a campaign with pagination.

        Automatically:
        - Filters out activity types defined in FILTERED_ACTIVITY_TYPES
        - Deduplicates emailsOpened (keeps first per lead/template/step)
        """
        all_activities = []
        offset = 0
        limit = 100

        while True:
            params = {'campaignId': campaign_id, 'limit': limit, 'offset': offset}
            response = self._make_request("/activities", params)

            activities = self._parse_json_response(response, "/activities")
            if not activities or len(activities) == 0:
                break

            # Filter out unwanted activity types before adding to results
            filtered = [a for a in activities if a.get('type') not in FILTERED_ACTIVITY_TYPES]
            all_activities.extend(filtered)

            # If we got fewer results than limit, we've reached the end
            if len(activities) < limit:
                break

            offset += limit

            # Small delay to respect rate limits
            time.sleep(API_PAGINATION_DELAY)

        # Deduplicate activities (e.g., multiple emailsOpened from same email)
        return deduplicate_activities(all_activities)

    def get_lead_details(self, email: str) -> Dict:
        """Fetch detailed information for a specific lead by email

        Args:
            email: Lead email address

        Returns:
            Dict with lead details including hubspotLeadId
        """
        response = self._make_request(f"/leads/{email}")
        leads = self._parse_json_response(response, f"/leads/{email}")

        # API returns a list with a single lead
        if leads and len(leads) > 0:
            return leads[0]
        else:
            return {}

    def get_all_campaigns(self, status: Optional[str] = None) -> List[Dict]:
        """Fetch all campaigns with pagination

        Args:
            status: Filter by status ('running', 'draft', 'archived', 'ended', 'paused', 'errors')

        Returns:
            List of campaign dictionaries with _id, name, status, etc.
        """
        all_campaigns = []
        offset = 0
        limit = 100

        while True:
            params = {'limit': limit, 'offset': offset}
            if status:
                params['status'] = status

            response = self._make_request("/campaigns", params)

            campaigns = self._parse_json_response(response, "/campaigns")
            if not campaigns or len(campaigns) == 0:
                break

            all_campaigns.extend(campaigns)

            # If we got fewer results than limit, we've reached the end
            if len(campaigns) < limit:
                break

            offset += limit

            # Small delay to respect rate limits
            time.sleep(API_PAGINATION_DELAY)

        return all_campaigns


# ============================================================================
# Helper Functions for Data Processing
# ============================================================================

def extract_leads_from_activities(activities: List[Dict]) -> List[Dict]:
    """Extract unique leads from activities response.

    Uses leadId as the unique key (not email) since the same email
    can exist in multiple campaigns with different leadIds.

    Args:
        activities: List of activity dicts from Lemlist API

    Returns:
        List of lead dicts with leadId, email, firstName, lastName, etc.
    """
    leads_dict = {}

    for activity in activities:
        lead_id = activity.get('leadId')
        email = activity.get('leadEmail') or activity.get('email')

        # leadId is required for the new schema
        if lead_id and lead_id not in leads_dict:
            job_title = activity.get('jobTitle')
            leads_dict[lead_id] = {
                'leadId': lead_id,
                'email': email,
                'firstName': activity.get('leadFirstName') or activity.get('firstName'),
                'lastName': activity.get('leadLastName') or activity.get('lastName'),
                'hubspotLeadId': activity.get('hubspotLeadId'),
                'linkedinUrl': (activity.get('linkedinUrl') or
                               activity.get('linkedinUrlSalesNav') or
                               activity.get('linkedinPublicUrl')),
                'companyName': activity.get('leadCompanyName') or activity.get('companyName'),
                'jobTitle': job_title,
                'job_level': calculate_job_level(job_title),
                'phone': activity.get('leadPhone') or activity.get('phone'),
                'location': activity.get('location'),
            }

    return list(leads_dict.values())


def get_activity_display_type(activity: Dict) -> str:
    """Get German display name for activity type.

    Args:
        activity: Activity dict from Lemlist API

    Returns:
        Human-readable activity type in German
    """
    act_type = activity.get('type', '')

    # For conditionChosen, use the label
    if act_type == 'conditionChosen':
        return activity.get('conditionLabel', 'Bedingung erfüllt')

    # Use mapping, fallback to original type
    return ACTIVITY_TYPE_MAP.get(act_type, act_type)


def get_activity_details(activity: Dict) -> str:
    """Extract meaningful details from activity.

    Args:
        activity: Activity dict from Lemlist API

    Returns:
        Details string (subject, URL, message, or condition info)
    """
    act_type = activity.get('type', '')

    if act_type == 'conditionChosen':
        label = activity.get('conditionLabel', 'Unbekannt')
        value = activity.get('conditionValue')
        if value is not None:
            result = 'Ja ✓' if value else 'Nein ✗'
            return f"Bedingung: {label} → Erfüllt: {result}"
        return f"Bedingung: {label}"

    # Try different detail fields
    if 'subject' in activity:
        return str(activity['subject'])
    elif 'url' in activity:
        return str(activity['url'])
    elif 'message' in activity:
        return str(activity['message'])

    return ''


def process_activity_for_db(activity: Dict) -> Optional[Dict]:
    """Process a single activity for database storage.

    Args:
        activity: Activity dict from Lemlist API

    Returns:
        Processed activity dict ready for DB, or None if invalid
    """
    # Skip activities without leadEmail
    if not activity.get('leadEmail'):
        return None

    act_copy = activity.copy()
    act_copy['type_display'] = get_activity_display_type(activity)
    act_copy['details'] = get_activity_details(activity)

    return act_copy


@st.cache_data(ttl=3600)
def load_campaigns_list(api_key: str, status: Optional[str] = None) -> List[Dict]:
    """Load list of campaigns with caching (1 hour TTL)"""
    client = LemlistClient(api_key)
    return client.get_all_campaigns(status=status)


def load_campaign_data_from_db(campaign_id: str) -> pd.DataFrame:
    """Load campaign data from local database"""
    db = LemlistDB()

    # Get activities with lead info from DB
    activities_db = db.get_activities_by_campaign(campaign_id)

    if not activities_db:
        return pd.DataFrame(columns=[
            'Lead Email', 'Lead FirstName', 'Lead LastName',
            'Company', 'Department', 'Job Title', 'Job Level',
            'Activity Type', 'Activity Date', 'Details',
            'HubSpot Link', 'LinkedIn Link'
        ])

    # Convert to DataFrame format expected by UI
    def format_date(date_str):
        try:
            dt = pd.to_datetime(date_str)
            return dt.strftime('%Y-%m-%d %H:%M')
        except Exception:
            return str(date_str)

    def format_hubspot_link(hubspot_id):
        """Format HubSpot ID as clickable link"""
        if hubspot_id:
            account_id = os.getenv("HUBSPOT_ACCOUNT_ID", "")
            if account_id:
                return f"https://app.hubspot.com/contacts/{account_id}/record/0-1/{hubspot_id}"
        return ''

    def format_linkedin_link(linkedin_url):
        """Format LinkedIn URL"""
        if linkedin_url:
            return linkedin_url
        return ''

    records = []
    for activity in activities_db:
        records.append({
            'Lead Email': activity['lead_email'],
            'Lead FirstName': activity['lead_first_name'] or '',
            'Lead LastName': activity['lead_last_name'] or '',
            'Company': activity.get('lead_company_name') or '',
            'Department': activity.get('lead_department') or '',
            'Job Title': activity.get('lead_job_title') or '',
            'Job Level': activity.get('lead_job_level') or '',
            'Activity Type': activity['type_display'] or activity['type'],
            'Activity Date': format_date(activity['created_at']),
            'Details': activity['details'] or '',
            'HubSpot Link': format_hubspot_link(activity.get('lead_hubspot_id')),
            'LinkedIn Link': format_linkedin_link(activity.get('lead_linkedin_url')),
            '_datetime_sort': pd.to_datetime(activity['created_at'], errors='coerce')
        })

    df = pd.DataFrame(records)

    # Sort by datetime (oldest first)
    df = df.sort_values('_datetime_sort', ascending=True)
    df = df.drop(columns=['_datetime_sort'])

    return df


def sync_campaign_data(api_key: str, campaign_id: str, force_full_reload: bool = False,
                       campaign_name: Optional[str] = None, campaign_status: Optional[str] = None) -> pd.DataFrame:
    """Sync campaign data from API to database with incremental updates.

    Args:
        api_key: Lemlist API key
        campaign_id: Campaign ID
        force_full_reload: If True, reload all data from scratch
        campaign_name: Optional campaign name (from dropdown)
        campaign_status: Optional campaign status (from dropdown)

    Returns:
        DataFrame with campaign activities
    """
    db = LemlistDB()
    client = LemlistClient(api_key)

    # Check if this is first load or incremental update
    campaign = db.get_campaign(campaign_id)
    is_first_load = campaign is None or force_full_reload

    if is_first_load:
        st.info("🔄 Erste Synchronisation - lade alle Daten...")

        # Fetch all activities (which already contain lead data!)
        with st.spinner("Lade Activities..."):
            activities = client.get_all_activities(campaign_id)
            st.success(f"✅ {len(activities)} Activities geladen")

        # Extract unique leads from activities response
        with st.spinner("Extrahiere Lead-Daten aus Activities..."):
            leads = extract_leads_from_activities(activities)
            st.success(f"✅ {len(leads)} eindeutige Leads extrahiert")

        # Fetch HubSpot IDs for leads (activities don't contain HubSpot IDs!)
        with st.spinner(f"Lade HubSpot IDs für {min(len(leads), MAX_LEADS_INITIAL_FETCH)} Leads..."):
            leads_to_fetch = leads[:MAX_LEADS_INITIAL_FETCH]
            processed = 0
            success = 0

            for lead in leads_to_fetch:
                email = lead.get('email')
                if not email:
                    continue

                try:
                    lead_details = client.get_lead_details(email)
                    hubspot_id = lead_details.get('hubspotLeadId')

                    if hubspot_id:
                        lead['hubspotLeadId'] = hubspot_id
                        success += 1

                    processed += 1

                    # Small delay to respect rate limits
                    time.sleep(API_RATE_LIMIT_DELAY)

                except Exception as e:
                    logger.warning(f"Failed to fetch HubSpot ID for {email}: {e}")
                    processed += 1
                    continue

            st.success(f"✅ {success} von {processed} Leads haben HubSpot IDs")

        # Save to DB
        with st.spinner("Speichere in Datenbank..."):
            # Use provided campaign name/status or fallback to generic values
            name = campaign_name or f"Campaign {campaign_id}"
            status = campaign_status or "unknown"

            db.upsert_campaign(campaign_id, name, status)
            db.upsert_leads(leads, campaign_id)

            # Process activities for DB storage
            activities_processed = [
                processed for act in activities
                if (processed := process_activity_for_db(act)) is not None
            ]

            db.upsert_activities(activities_processed, campaign_id)
            st.success(f"✅ {len(activities_processed)} Activities gespeichert")

    else:
        st.info("🔄 Incremental Update - lade nur neue Activities...")

        # Get latest activity date from DB
        latest_date = db.get_latest_activity_date(campaign_id)

        # Fetch only new activities
        with st.spinner("Lade neue Activities..."):
            all_activities = client.get_all_activities(campaign_id)

            # Filter for new activities
            new_activities = []
            if latest_date:
                for act in all_activities:
                    if act.get('createdAt', '') > latest_date:
                        new_activities.append(act)
            else:
                new_activities = all_activities

            # Extract leads from new activities
            new_leads = extract_leads_from_activities(new_activities)

            # Save new leads to DB
            if new_leads:

                # Fetch HubSpot IDs for new leads
                with st.spinner(f"Lade HubSpot IDs für {len(new_leads)} neue Lead(s)..."):
                    processed = 0
                    success = 0

                    for lead in new_leads:
                        email = lead.get('email')
                        if not email:
                            continue

                        try:
                            lead_details = client.get_lead_details(email)
                            hubspot_id = lead_details.get('hubspotLeadId')

                            if hubspot_id:
                                lead['hubspotLeadId'] = hubspot_id
                                success += 1

                            processed += 1
                            time.sleep(API_RATE_LIMIT_DELAY)

                        except Exception as e:
                            logger.warning(f"Failed to fetch HubSpot ID for {email}: {e}")
                            processed += 1
                            continue

                    if success > 0:
                        st.success(f"✅ {success} von {processed} neuen Leads haben HubSpot IDs")

                db.upsert_leads(new_leads, campaign_id)
                st.success(f"✅ {len(new_leads)} Lead(s) aktualisiert")

            if new_activities:
                st.success(f"✅ {len(new_activities)} neue Activities gefunden")

                # Process and save new activities
                activities_processed = [
                    processed for act in new_activities
                    if (processed := process_activity_for_db(act)) is not None
                ]

                db.upsert_activities(activities_processed, campaign_id)
                st.success(f"✅ {len(activities_processed)} neue Activities gespeichert")
            else:
                st.info("ℹ️ Keine neuen Activities gefunden")

        # Update campaign timestamp
        if campaign:
            db.upsert_campaign(campaign_id, campaign['name'], campaign['status'])

    # Return data from DB
    return load_campaign_data_from_db(campaign_id)


def fetch_all_lead_details(api_key: str, campaign_id: str,
                           batch_size: Optional[int] = None,
                           pause_seconds: Optional[float] = None,
                           progress_callback=None) -> Dict[str, int]:
    """Fetch HubSpot IDs and LinkedIn URLs for ALL leads that don't have them yet.

    Processes all leads automatically with a pause every batch_size leads.

    Args:
        api_key: Lemlist API key
        campaign_id: Campaign ID
        batch_size: Number of leads to process before pausing (default LEAD_BATCH_SIZE)
        pause_seconds: Seconds to pause after each batch (default LEAD_BATCH_PAUSE)
        progress_callback: Optional callback function(current, total) for progress updates

    Returns:
        Dict with statistics: {'processed': int, 'success': int, 'failed': int}
    """
    if batch_size is None:
        batch_size = LEAD_BATCH_SIZE
    if pause_seconds is None:
        pause_seconds = LEAD_BATCH_PAUSE

    db = LemlistDB()
    client = LemlistClient(api_key)

    # Get ALL leads without HubSpot ID
    leads_to_fetch = db.get_leads_without_hubspot_id(campaign_id, limit=10000)

    if not leads_to_fetch:
        return {'processed': 0, 'success': 0, 'failed': 0}

    total = len(leads_to_fetch)
    processed = 0
    success = 0
    failed = 0

    for i, lead_info in enumerate(leads_to_fetch):
        lead_id = lead_info['lead_id']
        email = lead_info['email']

        try:
            lead_details = client.get_lead_details(email)
            hubspot_id = lead_details.get('hubspotLeadId', None)
            linkedin_url = (lead_details.get('linkedinUrl') or
                          lead_details.get('linkedinPublicUrl') or
                          lead_details.get('linkedin') or
                          lead_details.get('linkedInUrl') or
                          None)
            company_name = (lead_details.get('companyName') or
                          lead_details.get('company') or
                          None)
            department = (lead_details.get('department') or
                         lead_details.get('departments') or
                         None)
            job_title = (lead_details.get('jobTitle') or
                        lead_details.get('position') or
                        None)

            # Calculate job level from job title
            job_level = calculate_job_level(job_title) if job_title else None

            # Update DB using lead_id as identifier
            db.update_lead_details(
                lead_id,
                hubspot_id=hubspot_id,
                linkedin_url=linkedin_url,
                company_name=company_name,
                department=department,
                job_title=job_title,
                job_level=job_level
            )

            if hubspot_id or linkedin_url or company_name or department or job_title:
                success += 1

            # Small delay to respect rate limits
            time.sleep(API_RATE_LIMIT_DELAY)

        except Exception as e:
            logger.warning(f"Failed to fetch details for {email} (lead_id={lead_id}): {e}")
            failed += 1

        processed += 1

        # Update progress
        if progress_callback:
            progress_callback(processed, total)

        # Pause every batch_size leads (but not at the very end)
        if processed % batch_size == 0 and processed < total:
            time.sleep(pause_seconds)

    return {
        'processed': processed,
        'success': success,
        'failed': failed
    }


def sync_to_hubspot(campaign_id: str, hubspot_token: str,
                    batch_size: Optional[int] = None,
                    progress_callback=None) -> Dict[str, int]:
    """Sync lead metrics from database to HubSpot.

    Calculates aggregated engagement metrics for each lead and updates
    the corresponding HubSpot contact properties.

    Args:
        campaign_id: Campaign ID to sync
        hubspot_token: HubSpot Private App access token
        batch_size: Number of contacts per batch (max 100, default HUBSPOT_BATCH_SIZE)
        progress_callback: Optional callback function(current, total) for progress updates

    Returns:
        Dict with statistics: {'processed': int, 'success': int, 'failed': int, 'skipped': int}
    """
    if batch_size is None:
        batch_size = HUBSPOT_BATCH_SIZE

    db = LemlistDB()
    hubspot = HubSpotClient(hubspot_token)

    # Get all leads with HubSpot IDs
    leads = db.get_all_leads_with_hubspot_ids(campaign_id)

    if not leads:
        return {'processed': 0, 'success': 0, 'failed': 0, 'skipped': 0}

    processed = 0
    success = 0
    failed = 0
    skipped = 0

    # Process in batches
    for i in range(0, len(leads), batch_size):
        batch = leads[i:i + batch_size]

        # Calculate metrics for each lead and prepare batch update
        updates = []
        for lead in batch:
            email = lead['email']
            hubspot_id = lead['hubspot_id']

            # Calculate metrics
            metrics = db.calculate_lead_metrics(email, campaign_id)

            if metrics:
                # Filter out None values (HubSpot doesn't accept null)
                filtered_metrics = {k: v for k, v in metrics.items() if v is not None}

                updates.append({
                    'id': hubspot_id,
                    'properties': filtered_metrics
                })
            else:
                skipped += 1

        # Batch update to HubSpot
        if updates:
            try:
                hubspot.batch_update_contacts(updates)
                success += len(updates)
            except HubSpotNotFoundError as e:
                # Some contacts might not exist, try individual updates
                logger.warning(f"Batch update had missing contacts, trying individual: {e}")
                for update in updates:
                    try:
                        hubspot.update_contact_properties(
                            update['id'],
                            update['properties']
                        )
                        success += 1
                    except HubSpotNotFoundError:
                        logger.warning(f"Contact {update['id']} not found in HubSpot")
                        failed += 1
                    except HubSpotError as e:
                        logger.error(f"Failed to update contact {update['id']}: {e}")
                        failed += 1
            except HubSpotError as e:
                logger.error(f"Batch {i // batch_size + 1} failed: {e}")
                failed += len(updates)

        processed += len(batch)

        # Update progress
        if progress_callback:
            progress_callback(processed, len(leads))

        # Rate limit: 4 req/sec for Batch API
        time.sleep(HUBSPOT_BATCH_DELAY)

    return {
        'processed': processed,
        'success': success,
        'failed': failed,
        'skipped': skipped
    }


# Streamlit UI
def main():
    # Load environment variables from .env file
    load_dotenv()

    st.set_page_config(
        page_title="Lemlist Campaign Data Extractor",
        page_icon="📊",
        layout="wide"
    )

    st.title("📊 Lemlist Campaign Data Extractor")
    st.markdown("Extrahiere Leads und Activities aus deiner Lemlist Kampagne")

    # Sidebar for inputs
    with st.sidebar:
        # =====================================================================
        # SECTION 1: API Keys from .env
        # =====================================================================
        # Load API keys from environment variables (no UI input)
        api_key = os.getenv("LEMLIST_API_KEY", "")
        default_campaign_id = os.getenv("CAMPAIGN_ID", "")
        hubspot_token = os.getenv("HUBSPOT_API_TOKEN", "")

        # Show configuration status
        st.subheader("🔑 Konfiguration")
        if api_key:
            st.caption("✅ Lemlist API Key geladen")
        else:
            st.error("❌ LEMLIST_API_KEY in .env fehlt")

        if hubspot_token:
            st.caption("✅ HubSpot Token geladen")
        else:
            st.caption("⚠️ HUBSPOT_API_TOKEN in .env fehlt (optional)")

        # =====================================================================
        # SECTION 2: Campaign Selection
        # =====================================================================
        st.subheader("📋 Campaign")

        campaign_id = None
        selected_campaign_name = None
        selected_campaign_status = None

        if api_key:
            # Status filter in compact form
            status_filter = st.selectbox(
                "Status",
                options=["Alle", "running", "draft", "paused", "ended"],
                index=0,
                label_visibility="collapsed"
            )

            # Load campaigns
            try:
                filter_status = None if status_filter == "Alle" else status_filter
                campaigns = load_campaigns_list(api_key, status=filter_status)

                if not campaigns:
                    st.warning("Keine Campaigns gefunden")
                else:
                    # Create dropdown options
                    campaign_info = {
                        c.get('name', 'Unnamed'): {
                            'id': c.get('_id'),
                            'name': c.get('name', 'Unnamed'),
                            'status': c.get('status', 'unknown')
                        }
                        for c in campaigns
                    }

                    # Find default selection
                    default_index = 0
                    if default_campaign_id:
                        for idx, info in enumerate(campaign_info.values()):
                            if info['id'] == default_campaign_id:
                                default_index = idx
                                break

                    selected_campaign = st.selectbox(
                        "Campaign",
                        options=list(campaign_info.keys()),
                        index=default_index,
                        label_visibility="collapsed"
                    )

                    # Extract campaign info
                    selected_info = campaign_info[selected_campaign]
                    campaign_id = selected_info['id']
                    selected_campaign_name = selected_info['name']
                    selected_campaign_status = selected_info['status']

                    # Show campaign badge
                    status_emoji = {"running": "🟢", "paused": "🟡", "draft": "⚪", "ended": "🔴"}.get(selected_campaign_status, "⚫")
                    st.caption(f"{status_emoji} {selected_campaign_status}")

            except UnauthorizedError:
                st.error("❌ Ungültiger API Key in .env")
            except Exception as e:
                st.error(f"Fehler: {str(e)}")
        else:
            st.info("💡 LEMLIST_API_KEY in .env setzen")

        # =====================================================================
        # SECTION 3: Status (konsolidiert)
        # =====================================================================
        db = LemlistDB()
        campaign_in_db = db.get_campaign(campaign_id) if campaign_id else None
        stats = db.get_campaign_stats(campaign_id) if campaign_id else None

        if campaign_in_db and stats:
            st.subheader("📈 Status")

            col1, col2 = st.columns(2)
            with col1:
                hubspot_info = f"({stats['leads_with_hubspot']} HS)" if stats['leads_with_hubspot'] > 0 else ""
                st.metric("Leads", f"{stats['leads']} {hubspot_info}")
            with col2:
                st.metric("Activities", stats['activities'])

            if stats['last_updated']:
                st.caption(f"Sync: {stats['last_updated']}")

        # =====================================================================
        # SECTION 4: Aktionen
        # =====================================================================
        st.subheader("🔄 Aktionen")

        # Primary action - main sync button
        sync_button = st.button(
            "🔄 Daten aktualisieren",
            type="primary",
            use_container_width=True,
            disabled=not campaign_id,
            help="Lädt neue Activities von Lemlist"
        )

        # Secondary actions in columns
        col1, col2 = st.columns(2)
        with col1:
            force_reload = st.button(
                "🔁 Neu laden",
                use_container_width=True,
                disabled=not campaign_id,
                help="Löscht Cache und lädt alles neu"
            )
        with col2:
            # Lead details button - only show if there are leads without HubSpot
            leads_without_hubspot = (stats['leads'] - stats['leads_with_hubspot']) if stats else 0
            fetch_details_clicked = st.button(
                "⬇️ Details",
                use_container_width=True,
                disabled=not (campaign_in_db and leads_without_hubspot > 0),
                help=f"Lädt HubSpot IDs für {leads_without_hubspot} Leads" if leads_without_hubspot > 0 else "Alle Leads haben bereits Details"
            )

        # Handle lead details fetch
        if fetch_details_clicked and leads_without_hubspot > 0:
            progress_bar = st.progress(0, text="Lade Lead Details...")

            def update_lead_progress(current, total):
                progress = current / total if total > 0 else 0
                progress_bar.progress(progress, text=f"Lade... {current}/{total}")

            result = fetch_all_lead_details(
                api_key,
                campaign_id,
                batch_size=LEAD_BATCH_SIZE,
                pause_seconds=LEAD_BATCH_PAUSE,
                progress_callback=update_lead_progress
            )
            progress_bar.empty()

            st.success(f"✅ {result['success']} von {result['processed']} erfolgreich")
            if result['failed'] > 0:
                st.warning(f"⚠️ {result['failed']} fehlgeschlagen")
            st.rerun()

        # Show hint if leads missing HubSpot data
        if stats and leads_without_hubspot > 0:
            st.caption(f"💡 {leads_without_hubspot} Leads ohne HubSpot IDs")

        # =====================================================================
        # SECTION 5: HubSpot Integration (Expander)
        # =====================================================================
        # hubspot_token already loaded from env at start of sidebar

        with st.expander("🔗 HubSpot Integration", expanded=False):
            # Show sync status
            leads_with_hubspot = stats.get('leads_with_hubspot', 0) if stats else 0

            if not hubspot_token:
                st.warning("⚠️ HUBSPOT_API_TOKEN in .env setzen")
            elif leads_with_hubspot > 0:
                st.caption(f"✅ {leads_with_hubspot} Leads bereit für Sync")
            elif campaign_in_db:
                st.caption("⚠️ Erst Lead Details laden")

            # Sync button with clear disabled reason
            sync_to_hubspot_disabled = not (campaign_id and hubspot_token and leads_with_hubspot > 0)

            if st.button("⬆️ Nach HubSpot syncen", use_container_width=True, disabled=sync_to_hubspot_disabled):
                try:
                    hubspot_client = HubSpotClient(hubspot_token)
                    if not hubspot_client.verify_token():
                        st.error("Ungültiger Token")
                    else:
                        progress_bar = st.progress(0, text="Synchronisiere...")

                        def update_progress(current, total):
                            progress = current / total if total > 0 else 0
                            progress_bar.progress(progress, text=f"Sync... {current}/{total}")

                        result = sync_to_hubspot(
                            campaign_id,
                            hubspot_token,
                            batch_size=HUBSPOT_BATCH_SIZE,
                            progress_callback=update_progress
                        )
                        progress_bar.empty()

                        if result['success'] > 0:
                            st.success(f"✅ {result['success']}/{result['processed']} gesynct")
                        if result['failed'] > 0:
                            st.warning(f"⚠️ {result['failed']} fehlgeschlagen")

                except HubSpotUnauthorizedError:
                    st.error("Ungültiger Token")
                except HubSpotRateLimitError as e:
                    st.error(f"Rate Limit - warte {e.retry_after}s")
                except HubSpotError as e:
                    st.error(f"Fehler: {str(e)}")

            # Notes Analysis Sub-Section
            st.markdown("---")
            st.markdown("**Notes Analyse**")

            notes_analysis_disabled = not (campaign_id and hubspot_token and leads_with_hubspot > 0)

            if st.button("📥 Notes laden", use_container_width=True, disabled=notes_analysis_disabled):
                try:
                    hubspot_client = HubSpotClient(hubspot_token)
                    analyzer = NotesAnalyzer(hubspot_client, db)

                    progress_bar = st.progress(0, text="Lade Notes...")

                    def update_notes_progress(current, total):
                        progress = current / total if total > 0 else 0
                        progress_bar.progress(progress, text=f"Notes... {current}/{total}")

                    notes = analyzer.fetch_all_notes(campaign_id, progress_callback=update_notes_progress)
                    progress_bar.empty()

                    st.session_state['hubspot_notes'] = notes
                    st.session_state['notes_campaign_id'] = campaign_id

                    lemlist_notes = [n for n in notes if n.get('parsed')]
                    st.success(f"✅ {len(lemlist_notes)} Lemlist Notes")

                except HubSpotError as e:
                    st.error(f"Fehler: {str(e)}")

            # Show analysis if notes loaded
            if 'hubspot_notes' in st.session_state and st.session_state.get('notes_campaign_id') == campaign_id:
                notes = st.session_state['hubspot_notes']

                if hubspot_token:
                    hubspot_client = HubSpotClient(hubspot_token)
                    analyzer = NotesAnalyzer(hubspot_client, db)
                    duplicates = analyzer.find_duplicates(notes)
                    dup_stats = analyzer.get_duplicate_stats(duplicates)

                    if duplicates:
                        st.warning(f"⚠️ {dup_stats['total_to_delete']} Duplikate")

                        if st.button("🗑️ Duplikate löschen", use_container_width=True):
                            try:
                                progress_bar = st.progress(0)
                                result = analyzer.delete_duplicates(
                                    duplicates, keep_newest=True,
                                    progress_callback=lambda c, t: progress_bar.progress(c/t if t > 0 else 0)
                                )
                                progress_bar.empty()
                                st.success(f"✅ {result['deleted']} gelöscht")
                                del st.session_state['hubspot_notes']
                            except Exception as e:
                                st.error(str(e))
                    else:
                        st.success("✅ Keine Duplikate")

                    # DB Comparison compact
                    comparison = analyzer.compare_with_db(notes, campaign_id)
                    comp_stats = comparison['stats']
                    st.caption(f"DB Match: {comp_stats['matched_count']} | Diff: {comp_stats['notes_only_count'] + comp_stats['db_only_count']}")

        # =====================================================================
        # SECTION 6: Wartung (Expander)
        # =====================================================================
        with st.expander("⚙️ Wartung", expanded=False):
            st.caption("Daten werden lokal in SQLite gespeichert")

            if st.button("🗑️ Datenbank leeren", use_container_width=True, type="secondary"):
                if campaign_id:
                    db.clear_campaign_data(campaign_id)
                    st.success("Datenbank geleert!")
                if 'df' in st.session_state:
                    st.session_state.df = None
                if 'current_campaign_id' in st.session_state:
                    st.session_state.current_campaign_id = None
                st.rerun()

    # Main content
    # Initialize session state for data persistence
    if 'df' not in st.session_state:
        st.session_state.df = None
    if 'current_campaign_id' not in st.session_state:
        st.session_state.current_campaign_id = None

    # Sync data when button is clicked
    if sync_button or force_reload:
        if not api_key:
            st.error("❌ LEMLIST_API_KEY fehlt in .env")
            return

        if not campaign_id:
            st.error("❌ Bitte Campaign auswählen")
            return

        try:
            # Sync data from API to DB
            df = sync_campaign_data(
                api_key,
                campaign_id,
                force_full_reload=force_reload,
                campaign_name=selected_campaign_name,
                campaign_status=selected_campaign_status
            )

            # Store in session state
            st.session_state.df = df
            st.session_state.current_campaign_id = campaign_id

            if len(df) == 0:
                st.warning("⚠️ Keine Activities gefunden für diese Kampagne")
                st.session_state.df = None
                return

        except UnauthorizedError:
            st.error("❌ Ungültiger LEMLIST_API_KEY in .env")
            st.session_state.df = None
            return
        except NotFoundError:
            st.error("❌ Campaign ID nicht gefunden. Bitte überprüfe die Campaign ID.")
            st.session_state.df = None
            return
        except RateLimitError as e:
            st.error(f"❌ Rate Limit erreicht. Bitte warte {e.retry_after}s und versuche es erneut.")
            st.session_state.df = None
            return
        except Exception as e:
            st.error(f"❌ Fehler beim Laden der Daten: {str(e)}")
            with st.expander("🔍 Details (für Debugging)"):
                st.exception(e)
            st.session_state.df = None
            return

    # Load data from DB if campaign selected (even without button click)
    elif campaign_id and (st.session_state.df is None or st.session_state.current_campaign_id != campaign_id):
        try:
            df = load_campaign_data_from_db(campaign_id)
            if len(df) > 0:
                st.session_state.df = df
                st.session_state.current_campaign_id = campaign_id
        except Exception as e:
            # Expected when no data in DB yet, log at debug level
            logger.debug(f"No cached data for campaign {campaign_id}: {e}")

    # Display data if available in session state
    if st.session_state.df is not None:
        df = st.session_state.df

        # Display metrics
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Gesamt Activities", len(df))
        with col2:
            st.metric("Unique Leads", df['Lead Email'].nunique())
        with col3:
            st.metric("Activity Types", df['Activity Type'].nunique())

        st.divider()

        # Filter Section
        st.subheader("🔍 Filter")

        col1, col2 = st.columns(2)

        with col1:
            # Lead Filter
            lead_options = ["Alle Leads"] + sorted(df['Lead Email'].dropna().unique().tolist())
            selected_lead = st.selectbox(
                "👤 Lead auswählen",
                options=lead_options,
                index=0,
                help="Zeige nur Aktivitäten für einen bestimmten Lead"
            )

        with col2:
            # Activity Type Filter
            activity_types = st.multiselect(
                "📊 Activity Types",
                options=sorted(df['Activity Type'].unique()),
                default=None,
                help="Filtere nach bestimmten Activity Types"
            )

        # Apply filters
        filtered_df = df.copy()

        # Lead filter
        if selected_lead != "Alle Leads":
            filtered_df = filtered_df[filtered_df['Lead Email'] == selected_lead]

            # On-demand fetch HubSpot/LinkedIn data for selected lead if not present
            if api_key and campaign_id:
                # Check if lead has HubSpot/LinkedIn data
                lead_has_data = False
                if len(filtered_df) > 0:
                    first_row = filtered_df.iloc[0]
                    lead_has_data = bool(first_row.get('HubSpot Link')) or bool(first_row.get('LinkedIn Link'))

                # Fetch data if not present
                if not lead_has_data:
                    try:
                        with st.spinner(f"Lade Details für {selected_lead}..."):
                            db = LemlistDB()
                            client = LemlistClient(api_key)

                            # First, get lead_id from DB
                            lead_record = db.get_lead_by_email(selected_lead, campaign_id)
                            if not lead_record:
                                st.warning("⚠️ Lead nicht in Datenbank gefunden")
                            else:
                                lead_id = lead_record['lead_id']

                                lead_details = client.get_lead_details(selected_lead)
                                hubspot_id = lead_details.get('hubspotLeadId', None)
                                linkedin_url = (lead_details.get('linkedinUrl') or
                                              lead_details.get('linkedinPublicUrl') or
                                              lead_details.get('linkedin') or
                                              lead_details.get('linkedInUrl') or
                                              None)

                                # Update DB using lead_id
                                if hubspot_id or linkedin_url:
                                    db.update_lead_details(lead_id, hubspot_id=hubspot_id, linkedin_url=linkedin_url)
                                    # Reload data from DB to get updated links
                                    df = load_campaign_data_from_db(campaign_id)
                                    st.session_state.df = df
                                    filtered_df = df[df['Lead Email'] == selected_lead]
                                    st.success("✅ Lead Details geladen")
                    except Exception as e:
                        st.warning(f"⚠️ Konnte Lead Details nicht laden: {str(e)}")

        # Activity type filter
        if activity_types:
            filtered_df = filtered_df[filtered_df['Activity Type'].isin(activity_types)]

        # Data Table
        st.divider()
        st.subheader("📋 Alle Aktivitäten")

        # Format the dataframe for display with clickable links
        display_df = filtered_df.copy()

        # Rename columns for cleaner display - keep raw URLs for LinkColumn
        if 'HubSpot Link' in display_df.columns:
            display_df['HubSpot'] = display_df['HubSpot Link']
            display_df = display_df.drop(columns=['HubSpot Link'])

        if 'LinkedIn Link' in display_df.columns:
            display_df['LinkedIn'] = display_df['LinkedIn Link']
            display_df = display_df.drop(columns=['LinkedIn Link'])

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                'HubSpot': st.column_config.LinkColumn(
                    'HubSpot',
                    help="Klick zum Öffnen des HubSpot Kontakts",
                    display_text="🔗 HubSpot"
                ),
                'LinkedIn': st.column_config.LinkColumn(
                    'LinkedIn',
                    help="Klick zum Öffnen des LinkedIn Profils",
                    display_text="🔗 LinkedIn"
                )
            }
        )

        # Download section
        st.subheader("💾 Export")

        col1, col2 = st.columns([2, 1])

        with col1:
            csv = filtered_df.to_csv(index=False).encode('utf-8')
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"lemlist_campaign_{st.session_state.current_campaign_id}_{timestamp}.csv"

            st.download_button(
                label="📥 Download CSV",
                data=csv,
                file_name=filename,
                mime="text/csv",
                use_container_width=True
            )

        with col2:
            st.metric("Zeilen im Export", len(filtered_df))
    else:
        # Prominent empty state with clear instructions
        st.markdown("""
        ### 👋 Willkommen!

        **So startest du:**

        1. **.env Datei** konfigurieren (siehe .env.example)
        2. **Campaign** auswählen (Sidebar links)
        3. **🔄 Daten aktualisieren** klicken

        Die Daten werden lokal gespeichert und sind beim nächsten Start sofort verfügbar.
        """)

        # Show quick tips
        col1, col2, col3 = st.columns(3)
        with col1:
            st.info("**📊 .env Konfiguration**\n\nKopiere .env.example → .env")
        with col2:
            st.info("**🔑 API Keys**\n\nLemlist + HubSpot in .env")
        with col3:
            st.info("**💾 Lokal gespeichert**\n\nDaten bleiben erhalten")


if __name__ == "__main__":
    main()
