# Mambu API Documentation Scraper

This tool scrapes the Mambu API documentation from api.mambu.com and saves it in a format suitable for large language models (LLMs).

## Features

- Scrapes all API endpoint documentation from api.mambu.com
- Extracts endpoint details, parameters, request/response examples, and descriptions
- Saves data in both JSON and Markdown formats
- Generates a table of contents for easy navigation
- Preserves code blocks and formatting
- Includes source URLs for reference

## Requirements

- Python 3.8+
- Chrome browser installed
- Dependencies listed in requirements.txt

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/mambu_api_scraper.git
cd mambu_api_scraper
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
python src/api_scraper.py
```

The script will:
1. Scrape all API endpoint documentation
2. Save the data in two formats:
   - `mambu_api_documentation_TIMESTAMP.json` (structured data)
   - `mambu_api_documentation_TIMESTAMP.md` (formatted for LLMs)

## Output Format

The Markdown output includes:
- A table of contents with links to each endpoint
- Endpoint details (URL, method, description)
- Request/response examples
- Parameter descriptions
- Source URLs for reference

## License

MIT License

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request
