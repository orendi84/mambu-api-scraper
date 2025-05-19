# Mambu API Documentation Scraper

A tool for scraping API documentation from `api.mambu.com` and uploading it to Google Drive.

## Features

- Scrapes documentation from Mambu API websites (v1, v2, payments, streaming)
- Extracts content into JSON and Markdown formats
- Uploads to Google Drive with versioning (archive old files)
- Supports running locally or in Docker container
- Configurable via environment variables

## Setup

### Prerequisites

- Python 3.9+
- Docker (for container mode)
- Google Drive Service Account credentials

### Installation

1. Clone this repository:
   ```bash
   git clone <repository-url>
   cd mambu-scraper
   ```

2. Create and activate a virtual environment (for local execution):
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Set up Google Drive credentials:
   - Place your Google Drive service account JSON credentials file in the project root as `drive_service_account_credentials.json`
   - Share your target and archive Google Drive folders with the service account email
   - Note the folder IDs for both folders

### Configuration

Create a `.env` file based on `.env.example`:

```bash
cp .env.example .env
```

Edit the `.env` file to include your specific settings:

```
# API Configuration
API_VERSION=v2    # Options: v1, v2, payments, streaming
LOG_LEVEL=DEBUG   # Options: DEBUG, INFO, WARNING, ERROR, CRITICAL
LANGUAGE=all      # Options: all, curl, http, javascript, ruby, python, java, go, php

# Output Configuration  
OUTPUT_DIR=./mambu_api_output
DELAY_BETWEEN_PAGES=1.0

# Google Drive Configuration
GOOGLE_DRIVE_TARGET_FOLDER_ID=your_target_folder_id
GOOGLE_DRIVE_ARCHIVE_FOLDER_ID=your_archive_folder_id

# Docker Configuration
RUNNING_IN_DOCKER=false

# Google Service Account
GOOGLE_APPLICATION_CREDENTIALS=drive_service_account_credentials.json
```

## Usage

### Running Locally

Use the run_local.sh script:

```bash
./run_local.sh
```

Or specify parameters directly:

```bash
./run_local.sh --api-version=v2 --log-level=DEBUG --language=all
```

### Running in Docker

Build the Docker image:

```bash
docker build --platform linux/amd64 -t mambu-api-scraper .
```

Run the container using the run_docker.sh script:

```bash
./run_docker.sh
```

Or specify parameters directly:

```bash
./run_docker.sh --api-version=v2 --log-level=DEBUG --language=all
```

## Output

The scraper generates:
- JSON data with all extracted content
- Markdown formatted documentation
- Both files are saved locally and the Markdown is uploaded to Google Drive

Output files are stored in:
- Local: `./mambu_api_output/` directory
- Google Drive: In the target folder specified by `GOOGLE_DRIVE_TARGET_FOLDER_ID`

## Customization

- Edit `website_scraper.py` to change the extraction logic
- Edit `mambu_api_scraper.py` to modify API-specific behavior
- Adjust environment variables for different API versions or languages

## Troubleshooting

### Common Issues

- **ChromeDriver not found:** Make sure you're using a compatible Chrome/Chromium version
- **Google Drive permissions:** Ensure the service account has Editor access to both folders
- **Environment variables:** Check .env file or command-line parameters are correctly set

### Logs

Logs are printed to stdout. Set `LOG_LEVEL=DEBUG` for detailed debugging information.

## License

[Your License Here]
