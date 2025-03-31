# Mambu Scraper

A Python-based web scraper for Mambu platform data collection.

## Features

- Automated web scraping using Selenium
- BeautifulSoup for HTML parsing
- Logging functionality
- Chrome WebDriver management

## Prerequisites

- Python 3.8 or higher
- Chrome browser installed
- Git

## Installation

1. Clone the repository:
```bash
git clone https://github.com/orendi84/mambu_scraper.git
cd mambu_scraper
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

Run the scraper:
```bash
python mambu_scraper.py
```

## Configuration

The scraper can be configured by modifying the following:
- Logging settings in `mambu_scraper.py`
- Chrome options in the `setup_driver()` function
- Scraping logic in the `main()` function

## Logging

Logs are stored in `mambu_scraper.log` and also displayed in the console.

## License

MIT License

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request
