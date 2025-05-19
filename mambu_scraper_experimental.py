import base64
import logging
import os
import shutil
import time
import argparse
import ssl
import json
import sys
import certifi
import urllib.request
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

import html2text
import PyPDF2
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import (
    JavascriptException,
    NoSuchElementException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# Google Drive API imports
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# Flask imports
from flask import Flask
import threading # To run scraping in a background thread
import signal # For graceful shutdown

# --- Google Drive Configuration ---
SERVICE_ACCOUNT_FILE = os.environ.get('MAMBU_SCRAPER_SERVICE_ACCOUNT_JSON_PATH', 'drive_service_account_credentials.json')
SCOPES = ['https://www.googleapis.com/auth/drive']

# --- Flask App Setup ---
app = Flask(__name__)

# --- Basic Logging Setup (ensure it's configured early) ---
# This might be redundant if setup_logging() is called robustly later,
# but for /test-env, let's ensure basic config.
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(), 
                    format='%(asctime)s - %(process)d - %(levelname)s - %(message)s')

# --- Global State for Scraping Task ---
scraping_status = {"status": "idle", "message": "", "file_path": None, "error": None}
scraping_thread = None
# Placeholder for the actual driver instance if we need to access it for shutdown
current_driver_instance = None 

# --- Logging Setup Function ---
def setup_logging(log_level="INFO"):
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(level=level,
                        format='%(asctime)s - %(process)d - %(levelname)s - %(message)s',
                        handlers=[
                            logging.FileHandler("scraper.log", mode='w'),
                            logging.StreamHandler()
                        ])
    logging.getLogger("selenium").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.info(f"Logging configured at level: {log_level}")

# --- ChromeDriver Setup Function ---
def setup_driver():
    global current_driver_instance
    script_dir = os.path.dirname(os.path.abspath(__file__))
    pdf_download_dir = os.path.join(script_dir, "pdf_downloads")
    os.makedirs(pdf_download_dir, exist_ok=True)
    logging.info(f"PDFs will be downloaded to: {pdf_download_dir}")

    chrome_options = Options()
    user_agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36"
    chrome_options.add_argument(f"user-agent={user_agent}")
    logging.info(f"Attempting to use User-Agent: {user_agent}")

    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--start-maximized") 
    chrome_options.add_argument("--disable-extensions")

    running_in_docker = os.environ.get("RUNNING_IN_DOCKER", "false").lower() == "true"

    if running_in_docker:
        logging.info("RUNNING_IN_DOCKER is true. Using fixed paths for Chrome and ChromeDriver.")
        chrome_options.binary_location = "/usr/bin/chromium"
        # Ensure headless mode is enabled for Docker/Batch execution
        chrome_options.add_argument("--headless=new")
        logging.info("Headless mode ENABLED for Docker execution.")

        chromedriver_path = "/usr/bin/chromedriver"
        # Add service_args for Docker if needed (e.g., log path)
        # chromedriver_log_path = "/tmp/chromedriver.log"
        # service_args = ["--verbose", f"--log-path={chromedriver_log_path}"]
        service = Service(executable_path=chromedriver_path) # service_args=service_args)
        logging.info(f"Using ChromeDriver at: {chromedriver_path}")
        logging.info(f"Chromium binary location: {chrome_options.binary_location}")
        try:
            logging.info("Setting up Selenium WebDriver with fixed paths for Docker...")
            driver = webdriver.Chrome(service=service, options=chrome_options)
        except Exception as e:
            logging.error(f"Error setting up Selenium WebDriver for Docker: {str(e)}", exc_info=True)
            # You might want to include the ldd checks from your Dockerfile here for debugging if it fails
            raise
    else:
        logging.info("RUNNING_IN_DOCKER is false or not set. Using ChromeDriverManager for local execution.")
        # For local testing, you might want to disable headless to see the browser
        # chrome_options.add_argument("--headless=new") 
        logging.info("Running with browser window visible for local testing.")
        try:
            logging.info("Setting up Selenium WebDriver with ChromeDriverManager for local execution...")
            chromedriver_path = ChromeDriverManager().install() # Or specify for Chromium if needed
            service = Service(executable_path=chromedriver_path)
            driver = webdriver.Chrome(service=service, options=chrome_options)
        except Exception as e:
            logging.error(f"Error setting up Selenium WebDriver with ChromeDriverManager: {str(e)}", exc_info=True)
            raise

    current_driver_instance = driver 
    driver.set_page_load_timeout(60) 
    logging.info("Selenium WebDriver is set up and Chrome session started.")
    actual_ua = driver.execute_script("return navigator.userAgent;")
    logging.info(f"Actual User-Agent in use: {actual_ua}")
    return driver

# --- Text Cleaning Function ---
def clean_text(text):
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# --- get_chromedriver_path (Fallback, likely unused if ChromeDriverManager works) ---
def get_chromedriver_path():
    import urllib.request
    import zipfile
    import io
    
    chromedriver_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chromedriver_bin')
    chromedriver_path = os.path.join(chromedriver_dir, 'chromedriver')
    os.makedirs(chromedriver_dir, exist_ok=True)
    
    if not os.path.exists(chromedriver_path):
        logging.info("ChromeDriver not found locally. Downloading...")
        chromedriver_url = 'https://storage.googleapis.com/chrome-for-testing-public/134.0.6998.165/mac-arm64/chromedriver-mac-arm64.zip'
        try:
            context = ssl.create_default_context(cafile=certifi.where())
            response = urllib.request.urlopen(chromedriver_url, context=context)
            zip_data = io.BytesIO(response.read())
            with zipfile.ZipFile(zip_data) as zip_file:
                binary_path_in_zip = None
                for name in zip_file.namelist():
                    if name.endswith('/chromedriver') and not name.startswith('__MACOSX'):
                        binary_path_in_zip = name
                        break
                if binary_path_in_zip:
                    logging.info(f"Extracting {binary_path_in_zip} to {chromedriver_path}")
                    with zip_file.open(binary_path_in_zip) as source, open(chromedriver_path, 'wb') as target:
                        target.write(source.read())
                    os.chmod(chromedriver_path, 0o755)
                    logging.info("ChromeDriver downloaded and extracted successfully.")
                else:
                    raise Exception("Could not find chromedriver binary in the downloaded zip file.")
        except Exception as e:
            logging.error(f"Failed to download or extract ChromeDriver: {e}")
            raise
    else:
        logging.debug(f"Using existing ChromeDriver at {chromedriver_path}")
    if not os.access(chromedriver_path, os.X_OK):
         logging.warning(f"ChromeDriver at {chromedriver_path} is not executable. Attempting to set permissions.")
         os.chmod(chromedriver_path, 0o755)
         if not os.access(chromedriver_path, os.X_OK):
             raise Exception(f"ChromeDriver at {chromedriver_path} is still not executable after setting permissions.")
    return chromedriver_path

# --- Overlay Handling Function ---
def handle_overlays(driver, timeout=10): 
    accept_selectors = [
        "//button[normalize-space(.)='OK']", 
        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept')]",
        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'agree')]",
        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'allow')]",
        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'confirm')]",
        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'got it')]",
        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'okay')]",
        "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept')]",
        "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'agree')]",
        "[id*='cookie'] button[class*='accept']",
        "[id*='consent'] button[class*='accept']",
        "[aria-label*='consent'] button",
        "button#hs-eu-confirmation-button", 
        "button#onetrust-accept-btn-handler",
    ]
    dismiss_selectors = [
        "//button[contains(@aria-label, 'Dismiss')]",
        "//button[contains(@aria-label, 'Close')]",
        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'dismiss')]",
        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'close')]",
        "//span[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'close')]",
        "[aria-label*='close']",
    ]
    logging.debug("Attempting to handle overlays...")
    for selectors_group in [accept_selectors, dismiss_selectors]:
        for selector in selectors_group:
            try:
                wait = WebDriverWait(driver, timeout)
                element_present_condition = EC.presence_of_element_located(
                    (By.XPATH, selector) if selector.startswith("//") else (By.CSS_SELECTOR, selector)
                )
                element = wait.until(element_present_condition)
                
                if element.is_displayed() and element.is_enabled():
                    logging.info(f"Found potential overlay button: {selector}. Attempting to click.")
                    try:
                        element.click()
                        logging.info("Clicked overlay button.")
                        time.sleep(1.5) 
                        return True 
                    except ElementClickInterceptedException:
                        logging.warning(f"Click intercepted for {selector}. Trying JavaScript click.")
                        try:
                            driver.execute_script("arguments[0].click();", element)
                            logging.info("Clicked overlay button using JavaScript.")
                            time.sleep(1.5) 
                            return True
                        except Exception as js_ex:
                            logging.error(f"JavaScript click failed for {selector}: {js_ex}")
                    except StaleElementReferenceException:
                        logging.warning(f"Element {selector} became stale. Overlay might have disappeared.")
                        return True 
                    except Exception as e_click:
                        logging.error(f"Error clicking overlay button {selector}: {e_click}")
            except TimeoutException:
                logging.debug(f"Overlay selector not found or not ready: {selector}")
            except Exception as e_find:
                logging.error(f"Error finding/processing overlay selector {selector}: {e_find}")
    logging.debug("No common overlay buttons found or handled.")
    return False

# --- Dynamic Scrolling Function (used by extract_page_content) ---
def scroll_to_bottom_and_wait(driver, scroll_pause_time=3, max_scroll_attempts=20, force_visibility_script=None):
    logging.info("Starting dynamic scroll to bottom...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    attempts = 0
    no_change_attempts = 0
    max_no_change_attempts = 3

    while attempts < max_scroll_attempts:
        if force_visibility_script:
            try:
                logging.debug("Executing force_visibility_script during scroll.")
                driver.execute_script(force_visibility_script)
            except Exception as e_script:
                logging.warning(f"Error executing visibility script during scroll: {e_script}")

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(scroll_pause_time)
        new_height = driver.execute_script("return document.body.scrollHeight")

        if new_height == last_height:
            no_change_attempts += 1
            logging.debug(f"Scroll height unchanged ({new_height}). No change attempts: {no_change_attempts}")
            if no_change_attempts >= max_no_change_attempts:
                logging.info(f"Page height stabilized at {new_height} after {attempts + 1} scrolls. Assuming full load.")
                break
        else:
            last_height = new_height
            no_change_attempts = 0 
            logging.debug(f"Page scrolled, new height: {new_height}")
        
        attempts += 1
        if attempts >= max_scroll_attempts:
            logging.warning(f"Reached max scroll attempts ({max_scroll_attempts}).")
            break
    logging.info("Dynamic scroll finished.")
    return

# --- HTML Page Content Extraction Function ---
def extract_page_content(driver, url):
    try:
        logging.info(f"HTML SCRAPE: Navigating to {url}")
        driver.set_page_load_timeout(60) 
        driver.get(url)
        
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "title")))
        title = driver.title
        logging.info(f"HTML SCRAPE: Page loaded: {title}")

        handle_overlays(driver, timeout=10)
        
        force_visibility_script = '''
        let elements = document.querySelectorAll('body *');
        for(let i=0; i < elements.length; i++){
            if(elements[i].style.display === 'none') elements[i].style.display = 'block';
            if(elements[i].style.visibility === 'hidden') elements[i].style.visibility = 'visible';
            if(elements[i].style.opacity === '0') elements[i].style.opacity = '1';
        }
        return true;
        '''

        scroll_to_bottom_and_wait(driver, scroll_pause_time=3, max_scroll_attempts=15, force_visibility_script=None) 

        try:
            WebDriverWait(driver, 20).until(
                lambda d: d.execute_script("return document.body.innerText.length > 500") 
            )
            logging.debug("Sufficient body text detected after scroll.")
        except TimeoutException:
            logging.warning("Timeout waiting for substantial body text after scrolling. Content might be sparse.")
            
        content_selectors_to_try = [
            "main", 
            "article", 
            ".topic", 
            ".docs-page", 
            "#content", 
            "body" 
        ]
        
        html_content = ""
        for selector in content_selectors_to_try:
            try:
                content_element = driver.find_element(By.CSS_SELECTOR, selector)
                if content_element:
                    logging.info(f"HTML SCRAPE: Extracting content from <{selector}> element.")
                    html_content = content_element.get_attribute('outerHTML')
                    break 
            except NoSuchElementException:
                logging.debug(f"HTML SCRAPE: Content selector '{selector}' not found.")
        
        if not html_content:
            logging.warning(f"HTML SCRAPE: Could not find a primary content element. Falling back to full body.")
            html_content = driver.page_source

        h = html2text.HTML2Text()
        h.ignore_links = False 
        h.ignore_images = True 
        markdown_content = h.handle(html_content)
        
        cleaned_markdown = clean_text(markdown_content)
        
        logging.info(f"HTML SCRAPE: Extracted from {url} (Markdown length: {len(cleaned_markdown)})")
        return {"title": title, "url": url, "content": cleaned_markdown, "source_type": "html_scrape"}

    except TimeoutException:
        logging.error(f"HTML SCRAPE: Timeout loading page {url}")
        return {"title": f"Timeout: {url}", "url": url, "content": "Error: Timeout during page load.", "source_type": "html_scrape_error"}
    except Exception as e:
        logging.error(f"HTML SCRAPE: Failed to extract content from {url}: {e}", exc_info=True)
        return {"title": f"Error: {url}", "url": url, "content": f"Error: {str(e)}", "source_type": "html_scrape_error"}

# --- Sitemap Parsing Function (NEW) ---
def get_links_from_sitemap(sitemap_url):
    """Downloads and parses a sitemap to extract URLs, filtering for /docs/ paths."""
    logging.info(f"Attempting to fetch and parse sitemap: {sitemap_url}")
    sitemap_content = download_page_direct(sitemap_url) # Uses existing direct downloader
    
    if not sitemap_content:
        logging.warning(f"Failed to download sitemap content from {sitemap_url}.")
        return []

    doc_links = set()
    try:
        root = ET.fromstring(sitemap_content)
        # Namespaces can be tricky with sitemaps. Common ones are:
        # xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        # Check if a namespace is defined in the root element
        namespace = ''
        if '}' in root.tag:
            namespace = root.tag.split('}')[0] + '}' # e.g. {http://www.sitemaps.org/schemas/sitemap/0.9}
        
        sitemap_ns_display_text = namespace if namespace else "(none)"
        logging.debug(f"Sitemap XML namespace identified: '{sitemap_ns_display_text}'")

        for url_element in root.findall(namespace + 'url'):
            loc_element = url_element.find(namespace + 'loc')
            if loc_element is not None and loc_element.text:
                url = loc_element.text.strip()
                # Filter for URLs that are part of the documentation
                if "support.mambu.com/docs/" in url:
                    doc_links.add(url)
                    logging.debug(f"Found doc link in sitemap: {url}")
        
        logging.info(f"Found {len(doc_links)} documentation links in sitemap: {sitemap_url}")
        return list(doc_links)
    except ET.ParseError as e:
        logging.error(f"Failed to parse sitemap XML from {sitemap_url}: {e}")
        # Optionally save sitemap_content for debugging if XML is malformed
        # with open("debug_sitemap_content.xml", "w", encoding="utf-8") as f_debug_sitemap:
        #     f_debug_sitemap.write(sitemap_content)
        # logging.info("Saved raw sitemap content to debug_sitemap_content.xml")
        return []
    except Exception as e:
        logging.error(f"An unexpected error occurred during sitemap parsing from {sitemap_url}: {e}", exc_info=True)
        return []

# --- Fallback function to provide hard-coded documentation URLs if link discovery fails ---
def get_fallback_doc_links():
    """Returns a list of hardcoded documentation URLs if automatic link discovery fails."""
    logging.info("Using fallback hardcoded documentation URLs")
    # Add some initial known URLs - add more if needed
    return [
        "https://support.mambu.com/docs/introduction-to-the-mambu-system",
        "https://support.mambu.com/docs/credit-arrangements",
        "https://support.mambu.com/docs/using-mambu-for-lending",
        "https://support.mambu.com/docs/creating-loan-accounts",
        "https://support.mambu.com/docs/creating-deposit-accounts"
    ]

# --- Modified Link Discovery Function to use reduced timeouts and be more aggressive ---
def get_all_doc_links(driver, start_url, timeout=30):  # Increased from 15 to handle slower websites
    """Discovers documentation links with reduced timeout and more aggressive fallbacks."""
    logging.info(f"[GET_LINKS] Fetching initial page: {start_url}")
    try:
        driver.get(start_url)
        initial_wait = 10  # Increased from 5 to 10 seconds to allow more time for page load
        logging.info(f"[GET_LINKS] Waiting {initial_wait} seconds for initial page load and JS execution on {start_url}...")
        time.sleep(initial_wait)
        
        # Save page source for debugging
        logging.info("[GET_LINKS] Saving initial page source for debugging")
        try:
            with open("/app/output/debug_start_page.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            logging.info("[GET_LINKS] Saved initial page source to /app/output/debug_start_page.html")
        except Exception as e_save:
            logging.error(f"[GET_LINKS] Error saving page source: {e_save}")

        handle_overlays(driver, timeout=5)  # Increased from 3 to handle multiple popups

        # Also save the page title and URL for debugging
        logging.info(f"[GET_LINKS] Page title: {driver.title}")
        logging.info(f"[GET_LINKS] Current URL after load: {driver.current_url}")

        doc_links = set()
        processed_links_cache = set()

        try:
            logging.info(f"[GET_LINKS] Waiting up to {timeout} seconds for link container '(id, categories)' to be present")
            # Wait for the main navigation link container with reduced timeout
            link_container_present = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.ID, "categories"))
            )
            logging.info(f"[GET_LINKS] Link container 'categories' is present. Now extracting links...")
            
            links_in_container = link_container_present.find_elements(By.TAG_NAME, "a")
            
            if not links_in_container:
                logging.warning(f"[GET_LINKS] No <a> tags found within 'categories' container")
            else:
                for link in links_in_container:
                    href = link.get_attribute("href")
                    if href and href not in processed_links_cache:
                        processed_links_cache.add(href)
                        if href.startswith(start_url) and href != start_url and "/docs/" in href:
                            logging.info(f"[GET_LINKS] Found valid doc link in 'categories': {href}")
                            doc_links.add(href)
        except TimeoutException:
            logging.warning(f"[GET_LINKS] Timeout waiting for link container - trying fallback approach")
        except Exception as e:
            logging.error(f"[GET_LINKS] Error in primary link discovery: {e}")
            
        # Fallback: broader search for any links on the page
        if not doc_links:
            logging.info("[GET_LINKS] Attempting fallback: searching all <a> tags on page")
            try:
                all_links_on_page = driver.find_elements(By.TAG_NAME, "a")
                logging.info(f"[GET_LINKS] Fallback: Found {len(all_links_on_page)} <a> tags. Filtering them...")
                
                for link in all_links_on_page:
                    href = link.get_attribute("href")
                    if href and href not in processed_links_cache and "/docs/" in href and "mambu.com" in href:
                        logging.info(f"[GET_LINKS] Fallback: Found potential doc link: {href}")
                        doc_links.add(href)
            except Exception as e_fallback:
                logging.error(f"[GET_LINKS] Error during fallback link search: {e_fallback}")
        
        # Final processing: normalize URLs
        final_doc_links = set()
        for link_url in doc_links:
            abs_link = urljoin(start_url, link_url)
            if "support.mambu.com/docs" in abs_link:
                parsed_abs_link = urlparse(abs_link)
                normalized_link = parsed_abs_link._replace(query="", fragment="").geturl()
                final_doc_links.add(normalized_link)
                
        logging.info(f"[GET_LINKS] Found {len(final_doc_links)} unique doc links")
        
        # If still no links found, return a hardcoded list
        if not final_doc_links:
            logging.warning("[GET_LINKS] CRITICAL: NO LINKS FOUND. Using hardcoded fallback URLs.")
            return get_fallback_doc_links()
            
        return list(final_doc_links)
        
    except Exception as e:
        logging.error(f"[GET_LINKS] Fatal error in link discovery: {e}")
        # Return a hardcoded list if everything fails
        return get_fallback_doc_links()

# --- PDF Text Extraction Function ---
def extract_text_from_pdf(pdf_path):
    logging.info(f"Extracting text from PDF: {pdf_path}")
    try:
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            text = ""
            for page_num in range(len(reader.pages)):
                page = reader.pages[page_num]
                text += page.extract_text() if page.extract_text() else ""
            logging.info(f"Successfully extracted {len(text)} characters from PDF: {pdf_path}")
            return text
    except Exception as e:
        logging.error(f"Failed to extract text from PDF {pdf_path}: {e}")
        return None

# --- PDF Download and Extraction via CDP ---
def download_and_extract_pdf_content(driver, page_url, title, pdf_download_dir):
    pdf_filename_base = re.sub(r'[^a-zA-Z0-9_-]+', '_', title) 
    pdf_filename_base = pdf_filename_base[:100] 
    pdf_filepath = os.path.join(pdf_download_dir, f"{pdf_filename_base}.pdf")

    logging.info(f"PDF EXPORT (CDP): Attempting for {page_url} (title: {title})")
    try:
        if driver.current_url != page_url:
            logging.debug(f"CDP PDF: Navigating to {page_url} before Page.printToPDF")
            driver.set_page_load_timeout(60)
            driver.get(page_url)
            WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            handle_overlays(driver, 5) 

        print_options = {
            'landscape': False,
            'displayHeaderFooter': False,
            'printBackground': True,
            'preferCSSPageSize': True,
        }
        logging.debug(f"CDP PDF: Executing Page.printToPDF for {page_url}")
        result = driver.execute_cdp_cmd("Page.printToPDF", print_options)
        pdf_data = base64.b64decode(result['data'])
        
        with open(pdf_filepath, 'wb') as f:
            f.write(pdf_data)
        logging.info(f"CDP PDF: Successfully saved PDF to {pdf_filepath}")

        extracted_text = extract_text_from_pdf(pdf_filepath)
        if extracted_text:
            logging.info(f"CDP PDF: Extracted text from PDF {pdf_filepath}")
            return {"title": title, "url": page_url, "content": clean_text(extracted_text), "source_type": "pdf_cdp"}
        else:
            logging.warning(f"CDP PDF: Could not extract text from saved PDF: {pdf_filepath}")
            return None
            
    except TimeoutException:
        logging.error(f"CDP PDF: Timeout during navigation or element wait for {page_url}")
        return None
    except Exception as e:
        logging.error(f"CDP PDF: Failed to download/extract PDF for {page_url}: {e}", exc_info=True)
        return None

# --- download_page_direct (Fallback for sitemap/robots.txt, not primary content) ---
def download_page_direct(url):
    try:
        logging.info(f"Attempting direct download of: {url}")
        # Use a more specific User-Agent, similar to what Selenium uses
        headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36'
        }
        context = ssl.create_default_context(cafile=certifi.where())
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, context=context, timeout=30) as response:
            if response.status == 200:
                content_type = response.headers.get('Content-Type', '').lower()
                charset = response.headers.get_content_charset() or 'utf-8'
                logging.info(f"Successfully downloaded {url} (Content-Type: {content_type}, Charset: {charset})")
                
                if 'text/plain' in content_type or 'text/xml' in content_type or 'application/xml' in content_type:
                    return response.read().decode(charset)
                elif 'application/json' in content_type:
                     return response.read().decode(charset) 
                else:
                    logging.warning(f"Downloaded {url} but content type '{content_type}' is not text/xml/json. Attempting decode.")
                    try:
                        return response.read().decode(charset) 
                    except UnicodeDecodeError:
                        logging.error(f"Could not decode content from {url} with charset {charset}.")
                        return None 
            else:
                logging.error(f"Failed to download {url}. Status code: {response.status}")
                return None
    except Exception as e:
        logging.error(f"Error during direct download of {url}: {e}")
        return None

# --- Saving Functions ---
def save_as_json(data, filename):
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logging.info(f"JSON data saved to: {filename}")
    except Exception as e:
        logging.error(f"Failed to save data to JSON file {filename}: {e}")

def save_as_markdown(data, filename, total_links_found):
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            if not data:
                f.write("# Mambu Documentation Scrape\n\nNo content was scraped.\n")
                logging.info("Markdown file saved (no content).")
                return

            f.write(f"# Mambu Documentation Scrape - Combined ({len(data)} pages processed)\n\n")
            f.write(f"Scraped on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            for i, page_data in enumerate(data):
                title = page_data.get('title', 'Untitled Page')
                url = page_data.get('url', 'N/A')
                content = page_data.get('content', 'No content available.')
                source_type = page_data.get('source_type', 'N/A')

                f.write(f"## {i+1}. {title}\n\n")
                f.write(f"**URL:** [{url}]({url})  \n")
                f.write(f"**Source Type:** {source_type}  \n\n")
                f.write(content)
                f.write("\n\n---\n\n")
            logging.info(f"Markdown data saved to: {filename}")
    except Exception as e:
        logging.error(f"Failed to save data to Markdown file {filename}: {e}")

# --- Google Drive Functions ---
def get_drive_service():
    """Authenticates and returns a Google Drive service client."""
    creds = None
    logging.info(f"Attempting to load Google Drive credentials from: {SERVICE_ACCOUNT_FILE}")
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        logging.error(f"Google Drive service account file not found at: {SERVICE_ACCOUNT_FILE}")
        logging.error("Please ensure the service account JSON key file is correctly placed and the path is accessible.")
        logging.error("If running in a cloud environment, ensure the MAMBU_SCRAPER_SERVICE_ACCOUNT_JSON_PATH environment variable is set correctly.")
        return None
    try:
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build('drive', 'v3', credentials=creds)
        logging.info("Successfully authenticated with Google Drive API.")
        return service
    except Exception as e:
        logging.error(f"Failed to authenticate with Google Drive API using {SERVICE_ACCOUNT_FILE}: {e}", exc_info=True)
        return None

def find_and_archive_existing_files(service, target_folder_id, archive_folder_id, filename_prefix="mambu_documentation_"):
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

# --- Main Scraping Logic (modified to be callable) ---
def main(args):
    # Ensure global current_driver_instance is accessible if main modifies it
    # global current_driver_instance # Only if main itself assigns to it, setup_driver does this.
    setup_logging(args.log_level)
    
    md_filename_to_upload = None
    json_filename_to_save = None # For consistency if uploading only
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
        logging.info("Starting Mambu documentation scraper...")
        logging.info(f"Start URL: {args.start_url}")
        logging.info(f"Output Directory: {args.output_dir}")
        logging.info(f"Log Level: {args.log_level}")

    start_time = time.time()
    if perform_scrape: # Only initialize these if actually scraping
        all_scraped_content = []
        doc_links = [] 
    driver = None

    try:
        if perform_scrape:
            # Skip sitemap download (known to be blocked) and use Selenium-based link discovery directly
            # or use fallback URLs if specified
            if args.use_fallback_urls:
                logging.info("Using predefined URLs as requested by --use_fallback_urls flag")
                doc_links = get_fallback_doc_links()
            else:
                logging.info("Using Selenium-based link discovery")
                driver = setup_driver()
                logging.info(f"Selenium User-Agent: {driver.execute_script('return navigator.userAgent;')}")
                doc_links = get_all_doc_links(driver, args.start_url)

            if doc_links:
                logging.info(f"Successfully retrieved {len(doc_links)} links")
            else:
                logging.warning("No documentation links found through any method. Exiting after link discovery.")
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                json_filename_to_save = os.path.join(args.output_dir, f"mambu_documentation_{timestamp}_nolinks.json")
                md_filename_to_upload = os.path.join(args.output_dir, f"mambu_documentation_{timestamp}_nolinks.md")
                if not os.path.exists(args.output_dir):
                    os.makedirs(args.output_dir)
                save_as_json([], json_filename_to_save)
                save_as_markdown([], md_filename_to_upload, 0)
                return
            
            # Process links - limit to max_pages if specified
            if args.max_pages is not None and args.max_pages > 0:
                logging.info(f"Limiting scraping to a maximum of {args.max_pages} pages from {len(doc_links)} found links.")
                doc_links_to_process = doc_links[:args.max_pages]
            else:
                doc_links_to_process = doc_links
            
            num_links_to_process = len(doc_links_to_process)
            logging.info(f"Preparing to scrape {num_links_to_process} unique documentation pages.")

            # Rest of the code remains the same
            pdf_download_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_downloads")
            
            # Make sure Driver is initialized if needed for PDF extraction
            if not driver:
                logging.info("Initializing WebDriver for PDF extraction")
                driver = setup_driver()

            for i, page_url in enumerate(doc_links_to_process):
                logging.info(f"Processing page {i+1}/{num_links_to_process}: {page_url}")
                
                # Time limit for each page processing
                page_processing_start = time.time()
                MAX_PAGE_PROCESSING_TIME = 20  # Set a timeout for processing each page

                try:
                    # Process page with timeout guard
                    page_title_for_pdf = f"Page_{i+1}_{page_url.split('/')[-1] if page_url.split('/')[-1] else page_url.split('/')[-2]}"
                    
                    # Make sure the driver is ready
                    current_nav_url = driver.current_url
                    if current_nav_url != page_url: 
                        driver.set_page_load_timeout(15)  # Reduced from 30
                        try:
                            driver.get(page_url)
                            WebDriverWait(driver, 10).until(EC.title_is)  # Reduced from 15
                            page_title_for_pdf = driver.title if driver.title else page_title_for_pdf
                        except TimeoutException:
                            logging.warning(f"Quick title grab timed out for {page_url}. Using placeholder")
                        except Exception as e_title:
                            logging.warning(f"Error during title grab for {page_url}: {e_title}")
                    else: 
                         page_title_for_pdf = driver.title if driver.title else page_title_for_pdf

                    # Try PDF download
                    pdf_content_data = download_and_extract_pdf_content(driver, page_url, page_title_for_pdf, pdf_download_dir)
                    
                    if pdf_content_data and pdf_content_data.get("content"):
                        logging.info(f"Successfully extracted content via PDF for: {page_url}")
                        all_scraped_content.append(pdf_content_data)
                    else:
                        logging.warning(f"PDF extraction failed for {page_url}. Falling back to HTML.")
                        html_content_data = extract_page_content(driver, page_url) 
                        if html_content_data and html_content_data.get("content"): 
                            all_scraped_content.append(html_content_data)
                        else:
                            logging.warning(f"HTML extraction also failed for {page_url}.")
                            all_scraped_content.append({
                                "title": f"Failed to scrape: {page_url}",
                                "url": page_url,
                                "content": "Error: Could not retrieve content.",
                                "source_type": "extraction_failed"
                            })
                    
                    # Check if page processing took too long
                    if time.time() - page_processing_start > MAX_PAGE_PROCESSING_TIME:
                        logging.warning(f"Page {page_url} processing took too long ({time.time() - page_processing_start:.1f}s). This might indicate an issue.")
                except Exception as e:
                    logging.error(f"Error processing page {page_url}: {e}")
                    all_scraped_content.append({
                        "title": f"Error: {page_url}",
                        "url": page_url,
                        "content": f"Error during processing: {str(e)}",
                        "source_type": "processing_error"
                    })
                
                # Short delay between pages
                time.sleep(args.delay_between_pages)
                
                # If page took too long, break out but still save what we've got
                if time.time() - page_processing_start > MAX_PAGE_PROCESSING_TIME * 1.5:
                    logging.error(f"Page {page_url} processing exceeded maximum time by a large margin. Stopping further processing to prevent hanging.")
                    break
                
            # After loop, define md_filename and json_filename for saving
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if not os.path.exists(args.output_dir):
                os.makedirs(args.output_dir)
                logging.info(f"Created output directory: {args.output_dir}")
            json_filename_to_save = os.path.join(args.output_dir, f"mambu_documentation_{timestamp}.json")
            md_filename_to_upload = os.path.join(args.output_dir, f"mambu_documentation_{timestamp}.md")
            num_links_intended = len(doc_links_to_process) # For saving functions

        logging.info(f"Successfully scraped {len(all_scraped_content)}/{num_links_intended} pages based on initial link count.")

        # This is reached if scraping is successful or if perform_scrape was false (for upload_only_file)
        # The return for successful scraping or upload_only mode happens after final saves/uploads.

    except KeyboardInterrupt:
        logging.info("Scraping process interrupted by user.")
        # Fallback filenames if not already set
        if not md_filename_to_upload:
            md_filename_to_upload = os.path.join(args.output_dir, f"mambu_documentation_interrupted.md")
        if not json_filename_to_save:
            json_filename_to_save = os.path.join(args.output_dir, f"mambu_documentation_interrupted.json")
        # Allow finally block to clean up and save what it can
        # Re-raise if necessary, or simply let finally run and then the function will end.
        # For the Flask context, we don't want sys.exit() here.
        # The run_scraping_task_wrapper will catch this if re-raised.
        raise # Or set a status and return a specific value

    except Exception as e:
        logging.error(f"An critical error occurred in the main scraping process: {e}", exc_info=True)
        if not md_filename_to_upload:
            md_filename_to_upload = os.path.join(args.output_dir, f"mambu_documentation_error.md")
        if not json_filename_to_save:
            json_filename_to_save = os.path.join(args.output_dir, f"mambu_documentation_error.json")
        # Allow finally block to clean up and save what it can
        raise # Re-raise for the Flask wrapper to catch and report

    finally:
        if driver:
            try:
                driver.quit()
                logging.info("Browser closed.")
            except Exception as e_quit:
                logging.error(f"Error closing browser: {e_quit}")
        
        # Ensure output directory exists if we need to save fallback files
        if not os.path.exists(args.output_dir):
            try:
                os.makedirs(args.output_dir)
            except OSError as e_mkdir:
                logging.error(f"Could not create output directory {args.output_dir} in finally block: {e_mkdir}")
                # If dir creation fails, saving might fail too.

        # Ensure filenames are defined before saving, especially if an error occurred early or for upload_only_file.
        # If md_filename_to_upload is already set (e.g. by upload_only_file, or by successful scrape before error/interrupt),
        # use it. Otherwise, create fallback names.
        if not md_filename_to_upload and not perform_scrape and args.upload_only_file:
            # This case should mean args.upload_only_file was set but perhaps failed before md_filename_to_upload was fully assigned
            md_filename_to_upload = args.upload_only_file # Try to use it if available
            if not os.path.exists(md_filename_to_upload):
                 logging.warning(f"upload_only_file {md_filename_to_upload} does not exist in finally block.")
                 # md_filename_to_upload = None # Reset if not valid

        if not md_filename_to_upload: 
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            status_suffix = "_cleanup_save"
            if scraping_status.get("status") == "failed" : status_suffix = "_error_save"
            if scraping_status.get("status") == "interrupted": status_suffix = "_interrupted_save"
            
            md_filename_to_upload = os.path.join(args.output_dir, f"mambu_documentation_{timestamp}{status_suffix}.md")
            json_filename_to_save = os.path.join(args.output_dir, f"mambu_documentation_{timestamp}{status_suffix}.json")
            logging.warning(f"MD/JSON filenames were not set prior to finally, using fallback names: {md_filename_to_upload}")
        elif not json_filename_to_save: # md_filename_to_upload was set, but json_filename_to_save might not have been (e.g. upload_only)
             json_filename_to_save = md_filename_to_upload.replace(".md", ".json")

        # Save whatever content was gathered, or an empty file if error before content gathering.
        # 'all_scraped_content' should exist. If an early error, it might be empty.
        # 'num_links_intended' might not be set if error was very early.
        current_scraped_content = all_scraped_content if 'all_scraped_content' in locals() else []
        current_num_links = num_links_intended if 'num_links_intended' in locals() else 0

        save_as_json(current_scraped_content, json_filename_to_save)
        save_as_markdown(current_scraped_content, md_filename_to_upload, current_num_links)
        
        # --- Google Drive Upload --- (Also attempt this in finally if md_filename_to_upload is valid)
        if md_filename_to_upload and os.path.exists(md_filename_to_upload) and args.target_folder_id:
            drive_service = get_drive_service()
            if drive_service:
                logging.info(f"FINALLY: Proceeding with Google Drive operations for: {md_filename_to_upload}")
                if args.archive_folder_id:
                    find_and_archive_existing_files(drive_service, args.target_folder_id, args.archive_folder_id)
                else:
                    logging.info("FINALLY: Archive folder ID not provided, skipping archiving.")
                upload_file_to_drive(drive_service, md_filename_to_upload, args.target_folder_id)
        elif not args.target_folder_id:
            logging.info("FINALLY: Target Google Drive folder ID not provided, skipping upload.")
        elif md_filename_to_upload and not os.path.exists(md_filename_to_upload):
            logging.error(f"FINALLY: Markdown file {md_filename_to_upload} not found for upload. Skipping.")
        else:
            logging.info("FINALLY: No valid file to upload, or Drive not configured. Skipping upload.")
        # --- End Google Drive Upload in finally ---

        end_time = time.time()
        total_time = end_time - start_time
        logging.info(f"Scraping process finished or terminated. Total execution time: {time.strftime('%H:%M:%S', time.gmtime(total_time))}")
        
        # This log about successful pages might be misleading if called after an error.
        # The scraping_status in the Flask wrapper is a better indicator of overall success/failure.
        # successful_pages = sum(1 for item in current_scraped_content if item.get("source_type") not in ["extraction_failed", "html_scrape_error"] and item.get("content"))
        # logging.info(f"FINALLY: Processed {successful_pages}/{current_num_links} pages.")

    # If we reach here without an exception being re-raised from try/except blocks, it means success.
    return md_filename_to_upload

# --- Flask Task Runner ---
def run_scraping_task_wrapper(args_dict):
    global scraping_status, current_driver_instance
    try:
        scraping_status = {"status": "running", "message": "Scraping process started.", "file_path": None, "error": None}
        
        class ArgsNamespace: # Helper to pass dict as object to main()
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
        args_for_main = ArgsNamespace(**args_dict)
        
        output_file_path = main(args_for_main) # main() is the original scraping logic

        if output_file_path and os.path.exists(output_file_path):
             scraping_status = {"status": "completed", "message": "Scraping completed successfully.", "file_path": output_file_path, "error": None}
        elif output_file_path: # Returned a path, but file doesn't exist
             scraping_status = {"status": "failed", "message": f"Scraping completed, but output file {output_file_path} not found.", "file_path": output_file_path, "error": "Output file missing"}
        else: # main returned None or empty
            scraping_status = {"status": "failed", "message": "Scraping completed, but no output file path was returned.", "file_path": None, "error": "No output path from main"}

    except Exception as e:
        logging.error(f"Exception during scraping task: {str(e)}", exc_info=True)
        scraping_status = {"status": "failed", "message": "Scraping process failed.", "file_path": None, "error": str(e)}
    finally:
        if current_driver_instance: # Clean up driver if it was created
            try:
                logging.info("Ensuring browser is closed after scraping task.")
                current_driver_instance.quit()
                current_driver_instance = None
            except Exception as e_quit:
                logging.error(f"Error closing browser in task wrapper: {e_quit}")


@app.route('/scrape', methods=['POST'])
def trigger_scrape_route():
    logging.error("TRIGGER_SCRAPE_ROUTE_STARTED_TEST_LOG")
    global scraping_status, scraping_thread
    
    try:
        if scraping_thread and scraping_thread.is_alive():
            logging.info("Scraping already in progress, returning 409.")
            return jsonify({"status": "error", "message": "Scraping is already in progress."}), 409

        req_data = {}
        try:
            if request.is_json:
                req_data = request.json
                logging.info(f"Successfully parsed request.json: {req_data}")
            else:
                logging.info(f"Request is not JSON. Content-Type: {request.content_type}. req_data remains empty {{}}.")
        except Exception as e_req:
            logging.error(f"CRITICAL: Error accessing request.json or request.is_json: {str(e_req)}", exc_info=True)
            req_data = {} 

        logging.info(f"Final req_data before processing env vars: {req_data}")

        env_target_folder_id = os.environ.get('TARGET_DRIVE_FOLDER_ID')
        env_archive_folder_id = os.environ.get('ARCHIVE_DRIVE_FOLDER_ID')
        logging.info(f"Read TARGET_DRIVE_FOLDER_ID from env: '{env_target_folder_id}'")
        logging.info(f"Read ARCHIVE_DRIVE_FOLDER_ID from env: '{env_archive_folder_id}'")

        args_for_main_dict = {
            'start_url': req_data.get('start_url', os.environ.get('START_URL', "https://support.mambu.com/docs")),
            'output_dir': req_data.get('output_dir', os.environ.get('OUTPUT_DIR', "/tmp/scraper_output")), 
            'log_level': req_data.get('log_level', os.environ.get('LOG_LEVEL', "INFO")),
            'headless': True,
            'no_headless': False,
            'target_folder_id': env_target_folder_id or req_data.get('target_folder_id'),
            'archive_folder_id': env_archive_folder_id or req_data.get('archive_folder_id'),
            'upload_only_file': req_data.get('upload_only_file', os.environ.get('UPLOAD_ONLY_FILE')),
            'config_file': None,
            'max_pages': int(req_data.get('max_pages', os.environ.get('MAX_PAGES', 0))) or None,
            'delay_between_pages': float(req_data.get('delay_between_pages', os.environ.get('DELAY_BETWEEN_PAGES', 1.0))),
            'use_fallback_urls': req_data.get('use_fallback_urls', False),
        }
        logging.info(f"Args for main - target_folder_id: '{args_for_main_dict['target_folder_id']}'")
        logging.info(f"Args for main - archive_folder_id: '{args_for_main_dict['archive_folder_id']}'")
        logging.info(f"Args for main - upload_only_file: '{args_for_main_dict['upload_only_file']}'")
        logging.info(f"Args for main - output_dir: '{args_for_main_dict['output_dir']}'")

        os.makedirs(args_for_main_dict['output_dir'], exist_ok=True) 
        logging.info(f"Ensured output directory exists: {args_for_main_dict['output_dir']}")
                
        actual_scraping_thread = threading.Thread(target=run_scraping_task_wrapper, args=(args_for_main_dict,))
        actual_scraping_thread.start()
        scraping_thread = actual_scraping_thread
        
        logging.info("Scraping process initiated successfully (Drive IDs check bypassed for /scrape endpoint).")
        return jsonify({"status": "success", "message": "Scraping process initiated (Drive IDs check bypassed)."}), 202

    except Exception as e_route:
        logging.error(f"CRITICAL UNHANDLED EXCEPTION IN trigger_scrape_route: {str(e_route)}", exc_info=True)
        return jsonify({"status": "error", "message": f"Internal server error in route handler: {str(e_route)}"}), 500

@app.route('/status', methods=['GET'])
def get_status_route():
    global scraping_status
    return jsonify(scraping_status)

# --- Test Environment Variables Endpoint ---
@app.route('/test-env', methods=['GET'])
def test_env_route():
    logging.error("TEST_ENV_ROUTE_CALLED") # Conspicuous log
    target_id = os.environ.get('TARGET_DRIVE_FOLDER_ID')
    archive_id = os.environ.get('ARCHIVE_DRIVE_FOLDER_ID')
    
    logging.error(f"TEST_ENV - TARGET_DRIVE_FOLDER_ID: '{target_id}'")
    logging.error(f"TEST_ENV - ARCHIVE_DRIVE_FOLDER_ID: '{archive_id}'")
    
    service_account_path_env = os.environ.get('MAMBU_SCRAPER_SERVICE_ACCOUNT_JSON_PATH')
    logging.error(f"TEST_ENV - MAMBU_SCRAPER_SERVICE_ACCOUNT_JSON_PATH: '{service_account_path_env}'")
    
    output_dir_env = os.environ.get('OUTPUT_DIR')
    logging.error(f"TEST_ENV - OUTPUT_DIR: '{output_dir_env}'")

    response_data = {
        "TARGET_DRIVE_FOLDER_ID": target_id,
        "ARCHIVE_DRIVE_FOLDER_ID": archive_id,
        "MAMBU_SCRAPER_SERVICE_ACCOUNT_JSON_PATH": service_account_path_env,
        "OUTPUT_DIR": output_dir_env
    }

    if target_id and archive_id:
        response_data["message"] = "Environment variables read successfully."
        return jsonify(response_data), 200
    else:
        response_data["message"] = "ERROR: One or more critical environment variables (folder IDs) are missing or empty."
        return jsonify(response_data), 500

# --- Argument Parsing for Local CLI execution ---
def parse_arguments():
    parser = argparse.ArgumentParser(description="Scrape Mambu documentation and optionally upload to Google Drive.")
    parser.add_argument("--start_url", default=os.environ.get('START_URL', "https://support.mambu.com/docs"), help="Starting URL for scraping.")
    parser.add_argument("--output_dir", default=os.environ.get('OUTPUT_DIR', "./output_local"), help="Directory to save scraped data for local run.")
    parser.add_argument("--log_level", default=os.environ.get('LOG_LEVEL', "INFO"), choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], help="Logging level.")
    parser.add_argument("--use_fallback_urls", action="store_true", help="Skip link discovery and use predefined URLs")
    
    # Keep existing arguments
    headless_group = parser.add_mutually_exclusive_group()
    headless_group.add_argument("--headless", action="store_true", dest="headless", default=True, help="Run in headless mode (default).")
    headless_group.add_argument("--no-headless", action="store_false", dest="headless", help="Disable headless mode (show browser window).")

    parser.add_argument("--target_folder_id", default=os.environ.get('TARGET_DRIVE_FOLDER_ID'), help="Google Drive folder ID to upload the final Markdown file.")
    parser.add_argument("--archive_folder_id", default=os.environ.get('ARCHIVE_DRIVE_FOLDER_ID'), help="Google Drive folder ID to archive previous versions.")
    parser.add_argument("--upload_only_file", type=str, default=os.environ.get('UPLOAD_ONLY_FILE'), help="Path to a local Markdown file to upload directly without scraping.")
    parser.add_argument("--config_file", type=str, help="Path to a JSON config file for arguments (optional, overrides defaults, overridden by CLI args).")
    
    parser.add_argument("--max_pages", type=int, default=int(os.environ.get('MAX_PAGES', 0)) or None, help="Maximum number of pages to scrape. Default is all found (0 or empty).")
    parser.add_argument("--delay_between_pages", type=float, default=float(os.environ.get('DELAY_BETWEEN_PAGES', 1.0)), help="Delay in seconds between processing pages.")

    # Add the rest of the argument parsing code
    cli_args = parser.parse_args()

    if cli_args.config_file:
        try:
            with open(cli_args.config_file, 'r') as f:
                config_data = json.load(f)
            
            # Create a temporary namespace with config data to iterate
            temp_config_ns = argparse.Namespace(**config_data)

            # Override parsed_args defaults with config_file values
            # CLI-provided values (non-default) will retain precedence due to initial parse_args()
            for key in vars(temp_config_ns):
                if hasattr(cli_args, key):
                    # If the current value in cli_args is the default, update it from config
                    if getattr(cli_args, key) == parser.get_default(key):
                        setattr(cli_args, key, getattr(temp_config_ns, key))
                    # If CLI arg was set, it stands (already different from default)
                else:
                    # Config file has an arg parser doesn't know, could warn or add if dynamic
                    logging.debug(f"Config file contains key '{key}' not defined in parser, ignoring.")

        except FileNotFoundError:
            logging.warning(f"Config file {cli_args.config_file} not found. Using other arguments.")
        except json.JSONDecodeError:
            logging.error(f"Error decoding JSON from config file {cli_args.config_file}. Using other arguments.")
        except Exception as e:
            logging.error(f"Error processing config file {cli_args.config_file}: {e}. Using other arguments.")
            
    return cli_args

# --- Graceful Shutdown Handler ---
def signal_handler(signum, frame):
    global scraping_thread, current_driver_instance
    logging.warning(f"Signal {signum} received. Initiating graceful shutdown...")
    if current_driver_instance:
        try:
            logging.info("Attempting to close browser due to signal...")
            current_driver_instance.quit()
            current_driver_instance = None
        except Exception as e:
            logging.error(f"Error closing browser during signal handling: {e}")
    if scraping_thread and scraping_thread.is_alive():
        logging.info("Scraping thread is active. It will be allowed to complete or timeout based on its own logic.")
    logging.info("Exiting application due to signal.")
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# --- Script Entry Point ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    run_as_server = os.environ.get("PORT") is not None or os.environ.get("RUN_AS_SERVER", "false").lower() == "true"

    if run_as_server:
        # Configure logging for server mode before app starts
        # (setup_logging might be called again in main if run via CLI)
        # For Flask, it's better to let Flask/Gunicorn manage basic logging config and then customize handlers.
        # Here, we ensure our desired format and level are set up if not already by a runner.
        # setup_logging(os.environ.get('LOG_LEVEL', "INFO")) # Call once for server mode
        
        # If this script is run directly with PORT set, use Flask's dev server.
        # In Cloud Run, gunicorn (or another WSGI server) will be used, specified in Dockerfile or Procfile.
        # Example: CMD ["gunicorn", "-w", "2", "-b", ":8080", "mambu_scraper_experimental:app"]
        logging.info(f"Starting Flask server on host 0.0.0.0 port {port}. Debug mode: False.")
        app.run(host='0.0.0.0', port=port, debug=False)
    else:
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

    # To run the Flask app locally for testing the /scrape endpoint:
    # app.run(debug=True, host='0.0.0.0', port=8080) 