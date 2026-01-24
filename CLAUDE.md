# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Streamlit application for extracting and analyzing data from the Lemlist API. The app fetches campaign leads and their associated activities, presenting them in a flat table format suitable for analysis and export.

## Tech Stack

- **Python**: Core language
- **Streamlit**: Web UI framework
- **SQLite**: Local database for caching campaign data
- **Pandas**: Data manipulation and export
- **Requests**: HTTP client for API calls

## Running the Application

```bash
streamlit run app.py
```

## Lemlist API Integration

### Base URL
```
https://api.lemlist.com/api
```

### Authentication
All API requests require ONLY an API key (no User ID or Email needed):
- **Method**: HTTP Basic Authentication
- **Format**: Empty username, API key as password (`:YourApiKey`)
- **In Python requests**: `session.auth = ('', api_key)`
- **In curl**: `--user ":YourApiKey"`
- **Header**: `Authorization: Basic {base64(:YourApiKey)}`

### Key Endpoints

1. **Get All Campaigns** (for dropdown selection)
   ```
   GET /campaigns?limit=100&offset=0&status=running
   ```
   - Returns list of all campaigns in the account
   - Supports pagination via `limit` and `offset` parameters
   - Optional `status` filter: running, draft, paused, ended, archived
   - Returns campaign `_id`, `name`, `status`, `createdAt`, etc.
   - Used to populate campaign dropdown in UI

2. **Get All Activities for Campaign** (PRIMARY DATA SOURCE)
   ```
   GET /activities?campaignId={campaignId}&limit=100&offset=0
   ```
   - **CRITICAL**: Use campaignId filter to get ALL activities at once (50-100x faster)
   - Returns all activities for the entire campaign
   - Supports pagination with `limit` and `offset`
   - Activity types include: emailsSent, emailsOpened, linkedinVisitDone, etc.
   - **Contains lead data**: `leadId`, leadEmail, leadFirstName, leadLastName, linkedinUrl
   - **`leadId`**: Unique Lemlist ID for the lead (e.g., `lea_xxx`) - used as PRIMARY KEY
   - **Does NOT contain**: hubspotLeadId (must fetch separately)
   - **Filtered types**: `hasEmailAddress`, `conditionChosen` are automatically filtered out
   - **Deduplicated**: `emailsOpened` are deduplicated by (leadEmail, emailTemplateId, sequenceStep)

3. **Get Lead Details** (for HubSpot ID enrichment)
   ```
   GET /leads/{email}
   ```
   - Returns detailed lead information for a specific email
   - **Contains**: hubspotLeadId, linkedinUrl, companyName, jobTitle, etc.
   - Used to enrich leads with HubSpot IDs after extracting from activities
   - Rate limit: 20 requests per 2 seconds (use 0.15s delay between calls)

**IMPORTANT**: The `/campaigns/{campaignId}/leads` endpoint returns empty responses and should NOT be used. Extract leads from the activities response instead.

## Data Structure

### Database Schema (SQLite)

**campaigns table**:
- `campaign_id` (TEXT PRIMARY KEY): Lemlist Campaign ID
- `name` (TEXT): Campaign name
- `status` (TEXT): Campaign status
- `last_updated` (DATETIME): Last sync timestamp

**leads table**:
- `lead_id` (TEXT PRIMARY KEY): Lemlist Lead ID (e.g., `lea_xxx`)
- `email` (TEXT NOT NULL): Lead email address
- `campaign_id` (TEXT NOT NULL): Associated campaign (FK)
- `first_name`, `last_name` (TEXT): Lead names
- `hubspot_id` (TEXT): HubSpot contact ID (nullable)
- `linkedin_url` (TEXT): LinkedIn profile URL (nullable)
- `company_name` (TEXT): Company name from Lemlist
- `job_title` (TEXT): Job title from Lemlist
- `job_level` (TEXT): Calculated job level (owner, director, manager, senior, employee)
- `department` (TEXT): Calculated department (Executive, Finance, IT, Marketing, Sales, etc.)
- `last_updated` (DATETIME): Last update timestamp

**Note**: Using `lead_id` as PRIMARY KEY allows the same email to exist in multiple campaigns (each with a unique `lead_id`). This is how Lemlist models leads internally.

**activities table**:
- `id` (TEXT PRIMARY KEY): Activity ID from Lemlist
- `lead_id` (TEXT NOT NULL): Foreign key to leads.lead_id
- `lead_email` (TEXT NOT NULL): Lead email (denormalized for convenience)
- `campaign_id` (TEXT NOT NULL): Associated campaign (FK)
- `type` (TEXT): Raw activity type
- `type_display` (TEXT): Translated/formatted type
- `created_at` (DATETIME): Activity timestamp
- `details` (TEXT): Extracted details (subject, URL, etc.)
- `raw_json` (TEXT): Full JSON payload

### Output Format
The app produces a flat table where each row represents a single activity:

| Column | Description |
|--------|-------------|
| Lead Email | Lead's email address |
| Lead FirstName | Lead's first name (from leads table JOIN) |
| Lead LastName | Lead's last name (from leads table JOIN) |
| Activity Type | Type of activity (emailOpened, linkedinVisit, etc.) |
| Activity Date | Formatted as YYYY-MM-DD HH:MM |
| Details | Activity payload/subject details |
| HubSpot Link | Clickable link to HubSpot contact (from leads.hubspot_id) |
| LinkedIn Link | Clickable link to LinkedIn profile (from leads.linkedin_url) |

## Important Implementation Details

### Pagination Logic
The Lemlist API uses offset-based pagination. Implement a loop that:
1. Starts with `offset=0` and `limit=100`
2. Fetches leads
3. Increments offset by limit
4. Continues until API returns empty results or fewer items than limit

### Rate Limits (CRITICAL)
The Lemlist API has strict rate limits:
- **20 requests per 2 seconds** per API key
- Monitor `X-RateLimit-Remaining` header to track quota
- Use `Retry-After` header when hitting 429 errors
- Implement exponential backoff for retries (1s, 2s, 4s)
- Add 0.1s delay between pagination requests to stay within limits

### Progress Indication
Use `st.spinner()` for each stage:
- "Lade Leads..." while fetching leads
- "Lade Activities..." while fetching activities
- "Verarbeite Daten..." while merging data
- Show success messages with counts after each stage

### Error Handling
The app implements comprehensive error handling:
- **401 Unauthorized**: Invalid API key → Show user-friendly error
- **404 Not Found**: Campaign ID doesn't exist → Show user-friendly error
- **429 Too Many Requests**: Rate limit exceeded → Auto-retry with backoff
- **Network errors/timeouts**: Auto-retry up to 3 times with exponential backoff
- Custom exception classes: `UnauthorizedError`, `NotFoundError`, `RateLimitError`

## User Interface Requirements

### Input Fields
1. API Key input (type: password) - `st.text_input(..., type="password")`
2. Campaign selection (two modes):
   - **Dropdown mode (default)**: `st.selectbox()` with campaigns loaded from API
   - Status filter to show only active/draft/paused campaigns
   - Manual mode: `st.text_input()` for direct ID entry
3. "Daten laden" (Load Data) button - `st.button(...)` (disabled until campaign selected)

### Output Display
1. **Filters** (two-column layout):
   - Lead filter: Dropdown to select specific lead or "Alle Leads"
   - Activity type filter: Multi-select for activity types
   - When lead is selected: Show lead-specific metrics (total activities, most common activity, latest activity)
2. **Preview**:
   - If lead selected: Show full activity timeline for that lead with name/email header
   - If "Alle Leads": Show first 10 rows of all data
3. **CSV Download**: Provide download button using `st.download_button()`
   - MIME type: `text/csv`
   - File name: Include campaign ID and timestamp
   - Exports filtered data (respects both lead and activity type filters)

## Architecture

### Activity Filtering & Deduplication (`app.py`)

**Filtered Activity Types** (not useful for analysis):
```python
FILTERED_ACTIVITY_TYPES = {'hasEmailAddress', 'conditionChosen'}
```

**Deduplicated Activity Types** (keep first occurrence per unique key):
```python
DEDUPLICATE_ACTIVITY_TYPES = {'emailsOpened'}
# Key for emailsOpened: (leadEmail, emailTemplateId, sequenceStep)
```

**`deduplicate_activities(activities)`**:
- Removes duplicate `emailsOpened` based on (leadEmail, emailTemplateId, sequenceStep)
- Keeps first occurrence (removes duplicate tracking pixel loads)
- Called automatically in `get_all_activities()`

### API Clients Package (`api_clients/`)

Reusable API client implementations:

**BaseAPIClient** (`base_client.py`):
- HTTP request handling with retries
- Rate limit management
- Error handling with custom exceptions

**HubSpotClient** (`hubspot.py`):
- `verify_token()`: Check if token is valid
- `update_contact_properties()`: Update single contact
- `batch_update_contacts()`: Batch update up to 100 contacts
- `get_notes_for_contact()`: Fetch notes for a contact
- `batch_delete_notes()`: Delete multiple notes

**LemlistClient** (`lemlist.py`):
- `get_all_campaigns()`: Fetch campaigns with pagination
- `get_all_activities()`: Fetch activities with campaignId filter
- `get_lead_details()`: Fetch detailed lead info

**StreamlitWrappers** (`streamlit_wrappers.py`):
- `StreamlitHubSpotClient`: HubSpot client with st.warning() notifications
- `StreamlitLemlistClient`: Lemlist client with st.warning() notifications

### LemlistDB Class (`db.py`)
SQLite database layer for local caching:
- `get_connection()`: Context manager for DB connections
- `upsert_campaign()`: Insert/update campaign record
- `upsert_leads()`: Bulk insert/update leads using `lead_id` as key
- `upsert_activities()`: Bulk insert/update activities (requires `leadId` for FK)
- `get_activities_by_campaign()`: Returns activities with LEFT JOIN on leads
- `get_campaign_stats()`: Returns metrics (lead count, activity count, hubspot coverage)
- `get_latest_activity_date()`: Returns timestamp of newest activity
- `get_leads_without_hubspot_id()`: Returns leads needing enrichment
- `get_leads_with_job_level()`: Returns leads with hubspot_id and job_level
- `get_leads_with_department()`: Returns leads with hubspot_id and department
- `update_lead_details()`: Updates hubspot_id, linkedin_url, job_level, department
- `clear_campaign_data()`: Deletes all data for a campaign

### Job Level Classification (`app.py`)

Automatic classification of job titles to HubSpot-compatible seniority levels:

```python
JOB_LEVEL_KEYWORDS = {
    'owner': ['ceo', 'cto', 'cfo', 'chief', 'geschäftsführer', 'vorstand', ...],
    'director': ['director', 'vp', 'vice president', 'head of', 'leiter', ...],
    'manager': ['manager', 'teamleiter', 'lead', 'supervisor', ...],
    'senior': ['senior', 'sr', 'principal', 'expert', ...],
    'employee': []  # Default fallback
}

JOB_LEVEL_TO_HUBSPOT_SENIORITY = {
    'owner': 'owner',
    'director': 'director',
    'manager': 'manager',
    'senior': 'senior',
    'employee': 'employee'
}
```

**`calculate_job_level(job_title)`**: Returns job level based on keyword matching

### Department Classification (`app.py`)

Automatic classification of job titles to departments:

```python
DEPARTMENT_KEYWORDS = {
    'Executive': ['ceo', 'cto', 'cfo', 'chief', 'geschäftsführer', ...],
    'Finance': ['finance', 'finanz', 'accounting', 'controller', ...],
    'Procurement': ['procurement', 'einkauf', 'purchasing', 'buyer', ...],
    'Engineering': ['engineer', 'ingenieur', 'develop', 'software', ...],
    'IT': ['it', 'information tech', 'network', 'cloud', 'digital', ...],
    'Operations': ['operation', 'project manag', 'quality', 'logist', 'assistenz', ...],
    'Production': ['production', 'produktion', 'manufactur', 'maintenance', ...],
    'Marketing': ['marketing', 'brand', 'communication', 'content', 'seo', ...],
    'Sales': ['sales', 'vertrieb', 'account', 'business develop', 'e-commerce', ...],
    'HR': ['human resource', 'hr', 'personal', 'recruit', 'talent', ...],
    'Legal': ['legal', 'recht', 'compliance', 'contract', 'datenschutz', ...]
}

DEPARTMENT_TO_HUBSPOT_ROLE = {
    'Finance': 'FINANCE',
    'Procurement': 'Procurement',
    'Engineering': 'Engineering',
    'IT': 'IT',
    'Executive': 'Executive',
    'Operations': 'Operations',
    'Production': 'Production',
    'Marketing': 'Marketing',
    'Sales': 'Sales',
    'HR': 'HR',
    'Legal': 'Legal'
}
```

**`calculate_department(job_title)`**: Returns department based on keyword matching

### Core Functions (`app.py`)

**`sync_campaign_data(api_key, campaign_id, force_full_reload)`**
Main sync logic with two modes:

1. **First Load / Full Reload**:
   - Fetch ALL activities from API (pagination)
   - Extract leads from activities using `leadId` as unique key
   - Calculate job_level and department from job_title
   - Fetch HubSpot IDs for first 50 leads via `/leads/{email}`
   - Save leads and activities to DB
   - Return data from DB

2. **Incremental Update**:
   - Fetch ALL activities from API
   - Filter for activities newer than `get_latest_activity_date()`
   - Extract new leads from new activities
   - Fetch HubSpot IDs for all new leads
   - Save new leads and activities to DB
   - Return data from DB

**`fetch_all_lead_details(api_key, campaign_id)`**
- Gets ALL leads without HubSpot ID from DB
- Fetches HubSpot IDs via `/leads/{email}` for ALL leads
- Calculates job_level and department from job_title
- Updates DB with fetched data
- Returns statistics (processed, success, failed)

**`sync_job_levels_to_hubspot(hubspot_token, campaign_id)`**
- Gets leads from local DB with hubspot_id and job_level
- Checks current hs_seniority in HubSpot (batch read)
- Only updates contacts where hs_seniority is NOT set
- Does NOT overwrite existing HubSpot data
- Returns statistics (total, updated, skipped, failed)

**`sync_departments_to_hubspot(hubspot_token, campaign_id)`**
- Gets leads from local DB with hubspot_id and department
- Checks current hs_role in HubSpot (batch read)
- Only updates contacts where hs_role is NOT set
- Does NOT overwrite existing HubSpot data
- Returns statistics (total, updated, skipped, failed)

### Data Flow

1. User clicks **"🔄 Aktivitäten aktualisieren"**:
   - `sync_campaign_data(api_key, campaign_id, force_full_reload=False)`
   - Incremental update: Only new activities saved
   - Fast (< 5 seconds)

2. User clicks **"🔁 Vollständig neu laden"**:
   - `db.clear_campaign_data(campaign_id)`
   - `sync_campaign_data(api_key, campaign_id, force_full_reload=True)`
   - Full reload: All activities saved, first 50 leads get HubSpot IDs
   - Slower (15-20 seconds for 1500+ activities)

3. User clicks **"⬇️ Alle Lead Details laden"**:
   - `fetch_all_lead_details(api_key, campaign_id)`
   - Fetches HubSpot IDs for ALL leads
   - Calculates job_level and department
   - Updates DB in place

4. User clicks **"🎯 Job Levels syncen"**:
   - `sync_job_levels_to_hubspot(hubspot_token, campaign_id)`
   - Syncs job_level to HubSpot hs_seniority
   - Only for Lemlist leads, only if hs_seniority is empty

5. User clicks **"🏢 Departments syncen"**:
   - `sync_departments_to_hubspot(hubspot_token, campaign_id)`
   - Syncs department to HubSpot hs_role
   - Only for Lemlist leads, only if hs_role is empty

6. **Display**:
   - `load_campaign_data_from_db(campaign_id)` → DataFrame
   - Apply filters (lead, activity type)
   - Show in Streamlit with LinkColumn for HubSpot and LinkedIn
   - Export to CSV

## Development Notes

- **Environment Variables**: Supports `.env` file for storing API key and campaign ID
  - Uses `python-dotenv` to load environment variables
  - Values from `.env` are used as defaults in UI inputs
  - Falls back to manual UI input if `.env` not present
- API credentials entered via UI (password input) or loaded from .env, never hardcoded
- **Database**: SQLite file `lemlist_data.db` created in project root (excluded from git)
- **Performance**:
  - First sync: 15-20 seconds for 1500+ activities
  - Incremental update: < 5 seconds (only new activities)
  - Loading from DB: < 1 second (instant display)
- **Lead extraction**: Leads are extracted from activities response, NOT from `/campaigns/{id}/leads`
- **HubSpot IDs**: Only available via `/leads/{email}` endpoint
- **Incremental updates**: Tracks latest activity timestamp to fetch only new activities
- **UI state**: Uses Streamlit session_state to persist DataFrame between interactions
- **Data persistence**: Each campaign's data is stored separately, switching campaigns preserves data

## HubSpot Sync Integration

### Overview
The app can sync data from the local SQLite database to HubSpot contact properties. This enables filtering, reporting, and segmentation based on Lemlist activity data.

### HubSpot API Setup

1. **Create Private App in HubSpot**:
   - Go to Settings → Integrations → Private Apps
   - Create new app with scope: `crm.objects.contacts.write`
   - Copy the access token

### Synced Properties

**Engagement Metrics** (via "Nach HubSpot syncen"):
| Property Name | Type | Description |
|---------------|------|-------------|
| `lemlist_total_activities` | Number | Total count of all activities |
| `lemlist_engagement_score` | Number | Calculated engagement score (0-100) |
| `lemlist_lead_status` | String | Lead status (new, cold, low/medium/high_engagement, bounced) |
| `lemlist_emails_sent` | Number | Count of emails sent |
| `lemlist_emails_opened` | Number | Count of emails opened |
| `lemlist_linkedin_invites_accepted` | Number | LinkedIn invites accepted |
| `lemlist_last_sync_date` | DateTime | Timestamp of last sync |

**Job Level** (via "Job Levels syncen"):
| Property | HubSpot Field | Values |
|----------|---------------|--------|
| job_level | `hs_seniority` | owner, director, manager, senior, employee |

**Department** (via "Departments syncen"):
| Property | HubSpot Field | Values |
|----------|---------------|--------|
| department | `hs_role` | Executive, Finance, Procurement, Engineering, IT, Operations, Production, Marketing, Sales, HR, Legal |

### Important: No Overwriting

All sync functions check if the HubSpot field already has a value:
- **hs_seniority**: Only written if empty in HubSpot
- **hs_role**: Only written if empty in HubSpot
- Existing HubSpot data is NEVER overwritten
- Skipped contacts are counted and reported

### Rate Limits
- Standard: 100 requests/10 seconds
- Batch API: 4 requests/second (use 0.25s delay between batches)
- Max 100 contacts per batch

## HubSpot Notes Analysis

### Overview
The app can analyze Lemlist notes stored in HubSpot contacts and identify/remove duplicates.

### Note Format (from Lemlist native integration)
```
{Activity Type} from campaign {Campaign Name} - (step {N})
Text: {Optional Message Content}
```

### LemlistNoteParser Class (`hubspot_notes_analyzer.py`)

Parses Lemlist notes from HubSpot to extract structured activity data.

### NotesAnalyzer Class (`hubspot_notes_analyzer.py`)

- `fetch_all_notes()`: Fetches all notes for leads in a campaign
- `find_duplicates()`: Groups notes by (contact_id, activity_type, campaign, step)
- `delete_duplicates()`: Deletes duplicate notes, keeping newest per group

### UI Integration

The sidebar includes a "Notes Analyse" section with:
1. **"📥 Notes laden"** button: Loads notes for analysis
2. **Duplicate Analysis**: Shows duplicate groups
3. **"🗑️ Alle Duplikate löschen"** button: Removes duplicates
