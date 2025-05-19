#!/usr/bin/env python3
"""
Website Documentation Scraper - Extract documentation from websites and save as Markdown.

This script crawls a documentation website, extracts content, and saves it in Markdown format.
It can also upload the resulting file to Google Drive and archive previous versions.
"""

import argparse
import os
import re
import sys
import time
import json
import logging
import html2text
import random
import urllib.request
import urllib.parse
import ssl
import zipfile
import io
import codecs
import subprocess
from datetime import datetime, timedelta
import threading
import certifi
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import traceback
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.options import Options
import urllib3
from webdriver_manager.chrome import ChromeDriverManager
try:
    from flask import Flask, jsonify, request
except ImportError:
    # Flask is optional, only needed for API mode
    pass

try:
    # Google Drive API imports
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google.oauth2 import service_account
    from googleapiclient.errors import HttpError
except ImportError:
    logging.warning("Google Drive API libraries not found. Drive upload functionality will be disabled.")

# --- Global Variables ---
app = Flask(__name__) if 'Flask' in sys.modules else None
current_driver_instance = None
scraping_status = {"status": "idle", "progress": 0, "total": 0, "last_update": None}
scraping_thread = None

# --- Version Information ---
SCRAPER_VERSION = "1.0.0"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36"

# --- Utility Functions ---
def setup_logging(log_level="INFO"):
    """Configure logging with the specified log level."""
    numeric_level = getattr(logging, log_level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {log_level}")
    
    logging.basicConfig(
        level=numeric_level,
        format='%(asctime)s - %(process)d - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("website_scraper.log"),
        ]
    )
    logging.info(f"Logging configured at level: {log_level}")

def setup_driver():
    """Set up and configure a Selenium WebDriver instance for Chrome."""
    global current_driver_instance
    
    # Set up Chrome options
    chrome_options = Options()
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-popup-blocking")
    chrome_options.add_argument(f"user-agent={USER_AGENT}")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # Check if running in Docker
    in_docker = os.environ.get('RUNNING_IN_DOCKER', 'false').lower() == 'true'
    
    if in_docker:
        logging.info("RUNNING_IN_DOCKER is true. Setting up for container execution.")
        chrome_options.add_argument('--headless=new')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        # Allow webdriver-manager to find Chrome and download the correct chromedriver
        try:
            logging.info("Setting up Selenium WebDriver with ChromeDriverManager for Docker execution...")
            # webdriver-manager will use the Chrome installed by the Dockerfile
            # and download the corresponding chromedriver to its cache.
            driver_executable_path = ChromeDriverManager().install()
            logging.info(f"ChromeDriverManager().install() returned path in Docker: {driver_executable_path}")
            service = Service(executable_path=driver_executable_path)
        except Exception as e:
            logging.error(f"Error setting up Selenium WebDriver with ChromeDriverManager in Docker: {e}")
            logging.error("Ensure 'google-chrome-stable' is correctly installed in the Docker image and accessible.")
            # Attempt to find system chromedriver as a fallback, though less reliable
            common_chromedriver_paths = ["/usr/bin/chromedriver", "/usr/local/bin/chromedriver"]
            found_cd = False
            for p in common_chromedriver_paths:
                if os.path.exists(p):
                    try:
                        logging.info(f"Fallback: Attempting to use system chromedriver at {p}")
                        service = Service(executable_path=p)
                        # Test if this service can start a basic browser
                        # This is a bit risky to do here, but can give an early warning
                        # temp_driver_test = webdriver.Chrome(service=service, options=chrome_options)
                        # temp_driver_test.quit()
                        found_cd = True
                        break
                    except Exception as e_fallback:
                        logging.warning(f"Fallback to system chromedriver at {p} failed: {e_fallback}")
            if not found_cd:
                logging.error("ChromeDriverManager failed and no system chromedriver found or usable. WebDriver setup will likely fail.")
                # If all fails, re-raise the original exception from ChromeDriverManager
                raise e
    else:
        logging.info("RUNNING_IN_DOCKER is false or not set. Using ChromeDriverManager for local execution.")
        # Set headless mode unless explicitly requested not to
        headless = os.environ.get('NO_HEADLESS', '').lower() != 'true'
        if not headless:
            logging.info("Running with browser window visible for local testing.")
        else:
            chrome_options.add_argument('--headless=new')
            logging.info("Running in headless mode.")
        
        # Set up service with ChromeDriverManager
        try:
            logging.info("Setting up Selenium WebDriver with ChromeDriverManager for local execution...")
            
            driver_path_from_manager = ChromeDriverManager().install()
            logging.info(f"ChromeDriverManager().install() initially returned path: {driver_path_from_manager}")

            # Determine the actual executable path
            actual_executable_path = driver_path_from_manager
            returned_filename = os.path.basename(driver_path_from_manager)
            
            # Check if the returned path is not the executable 'chromedriver' or if it's not executable
            if returned_filename != "chromedriver" or not os.access(driver_path_from_manager, os.X_OK):
                logging.warning(
                    f"Path from ChromeDriverManager ('{driver_path_from_manager}') is not the 'chromedriver' executable "
                    f"or is not executable. Attempting to locate it in the parent directory."
                )
                parent_dir_of_returned_path = os.path.dirname(driver_path_from_manager)
                candidate_path = os.path.join(parent_dir_of_returned_path, "chromedriver")

                if os.path.isfile(candidate_path) and os.access(candidate_path, os.X_OK):
                    actual_executable_path = candidate_path
                    logging.info(f"Corrected executable path to: {actual_executable_path}")
                else:
                    logging.error(
                        f"Could not find a valid 'chromedriver' executable at '{candidate_path}'. "
                        f"The original path from ChromeDriverManager ('{driver_path_from_manager}') will be used, which will likely fail."
                    )
                    # Fallback to the originally returned path, which will likely cause the known error
                    actual_executable_path = driver_path_from_manager
            else:
                logging.info(f"Using 'chromedriver' executable directly from path: {actual_executable_path}")
            
            service = Service(executable_path=actual_executable_path)

        except Exception as e:
            logging.error(f"Error setting up Selenium WebDriver with ChromeDriverManager: {e}")
            
            # Try fallback to local chromedriver
            try:
                local_driver_path = get_chromedriver_path()
                logging.info(f"Attempting fallback to local ChromeDriver at: {local_driver_path}")
                service = Service(executable_path=local_driver_path)
            except Exception as e2:
                logging.error(f"Local ChromeDriver fallback also failed: {e2}")
                raise
    
    # Create and return the WebDriver
    try:
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(30)
        current_driver_instance = driver
        logging.info(f"Selenium User-Agent: {driver.execute_script('return navigator.userAgent;')}")
        return driver
    except Exception as e:
        logging.error(f"Failed to create WebDriver: {e}")
        raise

# --- Save Functions ---
def save_as_json(data, filename):
    """Save the scraped data as JSON."""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logging.info(f"JSON data saved to: {filename}")

def save_as_markdown(data, filename, total_links_found):
    """Save the scraped data as Markdown format."""
    if not data:
        logging.info("No content to save as Markdown.")
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("# No content was scraped\n\n_This file is a placeholder_\n")
        logging.info("Markdown file saved (no content).")
        return
    
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"# Website Documentation\n\n")
        f.write(f"_Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_\n\n")
        f.write(f"_Contains content from {len(data)}/{total_links_found} pages_\n\n")
        
        # Table of contents
        f.write("## Table of Contents\n\n")
        for index, item in enumerate(data):
            title = item.get("title", f"Untitled Document {index+1}")
            url = item.get("url", "")
            clean_title = clean_title_for_link(title)
            toc_line = f"- [{title}](#{index+1}-{clean_title})"
            f.write(toc_line)
            f.write("\n")
        f.write("\n---\n\n")
        
        # Content sections
        for index, item in enumerate(data):
            title = item.get("title", f"Untitled Document {index+1}")
            url = item.get("url", "")
            content = item.get("content", "")
            source_type = item.get("source_type", "unknown")
            
            f.write(f"## {index+1}. {title}\n\n")
            f.write(f"**Source URL:** {url}\n\n")
            f.write(f"**Source Type:** {source_type}\n\n")
            f.write(f"{content}\n\n")
            f.write("---\n\n")
    
    logging.info(f"Markdown data saved to: {filename}")
    
# --- Google Drive Functions ---
def get_drive_service():
    """Authenticate and create a Google Drive service object."""
    try:
        # Try to find service account credentials
        creds_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', 'drive_service_account_credentials.json')
        if not os.path.exists(creds_path):
            logging.info(f"Attempting to load Google Drive credentials from: {creds_path}")
            return None
        
        scopes = ['https://www.googleapis.com/auth/drive']
        credentials = service_account.Credentials.from_service_account_file(creds_path, scopes=scopes)
        drive_service = build('drive', 'v3', credentials=credentials)
        logging.info("Successfully authenticated with Google Drive API.")
        return drive_service
    except Exception as e:
        logging.error(f"Failed to authenticate with Google Drive API: {e}", exc_info=True)
        return None

def find_and_archive_existing_files(service, target_folder_id, archive_folder_id, filename_prefix="website_documentation_"):
    """Finds files matching a prefix in the target folder and moves them to the archive folder."""
    if not service:
        return

    try:
        # List files in the target folder
        query = f"'{target_folder_id}' in parents and name contains '{filename_prefix}' and mimeType='text/markdown' and trashed=false"
        results = service.files().list(q=query, spaces='drive', fields='files(id, name, parents)').execute()
        items = results.get('files', [])

        if not items:
            logging.info(f"No existing files matching '{filename_prefix}*.md' found in Target Drive folder '{target_folder_id}'. No archiving needed.")
            return

        for item in items:
            file_id = item['id']
            file_name = item['name']
            logging.info(f"Found existing file '{file_name}' (ID: {file_id}) in Target Drive folder.")
            
            try:
                # Move the file by changing its parent
                logging.info(f"Attempting to move '{file_name}' to Archive Drive folder '{archive_folder_id}'...")
                updated_file = service.files().update(
                    fileId=file_id,
                    addParents=archive_folder_id,
                    removeParents=target_folder_id,
                    fields='id, parents'
                ).execute()
                logging.info(f"Successfully moved '{file_name}' (ID: {file_id}) to Archive Drive folder '{archive_folder_id}'. New parents: {updated_file.get('parents')}")
            except Exception as move_error:
                logging.error(f"Error moving file '{file_name}' (ID: {file_id}): {move_error}")
            
    except HttpError as error:
        logging.error(f"An HTTP error occurred while searching/archiving files in Google Drive: {error}", exc_info=True)
    except Exception as e:
        logging.error(f"An error occurred during Google Drive file search/archival: {e}", exc_info=True)


def upload_file_to_drive(service, local_file_path, target_folder_id):
    """Uploads a local file to the specified Google Drive folder."""
    if not service or not os.path.exists(local_file_path) or not target_folder_id:
        if service and not target_folder_id:
            logging.warning("Target Google Drive folder ID not provided. Skipping upload.")
        elif service:
            logging.error(f"Local file not found: {local_file_path}. Skipping upload.")
        return

    file_name = os.path.basename(local_file_path)
    logging.info(f"Attempting to upload '{file_name}' to Target Drive folder '{target_folder_id}'...")
    
    file_metadata = {
        'name': file_name,
        'parents': [target_folder_id]
    }
    media = MediaFileUpload(local_file_path, mimetype='text/markdown', resumable=True)
    
    try:
        uploaded_file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, name, webViewLink'
        ).execute()
        logging.info(f"Successfully uploaded '{uploaded_file.get('name')}' to Google Drive.")
        logging.info(f"File ID: {uploaded_file.get('id')}, View Link: {uploaded_file.get('webViewLink')}")
    except HttpError as error:
        logging.error(f"An HTTP error occurred during Google Drive upload: {error}", exc_info=True)
    except Exception as e:
        logging.error(f"An error occurred during Google Drive upload: {e}", exc_info=True)

def parse_arguments():
    parser = argparse.ArgumentParser(description="Scrape website documentation and optionally upload to Google Drive.")
    parser.add_argument("--start_url", default=os.environ.get('START_URL', "https://docs.example.com"), 
                        help="Starting URL for scraping.")
    parser.add_argument("--output_dir", default=os.environ.get('OUTPUT_DIR', "./output_local"), 
                        help="Directory to save scraped data for local run.")
    parser.add_argument("--log_level", default=os.environ.get('LOG_LEVEL', "INFO"), 
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], 
                        help="Logging level.")
    parser.add_argument("--use_fallback_urls", action="store_true", 
                        help="Skip link discovery and use predefined URLs")
    
    # Headless mode options
    headless_group = parser.add_mutually_exclusive_group()
    headless_group.add_argument("--headless", action="store_true", dest="headless", default=True, 
                                help="Run in headless mode (default).")
    headless_group.add_argument("--no-headless", action="store_false", dest="headless", 
                                help="Disable headless mode (show browser window).")

    # Google Drive options
    parser.add_argument("--target_folder_id", default=os.environ.get('TARGET_DRIVE_FOLDER_ID'), 
                        help="Google Drive folder ID to upload the final Markdown file.")
    parser.add_argument("--archive_folder_id", default=os.environ.get('ARCHIVE_DRIVE_FOLDER_ID'), 
                        help="Google Drive folder ID to archive previous versions.")
    parser.add_argument("--upload_only_file", type=str, default=os.environ.get('UPLOAD_ONLY_FILE'), 
                        help="Path to a local Markdown file to upload directly without scraping.")
    parser.add_argument("--config_file", type=str, 
                        help="Path to a JSON config file for arguments (optional, overrides defaults, overridden by CLI args).")
    
    parser.add_argument("--max_pages", type=int, default=int(os.environ.get('MAX_PAGES', 0)) or None, 
                        help="Maximum number of pages to scrape. Default is all found (0 or empty).")
    parser.add_argument("--delay_between_pages", type=float, 
                        default=float(os.environ.get('DELAY_BETWEEN_PAGES', 1.0)),
                        help="Delay between page requests in seconds.")
    
    return parser.parse_args()

# --- Main Entry Point ---
if __name__ == "__main__":
    if app is not None and os.environ.get('FLASK_RUN_FROM_CLI') != 'true' and \
       len(sys.argv) > 0 and not sys.argv[0].endswith('flask'):
        # Running as a script, not through Flask
        args = parse_arguments()
        # Re-initialize logging with potentially new level from args for CLI mode.
        # setup_logging is called inside main(), which is good.
        # Ensure it uses args.log_level from the final parsed args.
        
        logging.info("Running script in CLI mode.") # This log might appear before setup_logging from main()
                                                # if main() changes the log level.

        if not args.upload_only_file and (not args.target_folder_id or not args.archive_folder_id):
            logging.warning("TARGET_DRIVE_FOLDER_ID or ARCHIVE_DRIVE_FOLDER_ID is not set. "
                            "Google Drive operations will be skipped unless --upload_only_file is used.")
        try:
            # Ensure logging is set up with final args before main runs, if main doesn't do it first thing.
            # main() calls setup_logging(args.log_level) as its first step, so that should be fine.
            # Ensure it uses args.log_level from the final parsed args.
            output_file = main(args)
            if output_file:
                logging.info(f"CLI execution completed. Output: {output_file}")
            else:
                logging.warning("CLI execution completed, but no output file was returned.")
            sys.exit(0)
        except Exception as e:
            logging.critical(f"CLI execution failed: {e}", exc_info=True)
            sys.exit(1)
    else:
        # Running through Flask
        if app is None:
            print("Error: Flask is required for API mode but could not be imported.")
            sys.exit(1)
        
        # Set up signal handler for Flask
        import signal
        def signal_handler(sig, frame):
            global scraping_status
            logging.warning(f"Received signal {sig}, initiating graceful shutdown...")
            scraping_status["status"] = "interrupted"
            if current_driver_instance:
                try:
                    current_driver_instance.quit()
                    logging.info("Browser instance closed due to signal.")
                except:
                    pass
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Run Flask app
        app.run(host='0.0.0.0', port=5000)

# --- Main Script --- 

# --- Content Extraction Functions ---
def clean_text(text):
    """Clean and normalize text content."""
    if not text:
        return ""
    # Replace multiple newlines with a single newline
    text = re.sub(r'\n\s*\n', '\n\n', text)
    # Remove leading/trailing whitespace
    return text.strip()

def clean_title_for_link(title):
    """Clean a title string for use in markdown links."""
    return re.sub(r'[^\w\-]', '', title.lower().replace(' ', '-'))

def handle_overlays(driver, timeout=10):
    """Attempt to close any overlays or popups that might block interaction."""
    try:
        # Common overlay/popup close button selectors - customize based on target site
        overlay_selectors = [
            "button.close", 
            ".modal-close", 
            ".popup-close", 
            ".cookie-banner button",
            "button[aria-label='Close']",
            ".dismiss-button"
        ]
        
        for selector in overlay_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for element in elements:
                    if element.is_displayed():
                        logging.info(f"Closing overlay/popup using selector: {selector}")
                        element.click()
                        time.sleep(0.5)  # Short pause after clicking
            except Exception as e:
                logging.debug(f"Error handling overlay with selector {selector}: {e}")
        
        return True
    except Exception as e:
        logging.warning(f"Error handling overlays: {e}")
        return False

def scroll_to_bottom_and_wait(driver, scroll_pause_time=1, max_scroll_attempts=15):
    """Scroll to the bottom of the page to ensure all dynamic content is loaded."""
    logging.info("Scrolling to bottom of page to load all content...")
    
    # Get initial scroll height
    last_height = driver.execute_script("return document.body.scrollHeight")
    scroll_attempts = 0
    
    while scroll_attempts < max_scroll_attempts:
        # Scroll down to bottom
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        
        # Wait to load page
        time.sleep(scroll_pause_time)
        
        # Calculate new scroll height and compare with last scroll height
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            logging.info(f"Reached bottom of page after {scroll_attempts+1} scroll attempts")
            break
            
        last_height = new_height
        scroll_attempts += 1
    
    # Scroll back to top
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.5)
    
    return True

def extract_sections_from_page(driver, url):
    """Extract documentation sections from a single long-form page."""
    try:
        logging.info(f"Extracting content sections from {url}")
        all_sections = []
        
        # Handle potential overlays
        handle_overlays(driver)
        
        # First, scroll to load all dynamic content
        scroll_to_bottom_and_wait(driver)
        
        # Wait for the main content to load
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "h1"))
            )
        except TimeoutException:
            logging.warning("Timed out waiting for H1 element. Proceeding anyway...")
        
        # Try to find the table of contents or navigation sidebar
        try:
            # Adapt these selectors to match the target site's structure
            nav_selectors = [
                "nav", 
                ".sidebar", 
                ".toc", 
                ".navigation", 
                "#sidebar",
                ".sidebar-menu",
                ".left-menu"
            ]
            
            nav_element = None
            for selector in nav_selectors:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for element in elements:
                    if element.is_displayed() and element.text.strip():
                        nav_element = element
                        logging.info(f"Found navigation/TOC using selector: {selector}")
                        break
                if nav_element:
                    break
                    
            if nav_element:
                # Extract navigation items
                nav_items = nav_element.find_elements(By.CSS_SELECTOR, "a")
                logging.info(f"Found {len(nav_items)} navigation items")
                
                # Process each section linked from the navigation
                for i, nav_item in enumerate(nav_items):
                    section_title = nav_item.text.strip()
                    if not section_title:
                        continue
                        
                    logging.info(f"Processing section {i+1}/{len(nav_items)}: {section_title}")
                    
                    # Click on the nav item to navigate to that section
                    try:
                        driver.execute_script("arguments[0].scrollIntoView(true);", nav_item)
                        time.sleep(0.5)
                        driver.execute_script("arguments[0].click();", nav_item)
                        time.sleep(1)  # Wait for section content to be in view
                    except Exception as e:
                        logging.warning(f"Error clicking navigation item: {e}")
                        continue
                    
                    # Get the content of the section
                    try:
                        # Find the section content (adapt selectors as needed)
                        section_content_element = driver.find_element(By.CSS_SELECTOR, ".content-section, main, .main-content, article")
                        content_html = section_content_element.get_attribute("innerHTML")
                        
                        # Convert HTML to Markdown
                        converter = html2text.HTML2Text()
                        converter.ignore_links = False
                        converter.ignore_images = False
                        converter.body_width = 0  # No line wrapping
                        content_text = converter.handle(content_html)
                        
                        all_sections.append({
                            "title": section_title,
                            "url": url + "#" + section_title.lower().replace(" ", "-"),
                            "content": clean_text(content_text),
                            "source_type": "html_section"
                        })
                    except Exception as e:
                        logging.error(f"Error extracting section content: {e}")
                        all_sections.append({
                            "title": section_title,
                            "url": url,
                            "content": f"Error extracting content: {str(e)}",
                            "source_type": "extraction_failed"
                        })
            else:
                # If no navigation is found, treat the entire page as one document
                logging.info("No navigation found. Extracting entire page content.")
                extract_full_page_content(driver, url, all_sections)
        
        except Exception as e:
            logging.error(f"Error processing navigation: {e}")
            # Fallback to extracting the entire page
            extract_full_page_content(driver, url, all_sections)
            
        return all_sections
        
    except Exception as e:
        logging.error(f"Error extracting sections from page: {e}")
        return [{
            "title": "Extraction Failed",
            "url": url,
            "content": f"Failed to extract content. Error: {str(e)}",
            "source_type": "extraction_failed"
        }]

def extract_full_page_content(driver, url, all_sections):
    """Extract the entire page as a single document when no navigation structure is found."""
    try:
        # Get the page title
        title = driver.title
        
        # Get the main content
        content_selectors = [
            "main", 
            "article", 
            ".content", 
            "#content", 
            ".documentation",
            "body"
        ]
        
        content_element = None
        for selector in content_selectors:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                if element.is_displayed() and element.text.strip():
                    content_element = element
                    logging.info(f"Found main content using selector: {selector}")
                    break
            if content_element:
                break
        
        if not content_element:
            content_element = driver.find_element(By.TAG_NAME, "body")
            
        # Convert to markdown
        content_html = content_element.get_attribute("innerHTML")
        converter = html2text.HTML2Text()
        converter.ignore_links = False
        converter.ignore_images = False
        converter.body_width = 0  # No line wrapping
        content_text = converter.handle(content_html)
        
        all_sections.append({
            "title": title,
            "url": url,
            "content": clean_text(content_text),
            "source_type": "full_page_html"
        })
        
    except Exception as e:
        logging.error(f"Error extracting full page content: {e}")
        all_sections.append({
            "title": "Full Page Extraction Failed",
            "url": url,
            "content": f"Failed to extract full page content. Error: {str(e)}",
            "source_type": "extraction_failed"
        })

def extract_sections_with_headers(driver, url):
    """Extract sections from the page based on header elements (h1, h2, etc.)."""
    try:
        logging.info("Extracting content by headers")
        all_sections = []
        
        # Scroll and make sure all content is loaded
        scroll_to_bottom_and_wait(driver)
        
        # Find all header elements
        headers = driver.find_elements(By.CSS_SELECTOR, "h1, h2, h3")
        
        if not headers:
            logging.warning("No headers found for section extraction. Falling back to full page extraction.")
            extract_full_page_content(driver, url, all_sections)
            return all_sections
            
        logging.info(f"Found {len(headers)} potential section headers")
        
        # Process each header as a section
        for i in range(len(headers)):
            current_header = headers[i]
            header_text = current_header.text.strip()
            
            if not header_text:
                continue
                
            logging.info(f"Processing section {i+1}/{len(headers)}: {header_text}")
            
            try:
                # Get all elements between this header and the next
                section_content = []
                
                # Get the current element and all following siblings until next header
                driver.execute_script("arguments[0].scrollIntoView(true);", current_header)
                
                # Get innerHTML directly (faster in some cases)
                section_html = driver.execute_script("""
                    var header = arguments[0];
                    var nextHeader = arguments[1];
                    var result = "";
                    var current = header;
                    
                    // Include the header itself
                    result += header.outerHTML;
                    
                    // Get all elements until next header
                    current = header.nextElementSibling;
                    while(current && (nextHeader === null || !nextHeader.contains(current) && current !== nextHeader)) {
                        result += current.outerHTML;
                        current = current.nextElementSibling;
                    }
                    
                    return result;
                """, current_header, headers[i+1] if i+1 < len(headers) else None)
                
                # Convert HTML to Markdown
                converter = html2text.HTML2Text()
                converter.ignore_links = False
                converter.ignore_images = False
                converter.body_width = 0  # No line wrapping
                content_text = converter.handle(section_html)
                
                all_sections.append({
                    "title": header_text,
                    "url": url + "#" + header_text.lower().replace(" ", "-").replace(".", ""),
                    "content": clean_text(content_text),
                    "source_type": "header_section"
                })
                
            except Exception as e:
                logging.error(f"Error extracting header section: {e}")
                all_sections.append({
                    "title": header_text,
                    "url": url,
                    "content": f"Error extracting content: {str(e)}",
                    "source_type": "extraction_failed"
                })
        
        return all_sections
        
    except Exception as e:
        logging.error(f"Error in header-based section extraction: {e}")
        return [{
            "title": "Header Extraction Failed",
            "url": url,
            "content": f"Failed to extract content by headers. Error: {str(e)}",
            "source_type": "extraction_failed"
        }]

def extract_page_content(driver, url):
    """Extract content from a page, trying different strategies."""
    try:
        logging.info(f"Extracting content from: {url}")
        driver.get(url)
        
        # Wait for page to load - look for any content
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except TimeoutException:
            logging.warning(f"Page load timed out for: {url}")
            return {
                "title": "Page Load Timeout",
                "url": url,
                "content": "The page took too long to load.",
                "source_type": "timeout"
            }
        
        # Handle any overlays or popups
        handle_overlays(driver)
        
        # Try to get page title
        page_title = driver.title
        
        # First try to extract structured sections based on navigation
        sections = extract_sections_from_page(driver, url)
        
        # If no sections were found or extraction failed, try header-based approach
        if not sections or (len(sections) == 1 and sections[0].get("source_type") == "extraction_failed"):
            logging.info("Navigation-based extraction failed. Trying header-based extraction.")
            sections = extract_sections_with_headers(driver, url)
        
        # If still no success, get the whole page as a single section
        if not sections or (len(sections) == 1 and sections[0].get("source_type") == "extraction_failed"):
            logging.info("Header-based extraction failed. Falling back to full-page extraction.")
            full_page_sections = []
            extract_full_page_content(driver, url, full_page_sections)
            sections = full_page_sections
        
        # If we have multiple sections, return them all
        if len(sections) > 0:
            logging.info(f"Successfully extracted {len(sections)} sections from {url}")
            return sections
        
        # Last resort fallback
        logging.warning(f"All extraction methods failed for {url}. Using minimal fallback.")
        return [{
            "title": page_title or "Unknown Page",
            "url": url,
            "content": "Failed to extract meaningful content after trying multiple strategies.",
            "source_type": "extraction_failed"
        }]
            
    except Exception as e:
        logging.error(f"Error extracting page content: {e}")
        return [{
            "title": "Extraction Error",
            "url": url,
            "content": f"An error occurred during content extraction: {str(e)}",
            "source_type": "extraction_failed"
        }]

# --- Main Scraping Function ---
def main(args):
    """Main function to run the scraper."""
    setup_logging(args.log_level)
    
    md_filename_to_upload = None
    json_filename_to_save = None
    perform_scrape = True

    if args.upload_only_file:
        if not os.path.exists(args.upload_only_file):
            logging.error(f"File specified via --upload_only_file not found: {args.upload_only_file}. Exiting.")
            return
        if not args.upload_only_file.endswith(".md"):
            logging.error(f"File specified via --upload_only_file must be a .md file: {args.upload_only_file}. Exiting.")
            return
        md_filename_to_upload = args.upload_only_file
        # Try to infer a corresponding JSON filename for logging/completeness, though it won't be used for upload
        json_filename_to_save = md_filename_to_upload.replace(".md", ".json") 
        logging.info(f"--upload_only_file specified. Skipping scraping. Will attempt to upload: {md_filename_to_upload}")
        perform_scrape = False
        all_scraped_content = [{"content": "dummy"}] # Ensure Drive upload step is reached, content check is on this list
        num_links_intended = 1 # For logging purposes
    else:
        logging.info("Starting website documentation scraper...")
        logging.info(f"Start URL: {args.start_url}")
        logging.info(f"Output Directory: {args.output_dir}")
        logging.info(f"Log Level: {args.log_level}")

    start_time = time.time()
    all_scraped_content = []
    driver = None

    try:
        if perform_scrape:
            # Initialize the WebDriver
            driver = setup_driver()
            
            # For a single-page documentation site, we'll just extract from the start URL
            logging.info(f"Processing single-page documentation: {args.start_url}")
            sections = extract_page_content(driver, args.start_url)
            
            if sections:
                # Add all sections to the scraped content
                all_scraped_content.extend(sections)
                logging.info(f"Successfully extracted {len(sections)} sections from the documentation page")
            else:
                logging.warning("No sections were extracted from the documentation page")
            
            # Prepare filenames for saving the content
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            json_filename_to_save = os.path.join(args.output_dir, f"website_documentation_{timestamp}.json")
            md_filename_to_upload = os.path.join(args.output_dir, f"website_documentation_{timestamp}.md")
            
            # Ensure output directory exists
            os.makedirs(args.output_dir, exist_ok=True)
            
            # Save the scraped content
            save_as_json(all_scraped_content, json_filename_to_save)
            save_as_markdown(all_scraped_content, md_filename_to_upload, len(sections))
            
        # Google Drive Upload (if configured)
        if md_filename_to_upload and os.path.exists(md_filename_to_upload) and args.target_folder_id:
            drive_service = get_drive_service()
            if drive_service:
                logging.info(f"Proceeding with Google Drive operations for: {md_filename_to_upload}")
                if args.archive_folder_id:
                    find_and_archive_existing_files(drive_service, args.target_folder_id, args.archive_folder_id)
                else:
                    logging.info("Archive folder ID not provided, skipping archiving.")
                upload_file_to_drive(drive_service, md_filename_to_upload, args.target_folder_id)
        elif not args.target_folder_id:
            logging.info("Target Google Drive folder ID not provided, skipping upload.")
        elif md_filename_to_upload and not os.path.exists(md_filename_to_upload):
            logging.error(f"Markdown file {md_filename_to_upload} not found for upload. Skipping.")
        
    except Exception as e:
        logging.error(f"An error occurred in the main scraping process: {e}", exc_info=True)
        if not md_filename_to_upload:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            md_filename_to_upload = os.path.join(args.output_dir, f"website_documentation_error_{timestamp}.md")
        if not json_filename_to_save:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            json_filename_to_save = os.path.join(args.output_dir, f"website_documentation_error_{timestamp}.json")
        raise
        
    finally:
        if driver:
            try:
                driver.quit()
                logging.info("Browser closed.")
            except Exception as e_quit:
                logging.error(f"Error closing browser: {e_quit}")
        
        # Ensure output directory exists
        os.makedirs(args.output_dir, exist_ok=True)
        
        # Log execution time
        end_time = time.time()
        total_time = end_time - start_time
        logging.info(f"Documentation scraping finished in {time.strftime('%H:%M:%S', time.gmtime(total_time))}")
        
        # Log success summary
        if 'all_scraped_content' in locals():
            successful_count = sum(1 for item in all_scraped_content if item.get("source_type") not in ["extraction_failed", "timeout"])
            total_count = len(all_scraped_content)
            logging.info(f"Successfully scraped {successful_count}/{total_count} sections based on initial count.")
    
    return md_filename_to_upload 