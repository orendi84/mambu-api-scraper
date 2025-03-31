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

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('mambu_scraper.log'),
        logging.StreamHandler()
    ]
)

def setup_driver():
    chrome_options = Options()
    chrome_options.add_argument('--headless')  # Run in headless mode
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def clean_text(text):
    """Clean and format text content"""
    # Remove extra whitespace and newlines
    text = re.sub(r'\s+', ' ', text).strip()
    # Remove special characters but keep basic punctuation
    text = re.sub(r'[^\w\s.,!?-]', '', text)
    return text

def extract_page_content(driver, url):
    """Extract content from a single documentation page"""
    try:
        driver.get(url)
        time.sleep(2)  # Wait for dynamic content to load
        
        # Wait for the main content to load
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "article"))
        )
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Find the main article content
        article = soup.find('article')
        if not article:
            return None
        
        # Extract title
        title = soup.find('h1')
        title_text = clean_text(title.text) if title else "Untitled"
        
        # Extract content
        content_elements = article.find_all(['p', 'li', 'h2', 'h3', 'h4', 'pre', 'code'])
        content = []
        
        for element in content_elements:
            if element.name in ['h2', 'h3', 'h4']:
                content.append(f"\n## {clean_text(element.text)}\n")
            elif element.name in ['pre', 'code']:
                code_text = element.text.strip()
                if code_text:
                    content.append(f"\n```\n{code_text}\n```\n")
            else:
                text = clean_text(element.text)
                if text:
                    content.append(text)
        
        return {
            'url': url,
            'title': title_text,
            'content': '\n'.join(content)
        }
        
    except Exception as e:
        logging.error(f"Error extracting content from {url}: {str(e)}")
        return None

def get_all_doc_links(driver):
    """Get all documentation page links"""
    base_url = "https://support.mambu.com/docs"
    visited = set()
    to_visit = {base_url}
    all_links = set()
    
    try:
        while to_visit:
            current_url = to_visit.pop()
            if current_url in visited:
                continue
                
            logging.info(f"Getting links from: {current_url}")
            driver.get(current_url)
            time.sleep(2)  # Wait for dynamic content
            
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            
            # Find all links in the navigation and content
            links = soup.find_all('a', href=True)
            for link in links:
                href = link['href']
                full_url = urljoin(base_url, href)
                
                # Only include documentation pages
                if full_url.startswith(base_url) and '#' not in full_url:
                    all_links.add(full_url)
                    if full_url not in visited:
                        to_visit.add(full_url)
            
            visited.add(current_url)
            
    except Exception as e:
        logging.error(f"Error getting documentation links: {str(e)}")
    
    return all_links

def main():
    try:
        logging.info("Starting Mambu documentation scraper...")
        driver = setup_driver()
        
        # Get all documentation links
        doc_links = get_all_doc_links(driver)
        logging.info(f"Found {len(doc_links)} documentation pages to scrape")
        
        # Create the main content dictionary
        documentation = {
            'timestamp': datetime.now().isoformat(),
            'total_pages': len(doc_links),
            'pages': []
        }
        
        # Scrape each page
        for i, url in enumerate(doc_links, 1):
            logging.info(f"Scraping page {i}/{len(doc_links)}: {url}")
            page_content = extract_page_content(driver, url)
            if page_content:
                documentation['pages'].append(page_content)
        
        # Save the documentation
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'mambu_documentation_{timestamp}.json'
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(documentation, f, indent=2, ensure_ascii=False)
        
        # Create a markdown version for LLMs
        md_filename = f'mambu_documentation_{timestamp}.md'
        with open(md_filename, 'w', encoding='utf-8') as f:
            f.write("# Mambu Documentation\n\n")
            f.write("*Generated on: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + "*\n\n")
            f.write("## Table of Contents\n\n")
            
            # Generate table of contents
            for page in documentation['pages']:
                f.write(f"- [{page['title']}](#{page['title'].lower().replace(' ', '-')})\n")
            
            f.write("\n---\n\n")
            
            # Write each page's content
            for page in documentation['pages']:
                f.write(f"# {page['title']}\n")
                f.write(f"*Source: [{page['url']}]({page['url']})*\n\n")
                f.write(page['content'])
                f.write("\n\n---\n\n")
        
        logging.info(f"Documentation scraped successfully!")
        logging.info(f"JSON saved to: {filename}")
        logging.info(f"Markdown version saved to: {md_filename}")
        
        driver.quit()
        
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
        if 'driver' in locals():
            driver.quit()

if __name__ == "__main__":
    main() 