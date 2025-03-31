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
    # chrome_options.add_argument('--headless')  # Uncomment to run in headless mode
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def main():
    try:
        logging.info("Starting Mambu scraper...")
        driver = setup_driver()
        
        # Add your scraping logic here
        
        driver.quit()
        logging.info("Scraping completed successfully")
        
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
        if 'driver' in locals():
            driver.quit()

if __name__ == "__main__":
    main() 