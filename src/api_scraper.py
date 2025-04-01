import os
import time
import logging
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import json
import requests
from urllib.parse import urljoin
import concurrent.futures
from functools import partial
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import threading
from queue import Queue
import hashlib
from tqdm import tqdm
import pickle
import zlib
from pathlib import Path

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('mambu_api_scraper.log'),
        logging.StreamHandler()
    ]
)

class RateLimiter:
    def __init__(self, calls_per_second=2):
        self.calls_per_second = calls_per_second
        self.last_call_time = 0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            current_time = time.time()
            time_since_last_call = current_time - self.last_call_time
            if time_since_last_call < (1.0 / self.calls_per_second):
                time.sleep((1.0 / self.calls_per_second) - time_since_last_call)
            self.last_call_time = time.time()

class MambuAPIDocScraper:
    def __init__(self, max_workers=4, batch_size=10, calls_per_second=2):
        self.base_url = "https://api.mambu.com/v2"
        self.docs_url = "https://api.mambu.com/v2/docs"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        self.visited_urls = set()
        self.api_docs = []
        self.max_workers = max_workers
        self.batch_size = batch_size
        self.cache_dir = Path('cache')
        self.rate_limiter = RateLimiter(calls_per_second)
        self._setup_cache()
        self._setup_session()
        self.progress_bar = None

    def _setup_cache(self):
        """Set up the cache directory"""
        self.cache_dir.mkdir(exist_ok=True)

    def _setup_session(self):
        """Set up a session with retry strategy"""
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update(self.headers)
        self.session = session

    def _get_cache_key(self, url):
        """Generate a cache key for a URL"""
        return hashlib.md5(url.encode()).hexdigest()

    def _get_cached_content(self, url):
        """Get content from cache if available"""
        cache_key = self._get_cache_key(url)
        cache_file = self.cache_dir / f"{cache_key}.pkl"
        if cache_file.exists():
            try:
                with open(cache_file, 'rb') as f:
                    compressed_data = f.read()
                    decompressed_data = zlib.decompress(compressed_data)
                    return pickle.loads(decompressed_data)
            except:
                return None
        return None

    def _cache_content(self, url, content):
        """Cache content for a URL using compression"""
        cache_key = self._get_cache_key(url)
        cache_file = self.cache_dir / f"{cache_key}.pkl"
        try:
            serialized_data = pickle.dumps(content)
            compressed_data = zlib.compress(serialized_data)
            with open(cache_file, 'wb') as f:
                f.write(compressed_data)
        except:
            pass

    def get_soup(self, url):
        """Get BeautifulSoup object for a URL with caching and rate limiting"""
        # Check cache first
        cached_content = self._get_cached_content(url)
        if cached_content:
            return BeautifulSoup(cached_content, 'html.parser')

        # Apply rate limiting
        self.rate_limiter.wait()

        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            # Cache the content
            self._cache_content(url, response.text)
            return soup
        except requests.RequestException as e:
            logging.error(f"Failed to fetch {url}: {str(e)}")
            return None

    def extract_endpoint_info(self, endpoint_section):
        """Extract information from an endpoint section"""
        try:
            # Get the endpoint title/heading
            title = endpoint_section.find('h2')
            if not title:
                return None
            
            endpoint_text = title.get_text().strip()
            
            # Extract HTTP method and path
            method = None
            path = None
            if ' ' in endpoint_text:
                method, path = endpoint_text.split(' ', 1)
            
            # Get description
            description = ""
            desc_elem = endpoint_section.find('p')
            if desc_elem:
                description = desc_elem.get_text().strip()
            
            # Get parameters
            parameters = []
            params_section = endpoint_section.find('h3', string=lambda x: x and 'Parameters' in x)
            if params_section:
                params_table = params_section.find_next('table')
                if params_table:
                    for row in params_table.find_all('tr')[1:]:  # Skip header row
                        cols = row.find_all('td')
                        if len(cols) >= 3:
                            param = {
                                'name': cols[0].get_text().strip(),
                                'type': cols[1].get_text().strip(),
                                'description': cols[2].get_text().strip()
                            }
                            parameters.append(param)
            
            # Get request body
            request_body = None
            body_section = endpoint_section.find('h3', string=lambda x: x and 'Request Body' in x)
            if body_section:
                body_content = body_section.find_next('pre')
                if body_content:
                    request_body = body_content.get_text().strip()
            
            # Get response
            response = None
            response_section = endpoint_section.find('h3', string=lambda x: x and 'Response' in x)
            if response_section:
                response_content = response_section.find_next('pre')
                if response_content:
                    response = response_content.get_text().strip()
            
            return {
                'method': method,
                'path': path,
                'description': description,
                'parameters': parameters,
                'request_body': request_body,
                'response': response
            }
        except Exception as e:
            logging.error(f"Error extracting endpoint info: {str(e)}")
            return None

    def scrape_endpoint(self, url):
        """Scrape a single endpoint page"""
        if url in self.visited_urls:
            return
        
        self.visited_urls.add(url)
        if self.progress_bar:
            self.progress_bar.update(1)
        
        soup = self.get_soup(url)
        if not soup:
            return
        
        # Find the main content section
        content = soup.find('div', class_='content')
        if not content:
            return
        
        # Extract endpoint information
        endpoint_info = self.extract_endpoint_info(content)
        if endpoint_info:
            endpoint_info['url'] = url
            self.api_docs.append(endpoint_info)
        
        # Find and follow links to other endpoints
        links = []
        for link in content.find_all('a', href=True):
            href = link['href']
            if href.startswith('/v2/'):
                next_url = urljoin(self.base_url, href)
                if next_url not in self.visited_urls:
                    links.append(next_url)
        
        return links

    def process_batch(self, urls):
        """Process a batch of URLs"""
        new_links = []
        for url in urls:
            links = self.scrape_endpoint(url)
            if links:
                new_links.extend(links)
        return new_links

    def scrape_all(self):
        """Start scraping from the main documentation page with parallel processing"""
        logging.info("Starting to scrape Mambu API documentation...")
        urls_to_process = [self.docs_url]
        total_processed = 0
        
        # Initialize progress bar
        self.progress_bar = tqdm(total=1000, desc="Scraping endpoints", unit="endpoint")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while urls_to_process:
                # Process URLs in batches
                current_batch = urls_to_process[:self.batch_size]
                urls_to_process = urls_to_process[self.batch_size:]
                
                # Submit batch for processing
                future_to_url = {
                    executor.submit(self.process_batch, [url]): url 
                    for url in current_batch
                }
                
                # Process completed batches
                for future in concurrent.futures.as_completed(future_to_url):
                    url = future_to_url[future]
                    try:
                        new_links = future.result()
                        if new_links:
                            urls_to_process.extend(new_links)
                    except Exception as e:
                        logging.error(f"Error processing {url}: {str(e)}")
        
        # Close progress bar
        if self.progress_bar:
            self.progress_bar.close()
        
        # Save the results
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = f'mambu_api_documentation_{timestamp}.json'
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(self.api_docs, f, indent=2, ensure_ascii=False)
        
        logging.info(f"\nScraping completed. Found {len(self.api_docs)} endpoints.")
        logging.info(f"Results saved to {output_file}")

def main():
    try:
        # Create scraper with 4 workers, batch size of 10, and 2 calls per second
        scraper = MambuAPIDocScraper(max_workers=4, batch_size=10, calls_per_second=2)
        scraper.scrape_all()
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    main() 