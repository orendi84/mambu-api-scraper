#!/bin/bash
# Mambu API Scraper - Docker Runner
# This script runs the Mambu API scraper in a Docker container with environment variables

# Check if .env file exists and source it
if [ -f .env ]; then
  echo "Loading environment variables from .env file"
  source .env
else
  echo "No .env file found, using defaults or command-line arguments"
fi

# Command-line arguments override .env file
while [ $# -gt 0 ]; do
  case "$1" in
    --api-version=*)
      API_VERSION="${1#*=}"
      shift 1
      ;;
    --log-level=*)
      LOG_LEVEL="${1#*=}"
      shift 1
      ;;
    --language=*)
      LANGUAGE="${1#*=}"
      shift 1
      ;;
    --target-folder-id=*)
      GOOGLE_DRIVE_TARGET_FOLDER_ID="${1#*=}"
      shift 1
      ;;
    --archive-folder-id=*)
      GOOGLE_DRIVE_ARCHIVE_FOLDER_ID="${1#*=}"
      shift 1
      ;;
    --help)
      echo "Usage: ./run_docker.sh [options]"
      echo "Options:"
      echo "  --api-version=VERSION        API version to scrape (default: v2)"
      echo "  --log-level=LEVEL            Logging level (default: INFO)"
      echo "  --language=LANG              Code sample language (default: all)"
      echo "  --target-folder-id=ID        Google Drive target folder ID"
      echo "  --archive-folder-id=ID       Google Drive archive folder ID"
      echo "  --help                       Show this help message"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Use --help for usage information"
      exit 1
      ;;
  esac
done

# Ensure required variables have values
API_VERSION="${API_VERSION:-v2}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
LANGUAGE="${LANGUAGE:-all}"

# Print configuration (redact sensitive info)
echo "== Mambu API Scraper Configuration =="
echo "API Version: $API_VERSION"
echo "Log Level: $LOG_LEVEL"
echo "Language: $LANGUAGE" 
echo "Google Drive Target Folder: ${GOOGLE_DRIVE_TARGET_FOLDER_ID:0:4}...${GOOGLE_DRIVE_TARGET_FOLDER_ID: -4}"
echo "Google Drive Archive Folder: ${GOOGLE_DRIVE_ARCHIVE_FOLDER_ID:0:4}...${GOOGLE_DRIVE_ARCHIVE_FOLDER_ID: -4}"
echo "======================================="

# Ensure output directory exists locally
mkdir -p ./mambu_api_output

# Run the Docker container
docker run --rm \
  -e LOG_LEVEL="$LOG_LEVEL" \
  -e API_VERSION="$API_VERSION" \
  -e LANGUAGE="$LANGUAGE" \
  -e GOOGLE_DRIVE_TARGET_FOLDER_ID="$GOOGLE_DRIVE_TARGET_FOLDER_ID" \
  -e GOOGLE_DRIVE_ARCHIVE_FOLDER_ID="$GOOGLE_DRIVE_ARCHIVE_FOLDER_ID" \
  -e RUNNING_IN_DOCKER="true" \
  -v "$(pwd)/mambu_api_output:/app/output" \
  -v "$(pwd)/drive_service_account_credentials.json:/app/drive_service_account_credentials.json" \
  mambu-api-scraper

echo "Scraping complete. Check ./mambu_api_output for results." 