from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import requests
import time
import logging
import json
from datetime import datetime
from urllib.parse import urljoin
import re
import os
import urllib.request
import zipfile
import io
import ssl
import certifi
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException
import html2text
from collections import deque
import argparse

# --- Add Logging Setup Function --- 
def setup_logging(log_level="INFO"):
    """Sets up basic logging configuration."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(level=level,
                        format='%(asctime)s - %(levelname)s - %(message)s',
                        handlers=[
                            logging.FileHandler("scraper.log", mode='w'), # Log to file
                            logging.StreamHandler()  # Also log to console
                        ])
    # Suppress verbose logging from selenium and urlib3
    logging.getLogger("selenium").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING) # If using websockets
    logging.info(f"Logging configured at level: {log_level}")
# --- End Logging Setup Function --- 

def setup_driver():
    """Initialize and return a Chrome driver for scraping."""
    chrome_options = Options()
    chrome_options.add_argument("--window-size=1920,1080")
    # Don't use headless mode so we can see what's happening
    # chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    try:
        chromedriver_path = get_chromedriver_path()
        service = Service(executable_path=chromedriver_path)
        driver = webdriver.Chrome(service=service, options=chrome_options)
        logging.info(f"Using cached ChromeDriver from: {chromedriver_path}")
        return driver
    except Exception as e:
        logging.error(f"Error setting up ChromeDriver: {str(e)}")
        raise

def clean_text(text):
    """Clean and normalize text."""
    if not text:
        return ""
    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def get_chromedriver_path():
    """Get the path to the ChromeDriver executable."""
    import urllib.request
    import zipfile
    import io
    
    chromedriver_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chromedriver_bin')
    chromedriver_path = os.path.join(chromedriver_dir, 'chromedriver')
    os.makedirs(chromedriver_dir, exist_ok=True)
    
    if not os.path.exists(chromedriver_path):
        logging.info("ChromeDriver not found locally. Downloading...")
        # Match ChromeDriver version to installed Chrome version (134.0.6998.166 -> using 134.0.6998.165)
        chromedriver_url = 'https://storage.googleapis.com/chrome-for-testing-public/134.0.6998.165/mac-arm64/chromedriver-mac-arm64.zip'
        
        try:
            # Download the zip file
            context = ssl.create_default_context(cafile=certifi.where())
            response = urllib.request.urlopen(chromedriver_url, context=context)
            zip_data = io.BytesIO(response.read())
            
            # Extract the correct binary
            with zipfile.ZipFile(zip_data) as zip_file:
                # Find the correct binary path within the zip (it might be nested)
                binary_path_in_zip = None
                for name in zip_file.namelist():
                    # Look for the specific binary path, e.g., 'chromedriver-mac-arm64/chromedriver'
                    if name.endswith('/chromedriver') and not name.startswith('__MACOSX'):
                        binary_path_in_zip = name
                        break
                
                if binary_path_in_zip:
                    logging.info(f"Extracting {binary_path_in_zip} to {chromedriver_path}")
                    with zip_file.open(binary_path_in_zip) as source, open(chromedriver_path, 'wb') as target:
                        target.write(source.read())
                    # Make ChromeDriver executable
                    os.chmod(chromedriver_path, 0o755)
                    logging.info("ChromeDriver downloaded and extracted successfully.")
                else:
                    raise Exception("Could not find chromedriver binary in the downloaded zip file.")
                    
        except Exception as e:
            logging.error(f"Failed to download or extract ChromeDriver: {e}")
            raise  # Re-raise the exception to stop execution
            
    else:
        logging.debug(f"Using existing ChromeDriver at {chromedriver_path}")

    # Check if the file is executable
    if not os.access(chromedriver_path, os.X_OK):
         logging.warning(f"ChromeDriver at {chromedriver_path} is not executable. Attempting to set permissions.")
         os.chmod(chromedriver_path, 0o755)
         if not os.access(chromedriver_path, os.X_OK):
             raise Exception(f"ChromeDriver at {chromedriver_path} is still not executable after setting permissions.")
             
    return chromedriver_path

def handle_overlays(driver, timeout=5):
    """Attempts to find and click common accept/dismiss buttons for overlays (like cookie banners)."""
    accept_selectors = [
        "//button[normalize-space(.)='OK']", # Specific selector for Mambu cookie banner
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
        "button#hs-eu-confirmation-button", # HubSpot
        "button#onetrust-accept-btn-handler", # OneTrust
    ]
    dismiss_selectors = [
        "//button[contains(@aria-label, 'Dismiss')]",
        "//button[contains(@aria-label, 'Close')]",
        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'dismiss')]",
        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'close')]",
        "//span[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'close')]", # Sometimes spans act as close buttons
        "[aria-label*='close']",
    ]

    logging.debug("Attempting to handle overlays...")

    # Try accept buttons first
    for selector in accept_selectors:
        try:
            # Wait briefly for the element to be present and clickable
            wait = WebDriverWait(driver, timeout)
            # Use XPath for most selectors, handle CSS selectors separately
            if selector.startswith("//") or selector.startswith("(//"):
                 element = wait.until(EC.element_to_be_clickable((By.XPATH, selector)))
            else:
                 element = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))

            logging.info(f"Found potential accept button with selector: {selector}. Attempting to click.")
            element.click()
            logging.info("Clicked accept button.")
            time.sleep(1) # Short pause to allow overlay to disappear
            return True # Overlay handled
        except (NoSuchElementException, TimeoutException):
            logging.debug(f"Accept selector not found or not clickable: {selector}")
        except ElementClickInterceptedException:
            logging.warning(f"Accept button click intercepted for selector: {selector}. Trying JavaScript click.")
            try:
                 driver.execute_script("arguments[0].click();", element)
                 logging.info("Clicked accept button using JavaScript.")
                 time.sleep(1)
                 return True # Overlay handled
            except Exception as js_ex:
                 logging.error(f"JavaScript click failed for {selector}: {js_ex}")
        except Exception as e:
            logging.error(f"Error clicking accept button with selector {selector}: {e}")

    # Try dismiss buttons if no accept button worked
    for selector in dismiss_selectors:
        try:
            wait = WebDriverWait(driver, timeout)
            if selector.startswith("//") or selector.startswith("(//"):
                element = wait.until(EC.element_to_be_clickable((By.XPATH, selector)))
            else:
                element = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))

            logging.info(f"Found potential dismiss/close button with selector: {selector}. Attempting to click.")
            element.click()
            logging.info("Clicked dismiss/close button.")
            time.sleep(1)
            return True # Overlay handled
        except (NoSuchElementException, TimeoutException):
            logging.debug(f"Dismiss selector not found or not clickable: {selector}")
        except ElementClickInterceptedException:
             logging.warning(f"Dismiss button click intercepted for selector: {selector}. Trying JavaScript click.")
             try:
                 driver.execute_script("arguments[0].click();", element)
                 logging.info("Clicked dismiss button using JavaScript.")
                 time.sleep(1)
                 return True # Overlay handled
             except Exception as js_ex:
                 logging.error(f"JavaScript click failed for {selector}: {js_ex}")
        except Exception as e:
            logging.error(f"Error clicking dismiss button with selector {selector}: {e}")


    logging.debug("No common overlay buttons found or clicked.")
    return False # No overlay handled

def extract_page_content(driver, url):
    """Extract content from a single documentation page and convert to markdown"""
    try:
        logging.info(f"Navigating to {url}")
        driver.get(url)
        # Wait for page title to be present
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "title")))
        title = driver.title
        logging.info(f"Page loaded: {title}")

        # --- Add overlay handling here ---
        logging.debug("Attempting to handle overlays...")
        try:
            handle_overlays(driver)
            logging.debug("Finished handling overlays.")
        except Exception as overlay_ex:
            logging.error(f"Error during handle_overlays call: {overlay_ex}", exc_info=True)
        # --- End overlay handling ---

        # --- Simulate user interaction to trigger content loading ---
        logging.info("Simulating user interaction to trigger content loading...")
        try:
            # Scroll down gradually
            for scroll_position in [300, 600, 900, 1200]:
                driver.execute_script(f"window.scrollTo(0, {scroll_position});")
                time.sleep(1)  # Short pause between scrolls
                
            # Move mouse (simulated via JavaScript)
            driver.execute_script("""
                // Create and dispatch a mousemove event
                const evt = new MouseEvent('mousemove', {
                    bubbles: true,
                    cancelable: true,
                    view: window
                });
                document.body.dispatchEvent(evt);
            """)
            
            # Wait longer for content to potentially load
            logging.info("Waiting 10 seconds after user interaction simulation...")
            time.sleep(10)
            
            # Try to force content rendering directly
            driver.execute_script("""
                // Try to find the content container
                const contentContainer = document.querySelector('.content_block_text');
                if (contentContainer) {
                    // Log what we found for debugging
                    console.log('Content container found, content length:', contentContainer.innerHTML.length);
                    
                    // Try to expose any hidden elements
                    Array.from(contentContainer.querySelectorAll('*')).forEach(el => {
                        el.style.display = 'block';
                        el.style.visibility = 'visible';
                        el.style.opacity = '1';
                    });
                }
            """)
            
            # One more wait after JavaScript execution
            time.sleep(5)
            
        except Exception as interact_ex:
            logging.error(f"Error during user interaction simulation: {interact_ex}")
        # --- End user interaction simulation ---

        logging.debug("Pause finished.")

        logging.debug("Attempting to parse page source with BeautifulSoup...")
        try:
            page_source = driver.page_source
            soup = BeautifulSoup(page_source, 'html.parser')
            logging.debug("Successfully parsed page source.")
        except Exception as parse_ex:
            logging.error(f"Error parsing page source with BeautifulSoup for {url}: {parse_ex}", exc_info=True)
            return None

        # Extract title - try multiple possible locations
        title = None
        title_selectors = ['h1', '.page-title', '.documentation-title', '.doc-title', '.content_block_article_head h1'] # Added specific title selector
        for selector in title_selectors:
            title_elem = soup.select_one(selector)
            if title_elem:
                title = title_elem.text.strip()
                logging.info(f"Extracted title: '{title}'")
                break
        
        if not title:
            title = "Untitled"
            logging.warning(f"Could not extract title for {url}, using 'Untitled'.")

        # --- Try Multiple Approaches to Find Content ---
        logging.info("Trying multiple approaches to find content...")
        content_md = ""
        
        # Approach 1: Standard container finding
        content_selectors = [
            '.content_block_text',
            'article.content', 
            'main[role="main"]', 
            '#main-content',
            '.article-body',
            'div[itemprop="articleBody"]',
            '.article-content', # Added more specific selectors
            'div.text',
            'table', # Try directly finding a table
        ]
        
        for selector in content_selectors:
            logging.debug(f"Trying to find content with selector: '{selector}'")
            elements = soup.select(selector)
            if elements:
                logging.info(f"Found {len(elements)} potential content elements with selector: '{selector}'")
                
                # Try each found element
                for i, element in enumerate(elements):
                    try:
                        # Try html2text first
                        html_content = str(element)
                        h = html2text.HTML2Text()
                        h.ignore_links = False
                        h.ignore_images = True
                        md = h.handle(html_content).strip()
                        
                        # If we got substantial content, use it
                        if md and len(md) > 100:  # Arbitrary length to filter out tiny snippets
                            logging.info(f"Found substantial content ({len(md)} chars) with selector '{selector}' (element {i+1}/{len(elements)})")
                            content_md = md
                            break
                            
                        # If html2text doesn't work, try get_text()
                        text = element.get_text(separator='\n', strip=True)
                        if text and len(text) > 100:
                            logging.info(f"Found substantial text content ({len(text)} chars) with selector '{selector}' (element {i+1}/{len(elements)})")
                            content_md = text
                            break
                    except Exception as el_ex:
                        logging.error(f"Error processing element {i+1} with selector '{selector}': {el_ex}")
                
                # If we found content, break out of the selector loop
                if content_md:
                    break
        
        # Approach 2: Direct JavaScript Extraction (last resort)
        if not content_md:
            logging.info("Trying direct JavaScript extraction as last resort...")
            try:
                # Extract text content using JavaScript
                js_content = driver.execute_script("""
                    // Try to extract text from the document
                    const extractText = (element) => {
                        if (!element) return '';
                        
                        // Get all text nodes
                        const walker = document.createTreeWalker(
                            element, 
                            NodeFilter.SHOW_TEXT, 
                            null, 
                            false
                        );
                        
                        let text = '';
                        let node;
                        while(node = walker.nextNode()) {
                            if (node.textContent.trim()) {
                                text += node.textContent.trim() + '\\n';
                            }
                        }
                        return text;
                    };
                    
                    // Try the main content area first
                    const contentArea = document.querySelector('.content_block_text');
                    if (contentArea) {
                        return extractText(contentArea);
                    }
                    
                    // Fall back to main content area
                    return extractText(document.body);
                """)
                
                if js_content and len(js_content) > 100:
                    logging.info(f"Successfully extracted content ({len(js_content)} chars) via JavaScript")
                    content_md = js_content
            except Exception as js_ex:
                logging.error(f"Error during JavaScript content extraction: {js_ex}")
        
        if not content_md:
            logging.warning(f"All approaches failed to extract content from {url}")
            return None

        logging.info(f"Successfully extracted and formatted content from {url} (length: {len(content_md)})")
        return {
            'url': url,
            'title': title,
            'content': content_md
        }

    except Exception as e:
        logging.error(f"Error extracting content from {url}: {str(e)}", exc_info=True)
        return None

def get_all_doc_links(driver, start_url):
    """Recursively find all unique documentation links starting from a base URL."""
    logging.info(f"Getting links from: {start_url}")
    driver.get(start_url)
    time.sleep(2)  # Allow page to load
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    doc_links = set()
    processed_urls = set([start_url])
    urls_to_visit = deque([start_url])

    # Define a limit for the number of links to collect during testing
    max_links_to_find = 10 # <-- Updated limit

    while urls_to_visit:
        current_url = urls_to_visit.popleft()
        if not current_url.startswith(start_url):
             logging.debug(f"Skipping external or non-doc link: {current_url}")
             continue # Stay within the documentation section

        try:
            logging.debug(f"Processing URL for links: {current_url}")
            if current_url not in driver.current_url: # Navigate only if not already there
                 driver.get(current_url)
                 time.sleep(1) # Brief pause after navigation

            current_soup = BeautifulSoup(driver.page_source, 'html.parser')
            links = current_soup.find_all('a', href=True)
            logging.debug(f"Found {len(links)} potential links on {current_url}")

            for link in links:
                href = link['href']
                full_url = urljoin(current_url, href)

                # Basic filtering (adjust as needed)
                if full_url.startswith(start_url) and '#' not in full_url and full_url not in processed_urls:
                    if '/docs/' in full_url: # Ensure it looks like a doc page
                         logging.debug(f"Found potential doc link: {full_url}")
                         doc_links.add(full_url)
                         # ---- Stop if we have enough links ----
                         if len(doc_links) >= max_links_to_find:
                             logging.info(f"Reached link limit ({max_links_to_find}). Stopping link collection.")
                             # Convert set to list before returning
                             final_links = list(doc_links)
                             logging.info(f"Collected {len(final_links)} unique doc links: {final_links}")
                             return final_links
                         # ---- End stop condition ----

                    # Add to processed and queue for visiting only if it's within the scope
                    # (Even if it's not a /docs/ page itself, it might contain links to them)
                    processed_urls.add(full_url)
                    urls_to_visit.append(full_url)
                elif full_url in processed_urls:
                     logging.debug(f"Skipping already processed URL: {full_url}")
                else:
                     logging.debug(f"Skipping non-matching URL: {full_url}")

        except Exception as e:
            logging.error(f"Error processing {current_url} for links: {e}")
            # Continue with the next URL in the queue

    # Convert set to list if loop finishes before limit is reached
    final_links = list(doc_links)
    logging.info(f"Finished collecting links. Found {len(final_links)} unique doc links: {final_links}")
    return final_links

# --- Restore Saving Functions --- 
def save_as_json(data, filename):
    """Saves the scraped data as a JSON file."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logging.info(f"JSON data saved to: {filename}")
    except IOError as e:
        logging.error(f"Error saving JSON file {filename}: {e}")
    except TypeError as e:
        logging.error(f"Error serializing data to JSON for {filename}: {e}")

def save_as_markdown(data, filename, total_links):
    """Saves the scraped data as a Markdown file suitable for LLMs."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("# Mambu Documentation\n\n")
            f.write(f"*Generated on: {data.get('scrape_timestamp', datetime.now().isoformat())}*\n\n")
            f.write(f"*Based on {len(data.get('pages', []))} scraped pages (out of {total_links} found links)*\n\n") # Add context
            f.write("## Table of Contents\n\n")
            
            # Generate table of contents only if pages exist
            if data.get('pages'):
                for i, page in enumerate(data['pages']):
                    # Create a simple anchor based on page index or title
                    anchor = f"page-{i+1}-{page.get('title', 'untitled').lower().replace(' ', '-').replace('/', '')}"
                    anchor = re.sub(r'[^a-z0-9-]', '', anchor) # Sanitize anchor
                    f.write(f"- [{page.get('title', 'Untitled')}](#{anchor})\n")
            else:
                f.write("_(No pages successfully scraped to generate table of contents)_\n")
            
            f.write("\n---\n\n")
            
            # Write each page's content if pages exist
            if data.get('pages'):
                for i, page in enumerate(data['pages']):
                    anchor = f"page-{i+1}-{page.get('title', 'untitled').lower().replace(' ', '-').replace('/', '')}"
                    anchor = re.sub(r'[^a-z0-9-]', '', anchor) # Sanitize anchor
                    # Add an anchor target div for robustness
                    f.write(f'<div id="{anchor}"></div>\n') 
                    f.write(f"# {page.get('title', 'Untitled')}\n")
                    f.write(f"*Source: [{page.get('url')}]({page.get('url')})*\n\n")
                    f.write(page.get('content', '_No content extracted_'))
                    f.write("\n\n---\n\n")
            else:
                f.write("_No content extracted for any pages._\n")

        logging.info(f"Markdown data saved to: {filename}")
    except IOError as e:
        logging.error(f"Error saving Markdown file {filename}: {e}")
# --- End Restore Saving Functions --- 

def download_page_direct(url):
    """Attempt to download and extract content directly using requests without a browser."""
    logging.info(f"Attempting direct download of: {url}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://support.mambu.com/docs',
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            logging.error(f"Failed to download {url}: HTTP status {response.status_code}")
            return None
            
        logging.info(f"Successfully downloaded {url} (content size: {len(response.text)} bytes)")
        
        # Parse the HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract title
        title = None
        title_selectors = ['h1', '.page-title', '.documentation-title', '.doc-title', '.content_block_article_head h1'] 
        for selector in title_selectors:
            title_elem = soup.select_one(selector)
            if title_elem:
                title = title_elem.text.strip()
                logging.info(f"Extracted title directly: '{title}'")
                break
                
        if not title:
            title = "Untitled"
            logging.warning(f"Could not extract title directly from {url}, using 'Untitled'.")
        
        # Extract content
        content_selectors = [
            '.content_block_text',
            'article.content', 
            'main[role="main"]', 
            '#main-content',
            '.article-body',
            'div[itemprop="articleBody"]',
        ]
        
        main_content = None
        for selector in content_selectors:
            main_content = soup.select_one(selector)
            if main_content:
                logging.info(f"Found content container using selector: '{selector}' via direct download")
                break
        
        if not main_content:
            logging.warning(f"No content container found in direct download of {url}")
            return None
            
        # First try html2text for a nicely formatted result
        content_md = ""
        try:
            container_html = str(main_content)
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = True
            content_md = h.handle(container_html).strip()
            logging.info(f"Successfully converted direct download content to Markdown (length: {len(content_md)})")
        except Exception as HtEx:
            logging.error(f"html2text conversion failed for direct download content: {str(HtEx)}")
            # Fall back to simple text extraction
            content_md = ""
            
        # If html2text failed, try simple text extraction
        if not content_md:
            logging.warning("html2text failed on direct download content. Falling back to simple text extraction.")
            try:
                content_md = main_content.get_text(separator='\n', strip=True)
                if content_md:
                    logging.info(f"Successfully extracted text using simple fallback (length: {len(content_md)})")
                else:
                    logging.warning("Simple text extraction fallback also yielded no content from direct download")
            except Exception as fallback_ex:
                logging.error(f"Error during fallback text extraction from direct download: {fallback_ex}")
        
        if not content_md.strip():
            logging.warning(f"No content extracted from direct download of {url}")
            return None
            
        logging.info(f"Successfully extracted content via direct download from {url} (length: {len(content_md)})")
        return {
            'url': url,
            'title': title,
            'content': content_md
        }
        
    except Exception as e:
        logging.error(f"Error during direct download of {url}: {str(e)}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Scrape Mambu documentation.")
    parser.add_argument("--start_url", default="https://support.mambu.com/docs", help="The starting URL for scraping.")
    parser.add_argument("--max_depth", type=int, default=100, help="Maximum depth for crawling links from the start URL. Set to 0 to only scrape the start_url itself.")
    parser.add_argument("--output_dir", default=".", help="Directory to save the output files.")
    parser.add_argument("--log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Set the logging level.")
    
    args = parser.parse_args()
    
    setup_logging(args.log_level)
    logging.info("Starting Mambu documentation scraper...")
    logging.info(f"Start URL: {args.start_url}")
    logging.info(f"Max Depth: {args.max_depth}")
    logging.info(f"Output Directory: {args.output_dir}")
    logging.info(f"Log Level: {args.log_level}")
    
    # Create output directory if it doesn't exist
    os.makedirs(args.output_dir, exist_ok=True)
    
    driver = None
    try:
        driver = setup_driver()
        start_time = datetime.now()
        
        # Determine links to scrape
        if args.max_depth == 0 and args.start_url != "https://support.mambu.com/docs":
            # Special case: Scrape only the specified start_url
            logging.info(f"Max depth is 0. Scraping ONLY the specified start URL: {args.start_url}")
            links_to_scrape = [args.start_url]
            total_links_found = 1
        else:
            # Normal case: Collect links based on start_url and max_depth
            logging.info("Collecting documentation links...")
            doc_links = get_all_doc_links(driver, args.start_url)
            total_links_found = len(doc_links)
            logging.info(f"Found {total_links_found} unique documentation pages.")
            
            # Limit the number of pages to scrape based on internal setting
            max_pages_limit = 10
            links_to_scrape = list(doc_links)[:max_pages_limit]
            if len(links_to_scrape) < total_links_found:
                logging.info(f"Limiting scrape to the first {len(links_to_scrape)} pages based on internal test limit.")
            else:
                logging.info(f"Preparing to scrape all {len(links_to_scrape)} found pages.")
        
        documentation = {'pages': [], 'scrape_timestamp': start_time.isoformat()}
        scraped_count = 0
        
        if not links_to_scrape:
            logging.warning("No links found or determined to scrape. Exiting.")
        else:
            logging.info(f"Starting scraping process for {len(links_to_scrape)} page(s)...")
            for i, url in enumerate(links_to_scrape):
                logging.info(f"Scraping page {i+1}/{len(links_to_scrape)}: {url}")
                
                # Use the PDF extraction method
                page_data = extract_page_content(driver, url)
                
                # Fall back to other methods if PDF extraction fails
                if not page_data:
                    logging.info(f"PDF extraction failed, attempting with direct download.")
                    page_data = download_page_direct(url)
                    
                    if not page_data:
                        logging.info(f"Direct download failed, attempting with browser automation.")
                        page_data = extract_page_content(driver, url)
                
                if page_data:
                    documentation['pages'].append(page_data)
                    scraped_count += 1
                    logging.info(f"Successfully scraped and added: {url}")
                else:
                    logging.warning(f"Failed to extract content for: {url}")
                
                # Add a small delay between requests
                time.sleep(1)
        
        # Save the results
        timestamp = start_time.strftime("%Y%m%d_%H%M%S")
        json_filename = os.path.join(args.output_dir, f"mambu_documentation_{timestamp}.json")
        md_filename = os.path.join(args.output_dir, f"mambu_documentation_{timestamp}.md")
        
        logging.info(f"Saving results to {json_filename} and {md_filename}")
        save_as_json(documentation, json_filename)
        save_as_markdown(documentation, md_filename, total_links_found)
        
        end_time = datetime.now()
        duration = end_time - start_time
        logging.info(f"Documentation scraping finished in {duration}")
        logging.info(f"Successfully scraped {scraped_count}/{len(links_to_scrape)} pages.")
    
    except Exception as e:
        logging.critical(f"An unexpected error occurred: {e}", exc_info=True)
    finally:
        if driver:
            driver.quit()
            logging.info("Browser closed.")

if __name__ == "__main__":
    main() 