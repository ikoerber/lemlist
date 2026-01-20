# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Streamlit application for extracting and analyzing data from the Lemlist API. The app fetches campaign leads and their associated activities, presenting them in a flat table format suitable for analysis and export.

## Tech Stack

- **Python**: Core language
- **Streamlit**: Web UI framework
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

2. **Get Campaign Leads** (with pagination)
   ```
   GET /campaigns/{campaignId}/leads?limit=100&offset=0
   ```
   - Supports pagination via `limit` and `offset` parameters
   - Must iterate through all pages until no more leads are returned

3. **Get All Activities for Campaign** (RECOMMENDED)
   ```
   GET /activities?campaignId={campaignId}&limit=100&offset=0
   ```
   - **CRITICAL**: Use campaignId filter to get ALL activities at once (50-100x faster)
   - Returns all activities for the entire campaign
   - Supports pagination with `limit` and `offset`
   - Activity types include: emailOpened, emailClicked, emailReplied, linkedinVisit, etc.
   - Merge with leads client-side on email field

## Data Structure

### Output Format
The app produces a flat table where each row represents a single activity:

| Column | Description |
|--------|-------------|
| Lead Email | Lead's email address |
| Lead FirstName | Lead's first name |
| Lead LastName | Lead's last name |
| Activity Type | Type of activity (emailOpened, linkedinVisit, etc.) |
| Activity Date | Formatted as YYYY-MM-DD HH:MM |
| Details | Activity payload/subject details |

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

### LemlistClient Class
- Session-based HTTP client with Basic Auth
- Centralized `_make_request()` method with:
  - Rate limit monitoring via headers
  - Automatic retry with exponential backoff
  - Timeout handling (30s default)
  - Up to 3 retry attempts
- `get_all_campaigns()`: Fetches all campaigns with optional status filter
- `get_all_leads()`: Fetches all leads with pagination
- `get_all_activities()`: Fetches all activities with campaignId filter

### Data Processing
- `merge_leads_activities()`: Merges leads and activities DataFrames on email
- Handles missing fields gracefully
- Extracts meaningful details from activity payloads (subject, url, message)
- Formats dates to YYYY-MM-DD HH:MM
- Sorts by date (newest first)

### Caching
- Uses `@st.cache_data(ttl=3600)` for 1-hour caching
- Prevents redundant API calls for repeated analyses
- Manual cache clearing via UI button
- Significantly improves UX for daily/frequent usage

## Development Notes

- **Environment Variables**: Supports `.env` file for storing API key and campaign ID
  - Uses `python-dotenv` to load environment variables
  - Values from `.env` are used as defaults in UI inputs
  - Falls back to manual UI input if `.env` not present
- API credentials entered via UI (password input) or loaded from .env, never hardcoded
- All data processing happens client-side after API fetch
- Typical performance: 5-10 seconds for 500 leads (vs. minutes with old N+1 approach)
- Cache hit: < 1 second for repeated loads
