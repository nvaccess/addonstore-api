# NVDA Add-on Store API

## Application Overview

The Add-on Store API serves as the central distribution point for NVDA add-ons.
It provides versioned, language-specific access to add-on metadata.
It also provides a web-front end view to view add-ons at <https://addonstore.nvaccess.org/>

## API Endpoints

The website front end serves as the base URL, e.g. <https://addonstore.nvaccess.org/> (the legacy URL <https://addons.nvda-project.org/> also maps here).

### Add-on Retrieval

All GET endpoints require:

* language: The target language (e.g., 'en', 'es')
* NVDA Add-on API version (e.g., '2023.1.0')

Available endpoints:

* `GET /<language>/all/<version>.json`
  * Returns complete list of latest add-on releases (both stable and beta)
  * Example: `/en/all/2023.1.0.json`

* `GET /<language>/stable/<version>.json`
  * Returns only stable add-on releases
  * Example: `/en/stable/2023.1.0.json`

* `GET /<language>/beta/<version>.json`
  * Returns only beta add-on releases
  * Example: `/en/beta/2023.1.0.json`

* `GET /<language>/dev/<version>.json`
  * Returns only dev (alpha) add-on releases
  * Example: `/en/dev/2023.1.0.json`

### System Endpoints

* `POST /update`
  * GitHub webhook endpoint for updating add-on repository
  * Requires valid branch reference in payload
  * Requires auth token
  * Protected by distributed locking
  * Example payload: `{"ref": "refs/heads/main"}`

* `GET /cacheHash.json`
  * Returns current git hash of the data store
  * Used for health checks and cache validation

## Development

### Pre-requisites

* Python 3.13
* Pre-commit:
  * Markdown lint requires node and npm.
  * `npm install -g markdownlint-cli2`
* A virtual environment
* Install requirements.txt inside the virtual environment
* The `addon-datastore` repository checked out locally to a views branch.

### Testing Local Changes

Test the API endpoints after the repository clone is complete:

```bash
# Check if the repository is ready
curl http://localhost:5000/cacheHash.json

# Test add-on retrieval
curl http://localhost:5000/en/stable/latest.json
```

Check the web frontend by visiting <http://localhost:5000>

### Running the application

From the virtual environment

```sh
FLASK_APP=app PYTHONPATH=./src TEMP=/tmp/ \
dataViewsFolder=../../addon-datastore branchRef=views \
COPYRIGHT_YEARS=2026 LOG_LEVEL=DEBUG \
flask run
```

#### Environment Configuration

Required environment variables:

* `PYTHONPATH`: path to `src`
* `TEMP`: path to an existing folder to create temporary locks
* `dataViewsFolder`: path to where your repository of `addon-datastore` is checked out locally
* `branchRef`: Git branch to track for `addon-datastore`
  * Default is `main`
* `COPYRIGHT_YEARS`: String of years displayed on web front-end for add-on store
  * e.g. 2025-2026

Optional environment variables:

* `LOG_LEVEL`: Logging level (INFO/DEBUG)

## Testing

### API Testing

Test the following scenarios:

1. Language/Locale Support:
   * Supported language (e.g., `/es/all/2023.1.0.json`)
   * Unsupported locale defaulting to language (e.g., `/es_FOO/all/2023.1.0.json`)
   * Unsupported language defaulting to English (e.g., `/foo/all/2023.1.0.json`)

2. Version Support:
   * Valid NVDA API version (e.g., `/en/all/2023.1.0.json`)
   * Invalid NVDA API version should return appropriate error

3. Update Endpoint:
   * Valid branch update (e.g., `{"ref": "refs/heads/main"}`)
   * Invalid branch reference should be rejected
