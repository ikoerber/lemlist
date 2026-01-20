# Implementation Plan: HubSpot Sync mit aggregierten Metriken

## Ziel
Lemlist Activity-Daten aus unserer SQLite DB als strukturierte Custom Properties in HubSpot syncen, um:
- Unstrukturierte Notizen zu ersetzen
- Filterbare, reportfähige Metriken zu erstellen
- Lead-Segmentierung zu ermöglichen

## User Requirements
- ✅ Aggregierte Metriken (keine einzelnen Timeline Events)
- ✅ Manueller Sync via Button (später automatisierbar)
- ✅ Campaign-übergreifende Metriken (alle Activities eines Leads)
- ✅ Leads ohne HubSpot ID überspringen
- ✅ Replied-Metriken hinzufügen (Email + LinkedIn)
- ✅ HubSpot Property Group "Lemlist"
- ✅ Engagement Score normalisiert (0-100)
- ✅ Bounced-Status zurücksetzen bei späterem Engagement

## HubSpot Custom Properties

### Property Group: "Lemlist"
Alle Properties werden in einer eigenen Gruppe organisiert für bessere Übersicht.

### 1. Core Engagement Metrics
| Property Name | Type | Description | Berechnung |
|---------------|------|-------------|------------|
| `lemlist_total_activities` | Number | Gesamtzahl aller Activities | COUNT(*) über alle Campaigns |
| `lemlist_last_activity_date` | Date | Datum der letzten Activity | MAX(created_at) |
| `lemlist_first_activity_date` | Date | Datum der ersten Activity | MIN(created_at) |
| `lemlist_days_in_campaign` | Number | Tage seit erster Activity | TODAY - first_activity_date |
| `lemlist_campaigns_count` | Number | Anzahl verschiedener Campaigns | COUNT(DISTINCT campaign_id) |

### 2. Email Metrics
| Property Name | Type | Description | Berechnung |
|---------------|------|-------------|------------|
| `lemlist_emails_sent` | Number | Anzahl gesendete Emails | COUNT(type='emailsSent') |
| `lemlist_emails_opened` | Number | Anzahl geöffnete Emails | COUNT(type='emailsOpened') |
| `lemlist_emails_replied` | Number | Anzahl Email-Antworten | COUNT(type='emailsReplied') |
| `lemlist_emails_bounced` | Number | Anzahl bounced Emails | COUNT(type='emailsBounced') |
| `lemlist_email_open_rate` | Number | Email Open Rate (%) | (opened/sent) * 100 |
| `lemlist_last_email_opened_date` | Date | Letzter Email-Open | MAX(created_at WHERE type='emailsOpened') |

### 3. LinkedIn Metrics
| Property Name | Type | Description | Berechnung |
|---------------|------|-------------|------------|
| `lemlist_linkedin_visits` | Number | LinkedIn Profile Besuche | COUNT(type='linkedinVisitDone') |
| `lemlist_linkedin_invites_sent` | Number | LinkedIn Einladungen gesendet | COUNT(type='linkedinInviteDone') |
| `lemlist_linkedin_invites_accepted` | Number | LinkedIn Einladungen akzeptiert | COUNT(type='linkedinInviteAccepted') |
| `lemlist_linkedin_messages_sent` | Number | LinkedIn Nachrichten gesendet | COUNT(type='linkedinSent') |
| `lemlist_linkedin_messages_opened` | Number | LinkedIn Nachrichten geöffnet | COUNT(type='linkedinOpened') |
| `lemlist_linkedin_replied` | Number | LinkedIn Antworten | COUNT(type='linkedinReplied') |

### 4. Engagement Score & Status
| Property Name | Type | Description | Berechnung |
|---------------|------|-------------|------------|
| `lemlist_engagement_score` | Number | Engagement Score (0-100) | Normalized weighted sum |
| `lemlist_lead_status` | Enum | Lead Status | Regel-basiert (siehe unten) |
| `lemlist_last_sync_date` | DateTime | Letzte Sync mit Lemlist | Timestamp des letzten Syncs |

**Engagement Score Berechnung (Raw Score):**
```python
ENGAGEMENT_WEIGHTS = {
    'emailsSent': 1,
    'emailsOpened': 3,
    'emailsReplied': 10,      # Höchste Gewichtung - direkte Antwort
    'emailsBounced': -5,
    'linkedinVisitDone': 2,
    'linkedinInviteDone': 1,
    'linkedinInviteAccepted': 5,
    'linkedinSent': 2,
    'linkedinOpened': 4,
    'linkedinReplied': 10,    # Höchste Gewichtung - direkte Antwort
    'aircallDone': 3,
    'outOfOffice': 0,
    'conditionChosen': 0,
    'paused': -2,
}
```

**Score Normalisierung (0-100):**
```python
def normalize_score(raw_score: int) -> int:
    """Normalize raw score to 0-100 range"""
    # Clamp between 0 and 100
    # Score of 30+ raw points = 100 (excellent engagement)
    MAX_RAW_SCORE = 30
    normalized = int((raw_score / MAX_RAW_SCORE) * 100)
    return max(0, min(100, normalized))
```

**Lead Status Logik (mit Bounce-Reset):**
```python
def determine_lead_status(metrics: Dict) -> str:
    """
    Determine lead status based on metrics.

    WICHTIG: Bounced-Status wird zurückgesetzt wenn später
    positives Engagement kommt (z.B. via LinkedIn).
    """
    score = metrics['engagement_score']
    total = metrics['total_activities']
    bounced = metrics['emails_bounced']
    replied = metrics['emails_replied'] + metrics['linkedin_replied']

    # Replied hat höchste Priorität
    if replied > 0:
        return 'replied'

    # Bounced nur wenn KEIN anderes positives Engagement
    # (z.B. LinkedIn Invite Accepted würde bounced überschreiben)
    positive_engagement = (
        metrics['emails_opened'] +
        metrics['linkedin_invites_accepted'] +
        metrics['linkedin_messages_opened']
    )
    if bounced > 0 and positive_engagement == 0:
        return 'bounced'

    # Score-basierte Status
    if score >= 70:
        return 'high_engagement'
    elif score >= 40:
        return 'medium_engagement'
    elif score >= 15:
        return 'low_engagement'
    elif total <= 2:
        return 'new'
    else:
        return 'cold'
```

**Status Enum Values:**
- `replied` - Hat geantwortet (Email oder LinkedIn)
- `high_engagement` - Score >= 70
- `medium_engagement` - Score 40-69
- `low_engagement` - Score 15-39
- `new` - <= 2 Activities
- `cold` - Score < 15 und > 2 Activities
- `bounced` - Email bounced OHNE anderes positives Engagement

## Architektur

### 1. Neue Komponente: HubSpotClient (`hubspot_client.py`)

```python
import requests
import time
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class HubSpotError(Exception):
    """Base exception for HubSpot API errors"""
    pass


class HubSpotRateLimitError(HubSpotError):
    """Rate limit exceeded"""
    pass


class HubSpotClient:
    """Client für HubSpot API Interaktionen"""

    def __init__(self, api_token: str):
        self.base_url = "https://api.hubapi.com"
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {api_token}',
            'Content-Type': 'application/json'
        })

    def _make_request(self, method: str, endpoint: str,
                      max_retries: int = 3, **kwargs) -> requests.Response:
        """Zentrale Request-Methode mit Error Handling und Rate Limiting"""
        url = f"{self.base_url}{endpoint}"

        for attempt in range(max_retries):
            try:
                response = self.session.request(method, url, **kwargs)

                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 10))
                    logger.warning(f"Rate limit reached. Retry in {retry_after}s...")
                    time.sleep(retry_after)
                    continue

                if response.status_code == 401:
                    raise HubSpotError("Invalid API token")

                response.raise_for_status()
                return response

            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"Request failed, retry in {wait_time}s: {e}")
                    time.sleep(wait_time)
                else:
                    raise HubSpotError(f"Request failed after {max_retries} attempts: {e}")

        raise HubSpotError("Max retries exceeded")

    def batch_update_contacts(self, updates: List[Dict]) -> Dict:
        """Batch update für bis zu 100 Contacts

        POST /crm/v3/objects/contacts/batch/update

        Args:
            updates: List of dicts with 'id' (hubspot_id) and 'properties'

        Returns:
            Response data with results
        """
        if not updates:
            return {'results': []}

        if len(updates) > 100:
            raise ValueError("Batch size cannot exceed 100")

        payload = {
            "inputs": [
                {
                    "id": str(update['id']),
                    "properties": update['properties']
                }
                for update in updates
            ]
        }

        response = self._make_request(
            'POST',
            '/crm/v3/objects/contacts/batch/update',
            json=payload
        )

        return response.json()

    def create_property_group(self, name: str, label: str) -> Dict:
        """Create a property group for organizing custom properties

        POST /crm/v3/properties/contacts/groups
        """
        payload = {
            "name": name,
            "label": label,
            "displayOrder": 1
        }

        try:
            response = self._make_request(
                'POST',
                '/crm/v3/properties/contacts/groups',
                json=payload
            )
            return response.json()
        except HubSpotError as e:
            # Group might already exist
            if "already exists" in str(e).lower():
                logger.info(f"Property group '{name}' already exists")
                return {"name": name}
            raise

    def create_property(self, name: str, label: str,
                       property_type: str, field_type: str,
                       group_name: str = "lemlist",
                       options: Optional[List[Dict]] = None) -> Dict:
        """Create a custom contact property

        POST /crm/v3/properties/contacts

        Args:
            name: Internal property name (e.g., 'lemlist_engagement_score')
            label: Display label (e.g., 'Lemlist Engagement Score')
            property_type: 'string', 'number', 'date', 'datetime', 'enumeration'
            field_type: 'text', 'number', 'date', 'select', etc.
            group_name: Property group name
            options: For enumeration type, list of {label, value} dicts
        """
        payload = {
            "name": name,
            "label": label,
            "type": property_type,
            "fieldType": field_type,
            "groupName": group_name
        }

        if options:
            payload["options"] = options

        try:
            response = self._make_request(
                'POST',
                '/crm/v3/properties/contacts',
                json=payload
            )
            return response.json()
        except HubSpotError as e:
            if "already exists" in str(e).lower():
                logger.info(f"Property '{name}' already exists")
                return {"name": name}
            raise
```

### 2. Neue DB-Methode: Metriken berechnen (`db.py`)

```python
# Constants für Engagement Score
ENGAGEMENT_WEIGHTS = {
    'emailsSent': 1,
    'emailsOpened': 3,
    'emailsReplied': 10,
    'emailsBounced': -5,
    'linkedinVisitDone': 2,
    'linkedinInviteDone': 1,
    'linkedinInviteAccepted': 5,
    'linkedinSent': 2,
    'linkedinOpened': 4,
    'linkedinReplied': 10,
    'aircallDone': 3,
    'outOfOffice': 0,
    'conditionChosen': 0,
    'paused': -2,
}

MAX_RAW_SCORE = 30  # Raw score für 100% normalisiert


def calculate_lead_metrics(self, email: str) -> Optional[Dict[str, Any]]:
    """Berechnet aggregierte Metriken für einen Lead über ALLE Campaigns.

    Args:
        email: Lead email address

    Returns:
        Dict mit allen HubSpot Property Values, oder None wenn keine Activities
    """
    activities = self.get_activities_by_lead(email)

    if not activities:
        return None

    # Parse dates and get campaign info
    dates = []
    campaign_ids = set()
    for a in activities:
        try:
            dates.append(datetime.fromisoformat(a['created_at'].replace('Z', '+00:00')))
        except (ValueError, AttributeError):
            dates.append(datetime.now())
        campaign_ids.add(a['campaign_id'])

    first_date = min(dates)
    last_date = max(dates)
    days_in_campaign = (datetime.now() - first_date).days

    # Count activities by type
    type_counts = {}
    for a in activities:
        activity_type = a['type']
        type_counts[activity_type] = type_counts.get(activity_type, 0) + 1

    # Extract specific counts
    emails_sent = type_counts.get('emailsSent', 0)
    emails_opened = type_counts.get('emailsOpened', 0)
    emails_replied = type_counts.get('emailsReplied', 0)
    emails_bounced = type_counts.get('emailsBounced', 0)

    linkedin_visits = type_counts.get('linkedinVisitDone', 0)
    linkedin_invites_sent = type_counts.get('linkedinInviteDone', 0)
    linkedin_invites_accepted = type_counts.get('linkedinInviteAccepted', 0)
    linkedin_messages_sent = type_counts.get('linkedinSent', 0)
    linkedin_messages_opened = type_counts.get('linkedinOpened', 0)
    linkedin_replied = type_counts.get('linkedinReplied', 0)

    # Calculate raw engagement score
    raw_score = sum(
        ENGAGEMENT_WEIGHTS.get(activity_type, 0) * count
        for activity_type, count in type_counts.items()
    )

    # Normalize score to 0-100
    normalized_score = max(0, min(100, int((raw_score / MAX_RAW_SCORE) * 100)))

    # Determine lead status
    total_activities = len(activities)
    total_replied = emails_replied + linkedin_replied
    positive_engagement = emails_opened + linkedin_invites_accepted + linkedin_messages_opened

    if total_replied > 0:
        status = 'replied'
    elif emails_bounced > 0 and positive_engagement == 0:
        status = 'bounced'
    elif normalized_score >= 70:
        status = 'high_engagement'
    elif normalized_score >= 40:
        status = 'medium_engagement'
    elif normalized_score >= 15:
        status = 'low_engagement'
    elif total_activities <= 2:
        status = 'new'
    else:
        status = 'cold'

    # Format dates for HubSpot (Unix timestamp in milliseconds)
    def to_hubspot_date(dt: datetime) -> int:
        return int(dt.timestamp() * 1000)

    # Get last opened date
    last_email_opened = None
    for a in activities:
        if a['type'] == 'emailsOpened':
            try:
                dt = datetime.fromisoformat(a['created_at'].replace('Z', '+00:00'))
                if last_email_opened is None or dt > last_email_opened:
                    last_email_opened = dt
            except (ValueError, AttributeError):
                pass

    return {
        # Core metrics
        'lemlist_total_activities': total_activities,
        'lemlist_first_activity_date': to_hubspot_date(first_date),
        'lemlist_last_activity_date': to_hubspot_date(last_date),
        'lemlist_days_in_campaign': days_in_campaign,
        'lemlist_campaigns_count': len(campaign_ids),

        # Email metrics
        'lemlist_emails_sent': emails_sent,
        'lemlist_emails_opened': emails_opened,
        'lemlist_emails_replied': emails_replied,
        'lemlist_emails_bounced': emails_bounced,
        'lemlist_email_open_rate': round((emails_opened / emails_sent) * 100, 1) if emails_sent > 0 else 0,
        'lemlist_last_email_opened_date': to_hubspot_date(last_email_opened) if last_email_opened else None,

        # LinkedIn metrics
        'lemlist_linkedin_visits': linkedin_visits,
        'lemlist_linkedin_invites_sent': linkedin_invites_sent,
        'lemlist_linkedin_invites_accepted': linkedin_invites_accepted,
        'lemlist_linkedin_messages_sent': linkedin_messages_sent,
        'lemlist_linkedin_messages_opened': linkedin_messages_opened,
        'lemlist_linkedin_replied': linkedin_replied,

        # Engagement
        'lemlist_engagement_score': normalized_score,
        'lemlist_lead_status': status,
        'lemlist_last_sync_date': to_hubspot_date(datetime.now()),
    }


def get_all_leads_with_hubspot_ids(self, campaign_id: Optional[str] = None) -> List[Dict]:
    """Holt alle Leads mit HubSpot IDs für Batch-Sync.

    Args:
        campaign_id: Optional - wenn gesetzt, nur Leads dieser Campaign
                     wenn None, alle Leads mit HubSpot ID

    Returns:
        List of dicts with email and hubspot_id
    """
    with self.get_connection() as conn:
        cursor = conn.cursor()

        if campaign_id:
            cursor.execute("""
                SELECT DISTINCT email, hubspot_id, first_name, last_name
                FROM leads
                WHERE campaign_id = ?
                AND hubspot_id IS NOT NULL
                AND hubspot_id != ''
            """, (campaign_id,))
        else:
            cursor.execute("""
                SELECT DISTINCT email, hubspot_id, first_name, last_name
                FROM leads
                WHERE hubspot_id IS NOT NULL
                AND hubspot_id != ''
            """)

        return [dict(row) for row in cursor.fetchall()]
```

### 3. Neue Core Function: Sync zu HubSpot (`app.py`)

```python
def sync_to_hubspot(campaign_id: str, hubspot_token: str,
                    batch_size: int = 50,
                    progress_callback=None) -> Dict[str, int]:
    """Synct Lead-Metriken zu HubSpot.

    Berechnet Metriken über ALLE Campaigns für jeden Lead,
    synct aber nur Leads die in der gewählten Campaign sind.

    Args:
        campaign_id: Campaign ID to sync leads from
        hubspot_token: HubSpot API token
        batch_size: Number of contacts per batch (max 100)
        progress_callback: Optional callback for progress updates

    Returns:
        Statistics dict with processed, success, failed counts
    """
    from hubspot_client import HubSpotClient, HubSpotError

    db = LemlistDB()
    hubspot = HubSpotClient(hubspot_token)

    # Get leads with HubSpot IDs from this campaign
    leads = db.get_all_leads_with_hubspot_ids(campaign_id)

    if not leads:
        return {'processed': 0, 'success': 0, 'failed': 0, 'skipped': 0}

    processed = 0
    success = 0
    failed = 0
    skipped = 0

    total_batches = (len(leads) + batch_size - 1) // batch_size

    # Process in batches
    for batch_num, i in enumerate(range(0, len(leads), batch_size)):
        batch = leads[i:i + batch_size]

        # Calculate metrics for each lead (across ALL campaigns)
        updates = []
        for lead in batch:
            metrics = db.calculate_lead_metrics(lead['email'])

            if metrics:
                # Filter out None values (e.g., last_email_opened_date if no opens)
                filtered_metrics = {
                    k: v for k, v in metrics.items()
                    if v is not None
                }
                updates.append({
                    'id': lead['hubspot_id'],
                    'properties': filtered_metrics
                })
            else:
                skipped += 1

        if not updates:
            continue

        # Batch update to HubSpot
        try:
            hubspot.batch_update_contacts(updates)
            success += len(updates)
            logger.info(f"Batch {batch_num + 1}/{total_batches}: {len(updates)} contacts updated")
        except HubSpotError as e:
            logger.error(f"Batch {batch_num + 1} failed: {e}")
            failed += len(updates)

        processed += len(updates)

        # Progress callback
        if progress_callback:
            progress_callback(processed, len(leads))

        # Rate limit: 4 req/sec für Batch API
        time.sleep(0.25)

    return {
        'processed': processed,
        'success': success,
        'failed': failed,
        'skipped': skipped
    }
```

### 4. UI Integration (`app.py`)

**Sidebar: HubSpot Sync Section**
```python
st.divider()
st.subheader("🔄 HubSpot Sync")

# Load HubSpot token from .env if available
default_hubspot_token = os.getenv('HUBSPOT_API_TOKEN', '')

hubspot_token = st.text_input(
    "HubSpot API Token",
    value=default_hubspot_token,
    type="password",
    help="Private App Token aus HubSpot Settings → Integrations → Private Apps"
)

# Show sync status
if campaign_id:
    db = LemlistDB()
    leads_with_hubspot = db.get_all_leads_with_hubspot_ids(campaign_id)
    total_leads = len(db.get_leads_by_campaign(campaign_id))

    st.caption(
        f"📊 {len(leads_with_hubspot)} von {total_leads} Leads "
        f"haben HubSpot ID"
    )

if st.button("⬆️ Nach HubSpot syncen",
            disabled=not (campaign_id and hubspot_token),
            help="Synct Engagement-Metriken zu HubSpot Contact Properties"):

    progress_bar = st.progress(0)
    status_text = st.empty()

    def update_progress(current, total):
        progress_bar.progress(current / total)
        status_text.text(f"Verarbeite {current}/{total} Leads...")

    with st.spinner("Syncronisiere Metriken zu HubSpot..."):
        result = sync_to_hubspot(
            campaign_id,
            hubspot_token,
            batch_size=50,
            progress_callback=update_progress
        )

    progress_bar.empty()
    status_text.empty()

    if result['success'] > 0:
        st.success(
            f"✅ {result['success']} von {result['processed']} "
            f"Leads erfolgreich gesynct"
        )

    if result['failed'] > 0:
        st.warning(f"⚠️ {result['failed']} Leads fehlgeschlagen")

    if result['skipped'] > 0:
        st.info(f"ℹ️ {result['skipped']} Leads übersprungen (keine Activities)")
```

## Implementation Steps

### Phase 1: Setup & Preparation
1. **HubSpot Private App erstellen**
   - HubSpot Settings → Integrations → Private Apps
   - Permissions benötigt:
     - `crm.objects.contacts.write`
     - `crm.schemas.contacts.write` (für Property Creation)
   - Token kopieren

2. **Property Group und Properties anlegen**

   **Option A: Manuell in HubSpot UI**
   - Settings → Properties → Contact Properties
   - Neue Gruppe "Lemlist" erstellen
   - Alle Properties manuell anlegen

   **Option B: Via API (empfohlen)**
   - Führe Setup-Funktion einmalig aus:
   ```python
   def setup_hubspot_properties(hubspot_token: str):
       """Erstellt Property Group und alle Properties in HubSpot"""
       client = HubSpotClient(hubspot_token)

       # Create property group
       client.create_property_group("lemlist", "Lemlist")

       # Create properties
       properties = [
           ("lemlist_total_activities", "Total Activities", "number", "number"),
           ("lemlist_engagement_score", "Engagement Score", "number", "number"),
           ("lemlist_emails_sent", "Emails Sent", "number", "number"),
           ("lemlist_emails_opened", "Emails Opened", "number", "number"),
           ("lemlist_emails_replied", "Emails Replied", "number", "number"),
           ("lemlist_linkedin_replied", "LinkedIn Replied", "number", "number"),
           # ... weitere Properties
           ("lemlist_lead_status", "Lead Status", "enumeration", "select"),
       ]

       for name, label, prop_type, field_type in properties:
           if name == "lemlist_lead_status":
               client.create_property(
                   name, label, prop_type, field_type,
                   options=[
                       {"label": "Replied", "value": "replied"},
                       {"label": "High Engagement", "value": "high_engagement"},
                       {"label": "Medium Engagement", "value": "medium_engagement"},
                       {"label": "Low Engagement", "value": "low_engagement"},
                       {"label": "New", "value": "new"},
                       {"label": "Cold", "value": "cold"},
                       {"label": "Bounced", "value": "bounced"},
                   ]
               )
           else:
               client.create_property(name, label, prop_type, field_type)
   ```

### Phase 2: Core Implementation
3. **Neue Datei: `hubspot_client.py`**
   - HubSpotClient Klasse implementieren
   - Error Handling & Rate Limiting
   - Batch Update Methode
   - Property Creation Methoden

4. **DB erweitern: `db.py`**
   - `ENGAGEMENT_WEIGHTS` Konstante
   - `calculate_lead_metrics()` Methode
   - `get_all_leads_with_hubspot_ids()` Methode

5. **App erweitern: `app.py`**
   - `sync_to_hubspot()` Funktion
   - UI Section für HubSpot Token & Sync Button
   - Progress-Anzeige mit Callback

6. **Config erweitern**
   - `.env.example`: Add `HUBSPOT_API_TOKEN=`
   - Laden in app.py

### Phase 3: Testing
7. **Lokale Tests**
   - Test `calculate_lead_metrics()` mit Sample-Daten
   - Verify Score-Normalisierung (0-100)
   - Verify Status-Logik (besonders Bounce-Reset)

8. **Integration Test**
   - Mit 5-10 Test-Contacts in HubSpot
   - Verify Properties wurden korrekt gesetzt
   - Check Date-Format (Unix ms)

9. **Full Sync Test**
   - Mit allen ~250 Leads
   - Monitor für Rate Limit Errors
   - Verify in HubSpot UI

### Phase 4: Documentation
10. **README.md Update**
    - HubSpot Sync Feature dokumentieren
    - Setup Instructions
    - Property Liste

11. **CLAUDE.md Update**
    - HubSpot API Integration
    - Custom Properties Schema
    - Sync Logic Flow

## Error Handling

### Rate Limits
**HubSpot Standard Limits:**
- 100 requests/10 seconds (Standard)
- 150 requests/10 seconds (Marketing Hub Pro+)
- 4 requests/second für Batch APIs

### Contact Not Found
- HubSpot ID existiert nicht mehr → Log warning, continue
- Statistik am Ende zeigt failed count

### Invalid Properties
- Property existiert nicht → Sync schlägt fehl
- → Setup-Script vorher ausführen
- → Validate Property names

### Date Format
- HubSpot erwartet Unix Timestamp in Millisekunden
- `to_hubspot_date()` Helper konvertiert datetime

## Security & Best Practices

1. **Token Storage**
   - HubSpot Token via UI (password input) oder `.env`
   - **NIEMALS** in Git committen
   - `.env` ist in `.gitignore`

2. **Batch Size**
   - Default: 50 Contacts pro Batch
   - Max: 100 (HubSpot Limit)
   - 0.25s Delay zwischen Batches

3. **Idempotenz**
   - Properties überschreiben (nicht addieren)
   - Mehrfache Syncs sind safe

4. **Logging**
   - Info: Batch success
   - Warning: Rate limits, retries
   - Error: Failed batches

## Critical Files

| File | Action | Description |
|------|--------|-------------|
| `hubspot_client.py` | NEW | HubSpot API Client |
| `db.py` | MODIFY | Add metrics calculation |
| `app.py` | MODIFY | Add sync function and UI |
| `.env.example` | MODIFY | Add HUBSPOT_API_TOKEN |
| `README.md` | MODIFY | Document HubSpot integration |
| `CLAUDE.md` | MODIFY | Document architecture |

## Risks & Mitigation

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Rate Limit exceeded | Sync fails | Batch processing + 0.25s delays |
| Invalid HubSpot IDs | Some contacts fail | Skip & log, show statistics |
| Property not found | Sync fails | Setup-Script vorher ausführen |
| Large dataset (1000+) | Long sync time | Progress bar + batch status |
| Token expired | Auth fails | Clear error message |

## Success Criteria

- [ ] Property Group "Lemlist" in HubSpot angelegt
- [ ] Alle 20 Custom Properties erstellt
- [ ] Metriken werden korrekt berechnet (campaign-übergreifend)
- [ ] Score ist normalisiert (0-100)
- [ ] Bounced-Status wird bei Engagement zurückgesetzt
- [ ] Replied-Status hat höchste Priorität
- [ ] Batch-Sync funktioniert ohne Rate Limit Errors
- [ ] UI Button mit Progress-Anzeige
- [ ] Error Handling zeigt failed/skipped counts
- [ ] Dokumentation aktualisiert
