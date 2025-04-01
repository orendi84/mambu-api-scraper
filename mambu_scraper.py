# Import necessary libraries
from bs4 import BeautifulSoup # Note: BeautifulSoup is imported but not used in the provided logic. Consider removing if not needed elsewhere.
import requests
import time
import logging
import json
from datetime import datetime
from urllib.parse import urljoin
import re
import concurrent.futures
import threading
import hashlib
from tqdm import tqdm
import pickle
import zlib
from pathlib import Path
import platform # Note: platform is imported but not used. Consider removing.
from requests.adapters import HTTPAdapter
# Corrected import path for Retry based on modern urllib3 structure if needed,
# but requests usually bundles its own vendored version.
# from urllib3.util.retry import Retry
from requests.packages.urllib3.util.retry import Retry # Keep this if it works in your env
from playwright.sync_api import sync_playwright
# Note: asyncio is imported but not used in the sync playwright implementation. Consider removing.
# import asyncio

# --- Logging Setup ---
# Configure logging to write to a file and stream to console
logging.basicConfig(
    level=logging.DEBUG, # Set logging level to DEBUG for more detailed information
    format='%(asctime)s - %(levelname)s - %(message)s', # Define log message format
    handlers=[
        logging.FileHandler('mambu_scraper.log'), # Log to a file named 'mambu_scraper.log'
        logging.StreamHandler() # Also output logs to the console
    ]
)

# --- Rate Limiter Class ---
class RateLimiter:
    """Limits the rate of function calls to avoid overloading the server."""
    def __init__(self, calls_per_second=2):
        """
        Initializes the RateLimiter.
        Args:
            calls_per_second (int): Maximum number of calls allowed per second.
        """
        self.calls_per_second = calls_per_second
        self.interval = 1.0 / self.calls_per_second # Calculate minimum time interval between calls
        self.last_call_time = 0 # Timestamp of the last call
        self.lock = threading.Lock() # Thread lock for safe concurrent access

    def wait(self):
        """Waits if necessary to enforce the rate limit."""
        with self.lock: # Acquire lock to ensure thread safety
            current_time = time.time()
            time_since_last_call = current_time - self.last_call_time
            # Calculate time to wait if the interval hasn't passed
            wait_time = self.interval - time_since_last_call
            if wait_time > 0:
                time.sleep(wait_time) # Pause execution
            self.last_call_time = time.time() # Update the last call time

# --- Mambu Scraper Class ---
class MambuScraper:
    """Scrapes documentation from the Mambu support website."""
    def __init__(self, max_workers=4, calls_per_second=2):
        """
        Initializes the MambuScraper.
        Args:
            max_workers (int): Maximum number of concurrent threads for scraping.
            calls_per_second (int): Maximum requests per second allowed.
        """
        self.base_url = "https://support.mambu.com/docs" # Starting URL for scraping
        self.max_workers = max_workers # Max threads for parallel processing
        self.rate_limiter = RateLimiter(calls_per_second) # Initialize rate limiter
        self.cache_dir = Path('cache') # Directory to store cached page content
        self.cache_dir.mkdir(exist_ok=True) # Create cache directory if it doesn't exist
        self._setup_session() # Configure the requests session
        self.visited_urls = set() # Keep track of visited URLs during link discovery
        # Dictionary to store scraped data
        self.documentation = {
            'timestamp': datetime.now().isoformat(), # Timestamp of when scraping started
            'pages': [], # List to hold content of individual pages
            'common_sections': { # Dictionary to categorize common text patterns found
                'ui_elements': [],
                'configuration_warnings': [],
                'feature_requirements': [],
                'permissions': []
            }
        }
        self.seen_titles = set() # Keep track of page titles to avoid duplicates in output
        self.playwright = None # Playwright instance
        self.browser = None # Browser instance managed by Playwright

    def _setup_browser(self):
        """Initializes Playwright and launches a headless browser instance."""
        logging.info("Setting up Playwright browser...")
        try:
            self.playwright = sync_playwright().start()
            # Launch Chromium browser in headless mode (no GUI)
            self.browser = self.playwright.chromium.launch(headless=True)
            logging.info("Playwright browser launched successfully.")
        except Exception as e:
            logging.error(f"Failed to launch Playwright browser: {e}")
            raise # Reraise exception to stop execution if browser setup fails

    def _cleanup_browser(self):
        """Closes the browser and stops Playwright."""
        logging.info("Cleaning up Playwright browser...")
        if self.browser:
            try:
                self.browser.close()
                logging.info("Browser closed.")
            except Exception as e:
                logging.error(f"Error closing browser: {e}")
        if self.playwright:
            try:
                self.playwright.stop()
                logging.info("Playwright stopped.")
            except Exception as e:
                logging.error(f"Error stopping Playwright: {e}")
        self.browser = None
        self.playwright = None

    def _setup_session(self):
        """Sets up a requests Session with retry logic and headers."""
        # Note: This session is not used by Playwright for page loading,
        # but could be useful if making direct API calls or downloading files.
        session = requests.Session()
        # Define a retry strategy for HTTP requests
        retry_strategy = Retry(
            total=3, # Total number of retries
            backoff_factor=1, # Time factor for exponential backoff between retries
            status_forcelist=[429, 500, 502, 503, 504], # HTTP status codes that trigger a retry
        )
        # Create an adapter with the retry strategy
        adapter = HTTPAdapter(max_retries=retry_strategy)
        # Mount the adapter for both HTTP and HTTPS protocols
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        # Set a user-agent header to mimic a real browser
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36 MambuDocsScraper/1.0'
        })
        self.session = session

    def _get_cache_key(self, url):
        """Generates a unique filename (hash) for caching based on URL."""
        return hashlib.md5(url.encode()).hexdigest()

    def _get_cached_content(self, url):
        """Retrieves page content from cache if it exists and is valid."""
        cache_key = self._get_cache_key(url)
        cache_file = self.cache_dir / f"{cache_key}.pkl"
        if cache_file.exists():
            try:
                with open(cache_file, 'rb') as f:
                    compressed_data = f.read()
                    # Decompress and deserialize the cached data
                    decompressed_data = zlib.decompress(compressed_data)
                    data = pickle.loads(decompressed_data)
                    logging.info(f"Cache hit for URL: {url}")
                    return data
            except Exception as e:
                # Handle errors during cache reading (e.g., corrupted file)
                logging.warning(f"Cache read error for {url}: {e}. Will re-fetch.")
                try:
                    cache_file.unlink() # Attempt to delete corrupted cache file
                except OSError as oe:
                    logging.error(f"Could not delete corrupted cache file {cache_file}: {oe}")
                return None
        logging.info(f"Cache miss for URL: {url}")
        return None # Cache file doesn't exist or was invalid

    def _cache_content(self, url, content):
        """Saves page content to the cache with compression."""
        if content is None: # Do not cache None values
             logging.warning(f"Attempted to cache None content for {url}. Skipping.")
             return
        cache_key = self._get_cache_key(url)
        cache_file = self.cache_dir / f"{cache_key}.pkl"
        try:
            # Serialize and compress the content data
            serialized_data = pickle.dumps(content)
            compressed_data = zlib.compress(serialized_data)
            # Write the compressed data to the cache file
            with open(cache_file, 'wb') as f:
                f.write(compressed_data)
            logging.info(f"Cached content for URL: {url}")
        except Exception as e:
            # Handle errors during caching
            logging.error(f"Cache write error for {url}: {e}")

    # --- MODIFIED clean_text Function ---
    def clean_text(self, text):
        """
        Cleans text by removing leading/trailing whitespace from lines
        and normalizing multiple blank lines into single blank lines.
        Preserves single newlines between paragraphs.
        """
        if not text:
            return ""
        # Split text into lines
        lines = text.splitlines()
        # Strip whitespace from each line and keep non-empty lines
        stripped_lines = [line.strip() for line in lines]
        # Filter out empty lines to avoid excessive blank lines, but keep intended paragraph breaks
        # This simple join might merge lines that were meant to be separate if they only contained whitespace initially.
        # A more sophisticated approach might be needed if precise whitespace preservation is critical.
        # For Markdown feeding, joining non-empty stripped lines is usually sufficient.
        cleaned_text = "\n".join(line for line in stripped_lines if line) # Join non-empty lines with single newline
        return cleaned_text.strip() # Final strip for the whole text block

    def _extract_common_content(self, content):
        """Extracts and categorizes common text patterns using regex."""
        # Define regex patterns for different categories of common info
        patterns = {
            'configuration_warnings': [
                r"If you PUT a configuration to Mambu, any.*?will be deleted",
                r"PATCH requests are not currently supported",
                r"configuration settings not included in the new.*?will be deleted"
            ],
            'ui_elements': [
                r"menu in the top left",
                r"navigation bar",
                r"menu items",
                r"view preferences",
                r"custom views"
            ],
            'feature_requirements': [
                r"feature enabled for your tenant",
                r"feature must be enabled",
                r"requires the.*?feature"
            ],
            'permissions': [
                r"permission required",
                r"user must have.*?permission",
                r"requires.*?permission"
            ]
        }

        # Iterate through categories and patterns
        for category, pattern_list in patterns.items():
            for pattern in pattern_list:
                try:
                    # Find all occurrences of the pattern in the content (case-insensitive)
                    matches = re.finditer(pattern, content, re.IGNORECASE)
                    for match in matches:
                        # Add the found text snippet to the corresponding category
                        self.documentation['common_sections'][category].append(match.group())
                except Exception as e:
                    logging.error(f"Regex error in _extract_common_content for pattern '{pattern}': {e}")


    def _deduplicate_title(self, title):
        """Cleans and standardizes page titles."""
        if not title:
            return ""
        # Remove potential trailing numbers in parentheses (e.g., "Title (1)")
        title = re.sub(r'\s*\(\d+\)$', '', title)
        # Replace multiple whitespace characters with a single space
        title = re.sub(r'\s+', ' ', title)
        return title.strip() # Remove leading/trailing whitespace

    # --- MODIFIED extract_page_content Function ---
    def extract_page_content(self, url):
        """
        Extracts the title and main content from a given documentation page URL using Playwright.
        Uses inner_text() for better formatting preservation, falling back to text_content().
        Applies the modified clean_text function.
        """
        # Check cache first
        cached_content = self._get_cached_content(url)
        if cached_content:
            return cached_content # Return cached data if available

        # Ensure browser is initialized
        if not self.browser:
            logging.error("Browser not initialized in this thread. Cannot process URL.")
            if not self.playwright:
                logging.error("Playwright not initialized. Cannot create context.")
                return None

        context = None
        page = None
        try:
            # Apply rate limiting before making the request
            self.rate_limiter.wait()
            logging.info(f"Processing URL: {url}")

            # Create a new browser context and page for isolation
            context = self.browser.new_context(user_agent=self.session.headers['User-Agent'])
            page = context.new_page()

            # Navigate to the URL, wait for network activity to settle
            page.goto(url, wait_until='networkidle', timeout=60000)

            # --- Extract Title ---
            title = ""
            try:
                title_element = page.locator('h1').first
                title_element.wait_for(state='visible', timeout=5000)
                if title_element.is_visible():
                    raw_title = title_element.text_content()
                    title = self._deduplicate_title(raw_title)
                else:
                    logging.warning(f"h1 title element not visible for {url}")
            except Exception as title_err:
                logging.warning(f"Could not find or access h1 title for {url}: {title_err}")

            # Fallback title if h1 is not found
            if not title:
                title = url.split('/')[-1].replace('-', ' ').title()
                logging.info(f"Using fallback title '{title}' for {url}")

            # --- Extract Main Content ---
            # List of potential CSS selectors for the main content area
            content_selectors = [
                "article.article-content", # Primary target
                "div.article-body",       # Common alternative
                "div.content-body",       # Another possibility
                "div.docs-content",       # Specific to some doc systems
                "article",                # General article tag
                "main",                   # General main tag
                "[role='main']"           # Accessibility role
            ]

            content_text = None
            content_element_found = None
            # Iterate through selectors to find the first matching and visible one
            for selector in content_selectors:
                try:
                    content_element = page.locator(selector).first
                    content_element.wait_for(state='visible', timeout=3000)
                    if content_element.is_visible():
                        content_element_found = content_element
                        logging.info(f"Using selector '{selector}' for main content on {url}")
                        break
                except Exception:
                    logging.debug(f"Selector '{selector}' not found or not visible quickly for {url}")
                    continue

            # Extract text from the found content element
            if content_element_found:
                try:
                    # Extract all text content including formulas and examples
                    content_text = content_element_found.evaluate("""
                        (element) => {
                            // Get all text nodes
                            const walker = document.createTreeWalker(
                                element,
                                NodeFilter.SHOW_TEXT,
                                null,
                                false
                            );
                            
                            let text = '';
                            let node;
                            while (node = walker.nextNode()) {
                                // Get the parent element
                                const parent = node.parentElement;
                                
                                // Skip if parent is a script or style tag
                                if (parent.tagName === 'SCRIPT' || parent.tagName === 'STYLE') {
                                    continue;
                                }
                                
                                // Add the text content
                                text += node.textContent;
                                
                                // Add newlines after block elements
                                if (parent.tagName === 'P' || 
                                    parent.tagName === 'DIV' || 
                                    parent.tagName === 'H1' || 
                                    parent.tagName === 'H2' || 
                                    parent.tagName === 'H3' || 
                                    parent.tagName === 'H4' || 
                                    parent.tagName === 'H5' || 
                                    parent.tagName === 'H6' || 
                                    parent.tagName === 'BR' ||
                                    parent.tagName === 'LI') {
                                    text += '\\n';
                                }
                                
                                // Add extra newline after headings
                                if (parent.tagName === 'H1' || 
                                    parent.tagName === 'H2' || 
                                    parent.tagName === 'H3' || 
                                    parent.tagName === 'H4' || 
                                    parent.tagName === 'H5' || 
                                    parent.tagName === 'H6') {
                                    text += '\\n';
                                }
                            }
                            return text;
                        }
                    """)
                    
                    if not content_text or content_text.isspace():
                        logging.warning(f"JavaScript extraction returned empty/whitespace for {url}. Trying inner_text().")
                        content_text = content_element_found.inner_text(timeout=10000)
                        
                    if not content_text or content_text.isspace():
                        logging.warning(f"inner_text() also returned empty/whitespace for {url}. Trying text_content().")
                        content_text = content_element_found.text_content(timeout=10000)
                        
                    if not content_text or content_text.isspace():
                        logging.error(f"All text extraction methods failed for {url}")
                        content_text = None
                    else:
                        logging.info(f"Successfully extracted content for {url}")
                        
                except Exception as e:
                    logging.error(f"Error extracting content for {url}: {e}")
                    content_text = None
            else:
                logging.warning(f"Could not find a suitable main content element for {url}")
                content_text = None

            # --- Process and Cache Result ---
            if content_text:
                # Apply the clean_text function
                full_content = self.clean_text(content_text)

                # Extract common patterns from the cleaned content
                self._extract_common_content(full_content)

                # Add source attribution
                full_content += f"\n\n*Source: {url}*"

                # Prepare result dictionary
                result = {
                    'title': title,
                    'content': full_content,
                    'url': url
                }
                # Cache the successful result
                self._cache_content(url, result)
                return result
            else:
                logging.error(f"No content extracted for URL: {url}")
                return None

        except Exception as e:
            logging.error(f"General error processing {url}: {str(e)}", exc_info=True)
            return None

        finally:
            if page:
                try:
                    page.close()
                except Exception as e:
                    logging.error(f"Error closing page for {url}: {e}")
            if context:
                try:
                    context.close()
                except Exception as e:
                    logging.error(f"Error closing context for {url}: {e}")

    def get_all_doc_links(self):
        """Discovers all documentation links from the Mambu support website."""
        all_links = set()
        visited = set()
        to_visit = {self.base_url}
        
        context = None
        page = None
        try:
            context = self.browser.new_context()
            page = context.new_page()
            
            while to_visit:
                current_url = to_visit.pop()
                if current_url in visited:
                    continue
                    
                try:
                    logging.info(f"Discovering links on: {current_url}")
                    page.goto(current_url, wait_until='networkidle', timeout=30000)
                    
                    # Wait for the main content to load
                    page.wait_for_selector('a[href*="/docs/"]', timeout=10000)
                    
                    # Get all documentation links
                    links = page.query_selector_all('a[href*="/docs/"]')
                    for link in links:
                        try:
                            href = link.get_attribute('href')
                            if href:
                                full_url = urljoin(self.base_url, href)
                                if full_url.startswith(self.base_url) and '#' not in full_url:
                                    all_links.add(full_url)
                                    if full_url not in visited:
                                        to_visit.add(full_url)
                        except:
                            continue
                    
                    visited.add(current_url)
                    
                except Exception as e:
                    logging.error(f"Error discovering links on {current_url}: {str(e)}")
                    continue
                    
        except Exception as e:
            logging.error(f"Error getting documentation links: {str(e)}")
            
        finally:
            if page:
                page.close()
            if context:
                context.close()
                
        logging.info(f"Link discovery finished. Found {len(all_links)} unique potential documentation links.")
        return all_links


    def process_urls_parallel(self, urls):
        """Processes a list of URLs in parallel using a ThreadPoolExecutor."""
        if not urls:
            logging.warning("No URLs provided to process_urls_parallel.")
            return

        logging.info(f"Starting parallel processing of {len(urls)} URLs with {self.max_workers} workers...")
        # Use ThreadPoolExecutor for concurrent page scraping
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit each URL to the executor for processing by extract_page_content
            # Note the potential issues with sync Playwright and threads mentioned in extract_page_content
            future_to_url = {executor.submit(self.extract_page_content, url): url for url in urls}

            # Process results as they complete, with a progress bar (tqdm)
            for future in tqdm(concurrent.futures.as_completed(future_to_url), total=len(urls), desc="Scraping pages"):
                url = future_to_url[future] # Get the URL associated with the future
                try:
                    page_content = future.result() # Get the result (or exception) from the future
                    # If content was successfully extracted, add it to the documentation list
                    if page_content:
                        # Basic check for minimal content length (optional)
                        if len(page_content.get('content', '')) > 50: # Example threshold
                            self.documentation['pages'].append(page_content)
                        else:
                            logging.warning(f"Page content seems too short for {url}. Skipping addition.")
                    # else: # No need for else, None result is already logged in extract_page_content
                    #    logging.warning(f"No content returned for {url}")

                except Exception as e:
                    # Log any exceptions raised during the execution of extract_page_content
                    logging.error(f"Error processing future for URL {url}: {e}", exc_info=True)

        logging.info("Parallel processing finished.")


    def save_documentation(self):
        """Saves the collected documentation to Markdown and JSON files."""
        logging.info("Saving documentation...")
        # Generate filenames with timestamp
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        md_file = Path(f"mambu_documentation_{timestamp_str}.md")
        json_file = Path(f"mambu_documentation_{timestamp_str}.json")

        # --- Deduplicate and Sort Pages ---
        seen_titles_for_output = set()
        unique_pages = []
        duplicates_skipped = 0

        for page in self.documentation['pages']:
            # Ensure page data is valid
            if not page or not page.get('title') or not page.get('content'):
                logging.warning(f"Skipping invalid page data: {page.get('url', 'URL missing')}")
                continue

            title = page['title'] # Title should already be cleaned by _deduplicate_title

            # Check if this title has already been added to the output
            if title.lower() in seen_titles_for_output: # Case-insensitive check for duplicates
                logging.info(f"Skipping duplicate title: '{title}' from URL: {page['url']}")
                duplicates_skipped += 1
                continue

            # Add the title to the set and the page to the list
            seen_titles_for_output.add(title.lower())
            # *** REMOVED redundant clean_text call here ***
            # page['content'] = self.clean_text(page['content']) # Content is cleaned during extraction
            unique_pages.append(page)

        logging.info(f"Removed {duplicates_skipped} pages due to duplicate titles.")

        # Sort the unique pages alphabetically by title (case-insensitive)
        unique_pages.sort(key=lambda x: x['title'].lower())
        logging.info(f"Saving {len(unique_pages)} unique pages.")

        # --- Write Markdown File ---
        try:
            with open(md_file, 'w', encoding='utf-8') as f:
                f.write("# Mambu Documentation\n\n")
                f.write(f"*Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")

                # Write common sections found
                f.write("## Common Information Snippets\n\n")
                common_found = False
                for section, items in self.documentation['common_sections'].items():
                    unique_items = sorted(list(set(items))) # Deduplicate and sort items
                    if unique_items:
                        common_found = True
                        f.write(f"### {section.replace('_', ' ').title()}\n\n")
                        for item in unique_items:
                            f.write(f"- {item}\n")
                        f.write("\n")
                if not common_found:
                    f.write("*(No common information snippets were extracted based on defined patterns)*\n\n")


                # Write table of contents
                f.write("## Table of Contents\n\n")
                if unique_pages:
                    for page in unique_pages:
                        title = page['title']
                        # Create a simple anchor link (GitHub-style)
                        anchor = title.lower().replace(' ', '-').replace('/', '')
                        anchor = re.sub(r'[^\w\-]+', '', anchor) # Remove non-alphanumeric chars except hyphen
                        f.write(f"- [{title}](#{anchor})\n")
                else:
                    f.write("*(No pages were successfully scraped)*\n")
                f.write("\n")

                # Write page content
                f.write("\n---\n\n") # Separator before content starts
                if unique_pages:
                    for page in unique_pages:
                        # Use the same anchor generation logic for the header ID
                        title = page['title']
                        anchor = title.lower().replace(' ', '-').replace('/', '')
                        anchor = re.sub(r'[^\w\-]+', '', anchor)
                        # Write header with anchor (optional, depends on Markdown flavor)
                        # f.write(f"<h1 id=\"{anchor}\">{page['title']}</h1>\n\n") # HTML anchor
                        f.write(f"# {page['title']}\n\n") # Standard Markdown header
                        f.write(f"{page['content']}\n\n") # Write the cleaned content
                        f.write("---\n\n") # Separator between pages
                else:
                    f.write("*(No page content to display)*\n\n")

            logging.info(f"Markdown documentation saved to: {md_file}")

        except IOError as e:
            logging.error(f"Failed to write Markdown file {md_file}: {e}")
        except Exception as e:
             logging.error(f"An unexpected error occurred writing Markdown file: {e}", exc_info=True)


        # --- Write JSON File ---
        try:
            # Prepare data structure for JSON output
            json_output = {
                'scrape_timestamp': self.documentation['timestamp'],
                'generated_timestamp': datetime.now().isoformat(),
                'base_url': self.base_url,
                'total_pages_scraped': len(unique_pages),
                'pages': unique_pages, # Already sorted and deduplicated
                'common_sections': {k: sorted(list(set(v))) for k, v in self.documentation['common_sections'].items()} # Deduplicate common sections too
            }
            # Write the dictionary to a JSON file with indentation
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(json_output, f, indent=2, ensure_ascii=False)
            logging.info(f"JSON documentation saved to: {json_file}")

        except IOError as e:
            logging.error(f"Failed to write JSON file {json_file}: {e}")
        except TypeError as e:
             logging.error(f"Data serialization error writing JSON file: {e}", exc_info=True)
        except Exception as e:
             logging.error(f"An unexpected error occurred writing JSON file: {e}", exc_info=True)


    def run(self):
        """Main execution method orchestrating the scraping process."""
        start_time = time.time()
        logging.info("Starting Mambu documentation scraper run...")
        try:
            # Set up the headless browser
            self._setup_browser()

            # Discover all documentation links starting from the base URL
            doc_links = self.get_all_doc_links()

            # Check if links were found
            if not doc_links:
                 logging.warning("No documentation links found. Exiting.")
                 return # Exit if no links

            logging.info(f"Found {len(doc_links)} potential documentation pages to scrape.")

            # Process the discovered URLs in parallel to extract content
            self.process_urls_parallel(list(doc_links)) # Convert set to list for executor

            # Save the collected and processed documentation
            self.save_documentation()

            logging.info("Documentation scraping run completed successfully!")

        except Exception as e:
            # Log any critical error during the run
            logging.critical(f"A critical error occurred during the scraper run: {e}", exc_info=True)
        finally:
            # Ensure browser resources are always cleaned up
            self._cleanup_browser()
            end_time = time.time()
            logging.info(f"Scraper run finished in {end_time - start_time:.2f} seconds.")

# --- Main Execution Block ---
def main():
    """Entry point for the script."""
    # Create an instance of the scraper
    # Adjust max_workers based on your system resources and network.
    # Adjust calls_per_second based on the website's tolerance (start low).
    scraper = MambuScraper(max_workers=5, calls_per_second=2)
    # Start the scraping process
    scraper.run()

if __name__ == "__main__":
    # Ensure the main function is called only when the script is executed directly
    main()
