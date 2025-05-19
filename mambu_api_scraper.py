print("DEBUG: mambu_api_scraper.py - Script Start") # DEBUG Line

#!/usr/bin/env python3
"""
Mambu API Documentation Scraper

This script is specifically designed to scrape the Mambu API documentation
from their single-page scrollable documentation interface. It extracts all
sections and code examples.
"""

import os
import logging
import argparse
from datetime import datetime
import website_scraper
import sys
import time

# Constants specific to Mambu API docs
MAMBU_API_URL = "https://api.mambu.com"
DEFAULT_OUTPUT_DIR = "./mambu_api_output"

# This will hold the parsed arguments, to be accessible by enhance_for_mambu_api
# We are making it global here to simplify, but in a larger app, passing it around would be cleaner.
parsed_args_global = None

def parse_arguments():
    """Parse command line arguments specific to Mambu API scraping."""
    parser = argparse.ArgumentParser(description="Scrape Mambu API documentation.")
    
    # Scraping parameters
    parser.add_argument("--api_version", default=os.environ.get('API_VERSION', "v2"), 
                        choices=["v1", "v2", "payments", "streaming"], 
                        help="Mambu API version to scrape")
    parser.add_argument("--output_dir", default=os.environ.get('OUTPUT_DIR', DEFAULT_OUTPUT_DIR), 
                        help="Directory to save scraped data")
    parser.add_argument("--log_level", default=os.environ.get('LOG_LEVEL', "INFO"), 
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], 
                        help="Logging level")
    parser.add_argument("--language", default=os.environ.get('LANGUAGE', "all"),
                        choices=["all", "curl", "http", "javascript", "ruby", "python", "java", "go", "php"],
                        help="Programming language for code examples (default: all)")
    
    # Google Drive parameters
    parser.add_argument("--target_folder_id", 
                        default=os.environ.get('GOOGLE_DRIVE_TARGET_FOLDER_ID'), 
                        help="Google Drive folder ID to upload the final Markdown file")
    parser.add_argument("--archive_folder_id", 
                        default=os.environ.get('GOOGLE_DRIVE_ARCHIVE_FOLDER_ID'), 
                        help="Google Drive folder ID to archive previous versions")
    
    return parser.parse_args()

def get_api_url(api_version):
    """Get the appropriate URL for the specified API version."""
    if api_version == "v2":
        return f"{MAMBU_API_URL}/#/v2"
    elif api_version == "v1":
        return f"{MAMBU_API_URL}/#/v1"
    elif api_version == "payments":
        return f"{MAMBU_API_URL}/#/payments"
    elif api_version == "streaming":
        return f"{MAMBU_API_URL}/#/streaming"
    else:
        # Fallback for any other value, assuming it's a direct path segment
        return f"{MAMBU_API_URL}/#/{api_version}"

def enhance_for_mambu_api(driver, language_arg_from_main):
    """Apply Mambu API-specific enhancements to the extraction process."""
    logging.info("Applying Mambu API-specific enhancements")
    
    try:
        effective_language_arg = language_arg_from_main.lower()
        if effective_language_arg != "all":
            logging.info(f"Setting preferred code language to: {effective_language_arg}")
            language_selectors = {
                "curl": "a[href*='cURL'], li > span:contains('cURL')", # Adjusted selectors
                "http": "a[href*='HTTP'], li > span:contains('HTTP')",
                "javascript": "a[href*='JavaScript'], li > span:contains('JavaScript')",
                "ruby": "a[href*='Ruby'], li > span:contains('Ruby')",
                "python": "a[href*='Python'], li > span:contains('Python')",
                "java": "a[href*='Java']:not([href*='JavaScript']), li > span:contains('Java'):not(:contains('JavaScript'))",
                "go": "a[href*='Go'], li > span:contains('Go')",
                "php": "a[href*='PHP'], li > span:contains('PHP')"
            }
            
            if effective_language_arg in language_selectors:
                selector_query = language_selectors[effective_language_arg]
                try:
                    tabs = driver.find_elements(website_scraper.By.CSS_SELECTOR, selector_query)
                    clicked = False
                    for tab in tabs:
                        if tab.is_displayed() and tab.is_enabled():
                            logging.info(f"Attempting to click language tab: {tab.text}")
                            driver.execute_script("arguments[0].click();", tab)
                            time.sleep(0.5) # Give it a moment to react
                            logging.info(f"Selected {effective_language_arg} language tab.")
                            clicked = True
                            break
                    if not clicked:
                        logging.warning(f"Language tab for '{effective_language_arg}' not found or not clickable with selector '{selector_query}'.")
                except Exception as e:
                    logging.warning(f"Could not set language preference to {effective_language_arg}: {e}")
            else:
                logging.warning(f"Unsupported language for selection: {effective_language_arg}")
    except Exception as e:
        logging.error(f"Error in Mambu API-specific enhancements: {e}")

def main():
    """Main function to run the Mambu API scraper."""
    global parsed_args_global # To make args accessible to the hook function via a global
    parsed_args_global = parse_arguments()
    
    website_scraper.setup_logging(parsed_args_global.log_level)
    logging.info(f"Starting Mambu API documentation scraper for {parsed_args_global.api_version}")
    print(f"DEBUG: mambu_api_scraper.py - Log level set to: {parsed_args_global.log_level}") # DEBUG

    os.makedirs(parsed_args_global.output_dir, exist_ok=True)
    
    api_url = get_api_url(parsed_args_global.api_version)
    logging.info(f"Scraping Mambu API documentation from: {api_url}")
    print(f"DEBUG: mambu_api_scraper.py - Target URL: {api_url}") # DEBUG

    scraper_args_for_website_scraper = argparse.Namespace(
        start_url=api_url,
        output_dir=parsed_args_global.output_dir,
        log_level=parsed_args_global.log_level,
        target_folder_id=parsed_args_global.target_folder_id,
        archive_folder_id=parsed_args_global.archive_folder_id,
        use_fallback_urls=False, # Mambu API is single page, no link discovery needed beyond sections
        headless=True, # Default to headless for automation
        upload_only_file=None, # Not used in this specialized script's main flow
        config_file=None,
        max_pages=1, # Effectively, as it's a single page structure
        delay_between_pages=parsed_args_global.delay_between_pages if hasattr(parsed_args_global, 'delay_between_pages') else 1.0
    )

    # Monkey-patch or hook into website_scraper's content extraction if needed,
    # or ensure website_scraper's extract_page_content is suitable.
    # For Mambu API, we might need a specific extraction logic for its unique structure.

    original_extract_page_content = website_scraper.extract_page_content

    def mambu_enhanced_extract_page_content(driver, url):
        print("DEBUG: mambu_api_scraper.py - Entering mambu_enhanced_extract_page_content") # DEBUG
        enhance_for_mambu_api(driver, parsed_args_global.language)
        # Now, call the original (or a more specialized one if website_scraper.py has it)
        # This assumes website_scraper.extract_page_content is designed for single-page apps
        # or can be adapted via its internal logic (e.g. by identifying sections)
        print("DEBUG: mambu_api_scraper.py - Calling original_extract_page_content") # DEBUG
        result = original_extract_page_content(driver, url)
        print(f"DEBUG: mambu_api_scraper.py - Exiting mambu_enhanced_extract_page_content, result sections: {len(result) if isinstance(result, list) else 'N/A'}") # DEBUG
        return result

    website_scraper.extract_page_content = mambu_enhanced_extract_page_content
    
    output_file = None
    try:
        print("DEBUG: mambu_api_scraper.py - Calling website_scraper.main()") # DEBUG
        output_file = website_scraper.main(scraper_args_for_website_scraper)
        print(f"DEBUG: mambu_api_scraper.py - website_scraper.main() returned: {output_file}") # DEBUG
        
        if output_file and os.path.exists(output_file):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            new_filename_base = f"mambu_api_{parsed_args_global.api_version}_docs_{parsed_args_global.language}_{timestamp}.md"
            new_filename = os.path.join(parsed_args_global.output_dir, new_filename_base)
            try:
                os.rename(output_file, new_filename)
                logging.info(f"Renamed output file to: {new_filename}")
                output_file = new_filename # update path to new name
            except Exception as e:
                logging.error(f"Error renaming output file '{output_file}' to '{new_filename}': {e}")
        
    except Exception as e:
        logging.critical(f"Error running Mambu API scraper's main logic: {e}", exc_info=True)
        print(f"DEBUG: mambu_api_scraper.py - Exception in main logic: {e}") # DEBUG
    finally:
        website_scraper.extract_page_content = original_extract_page_content # Restore
        print("DEBUG: mambu_api_scraper.py - Restored original_extract_page_content") # DEBUG

    return output_file

if __name__ == "__main__":
    print("DEBUG: mambu_api_scraper.py - Inside __main__ block") # DEBUG Line
    # Need to ensure website_scraper also has its By imported if used by enhance_for_mambu_api directly
    # For simplicity, ensure website_scraper exposes By or handle imports carefully.
    # A quick check for `time` module for `time.sleep` if used in enhance_for_mambu_api:
    if 'time' not in sys.modules:
        import time 

    final_output_path = main()
    if final_output_path:
        logging.info(f"Mambu API documentation scraping completed. Output: {final_output_path}")
        print(f"DEBUG: mambu_api_scraper.py - Success, output: {final_output_path}") # DEBUG Line
    else:
        logging.error("Mambu API documentation scraping failed or produced no output file.")
        print("DEBUG: mambu_api_scraper.py - Failed or no output file") # DEBUG Line

# Ensure all necessary imports are at the top, like `import sys` if using `sys.modules`.
# It might be better to add `import time` and `import sys` at the top of the file. 