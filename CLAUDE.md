# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Streamlit application for extracting and analyzing data from the Lemlist API. The app fetches campaign leads and their associated activities, presenting them in a flat table format suitable for analysis and export.

## Tech Stack

- **Python**: Core language
- **Streamlit**: Web UI framework
- **SQLite**: Local database for caching campaign data
- **Pandas**: Data manipulation and export
- **Requests**: HTTP client for Lemlist API calls

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
   - Activity types include: emailsSent, emailsOpened, linkedinVisitDone, conditionChosen, etc.
   - **Contains lead data**: leadEmail, leadFirstName, leadLastName, linkedinUrl
   - **Does NOT contain**: hubspotLeadId (must fetch separately)

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
- `id` (TEXT PRIMARY KEY): Campaign ID
- `name` (TEXT): Campaign name
- `status` (TEXT): Campaign status
- `last_sync` (DATETIME): Last sync timestamp

**leads table**:
- `email` (TEXT PRIMARY KEY): Lead email
- `campaign_id` (TEXT): Associated campaign
- `first_name`, `last_name` (TEXT): Lead names
- `hubspot_id` (TEXT): HubSpot contact ID (nullable)
- `linkedin_url` (TEXT): LinkedIn profile URL (nullable)
- `last_updated` (DATETIME): Last update timestamp

**activities table**:
- `id` (TEXT PRIMARY KEY): Activity ID
- `lead_email` (TEXT): Foreign key to leads
- `campaign_id` (TEXT): Associated campaign
- `type` (TEXT): Raw activity type
- `type_display` (TEXT): Translated/formatted type
- `created_at` (DATETIME): Activity timestamp
- `details` (TEXT): Extracted details (subject, URL, etc.)
- `raw_data` (TEXT): Full JSON payload

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

### LemlistClient Class (`app.py`)
Session-based HTTP client with Basic Auth:
- `_make_request()`: Centralized request handler with rate limit monitoring, retry logic, timeout handling
- `get_all_campaigns(status)`: Fetches campaigns with optional status filter (pagination)
- `get_all_activities(campaign_id)`: Fetches ALL activities with campaignId filter (pagination)
- `get_lead_details(email)`: Fetches detailed lead info including hubspotLeadId (rate limited)

### LemlistDB Class (`db.py`)
SQLite database layer for local caching:
- `get_connection()`: Context manager for DB connections
- `upsert_campaign()`: Insert/update campaign record
- `upsert_leads()`: Bulk insert/update leads (extracts hubspot_id, linkedin_url)
- `upsert_activities()`: Bulk insert/update activities
- `get_activities_by_campaign()`: Returns activities with LEFT JOIN on leads (includes lead names, hubspot_id, linkedin_url)
- `get_campaign_stats()`: Returns metrics (lead count, activity count, hubspot coverage)
- `get_latest_activity_date()`: Returns timestamp of newest activity (for incremental updates)
- `get_leads_without_hubspot_id()`: Returns emails that need HubSpot ID enrichment
- `update_lead_details()`: Updates hubspot_id and linkedin_url for a single lead
- `clear_campaign_data()`: Deletes all data for a campaign

### Core Functions (`app.py`)

**`sync_campaign_data(api_key, campaign_id, force_full_reload)`**
Main sync logic with two modes:

1. **First Load / Full Reload**:
   - Fetch ALL activities from API (pagination)
   - Extract leads from activities (leadEmail, leadFirstName, leadLastName, linkedinUrl)
   - Fetch HubSpot IDs for first 50 leads via `/leads/{email}` (0.15s delay between calls)
   - Save leads and activities to DB
   - Return data from DB

2. **Incremental Update**:
   - Fetch ALL activities from API
   - Filter for activities newer than `get_latest_activity_date()`
   - Extract new leads from new activities
   - Fetch HubSpot IDs for all new leads
   - Save new leads and activities to DB
   - Return data from DB

**`load_campaign_data_from_db(campaign_id)`**
- Calls `db.get_activities_by_campaign()` (LEFT JOIN)
- Formats HubSpot links: `https://app.hubspot.com/contacts/19645216/record/0-1/{hubspot_id}`
- Returns Pandas DataFrame sorted by date (oldest first)

**`fetch_lead_details_batch(api_key, campaign_id, batch_size=10)`**
- Gets leads without HubSpot ID from DB
- Fetches HubSpot IDs via `/leads/{email}` for batch_size leads
- Updates DB with fetched HubSpot IDs
- Returns statistics (processed, success, failed, remaining)
- Used by "⬇️ Lead Details laden" button

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

3. User clicks **"⬇️ Lead Details laden"**:
   - `fetch_lead_details_batch(api_key, campaign_id, batch_size=50)`
   - Fetches HubSpot IDs for 50 more leads
   - Updates DB in place
   - UI refreshes to show new HubSpot links

4. **Display**:
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
  - First sync: 15-20 seconds for 1500+ activities (includes HubSpot fetching for 50 leads)
  - Incremental update: < 5 seconds (only new activities)
  - Loading from DB: < 1 second (instant display)
  - HubSpot batch fetch: ~8 seconds for 50 leads (0.15s delay per lead)
- **Lead extraction**: Leads are extracted from activities response, NOT from `/campaigns/{id}/leads` (which returns empty)
- **HubSpot IDs**: Only available via `/leads/{email}` endpoint, not in activities response
- **Incremental updates**: Tracks latest activity timestamp to fetch only new activities
- **UI state**: Uses Streamlit session_state to persist DataFrame between interactions
