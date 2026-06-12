import logging
import re
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Module-level: stream-only logger so functions work without setup_logging().
# setup_logging() in main() adds the file handler.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

DEFAULT_TENANT = "demotenant.dev.mambucloud.com"
STREAMING_API_URL = "https://api.mambu.com/streaming-api/mambu-streaming-api-spec-oas3.json"
MAX_FAILURE_RATIO = 0.10

_LOG_FILENAME = "mambu_openapi_fetcher.log"


def setup_logging(output_dir):
    """Add a file handler that writes into output_dir. Called from main()."""
    log_path = Path(output_dir) / _LOG_FILENAME
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logging.getLogger().addHandler(file_handler)
    log.info(f"Log file: {log_path}")


def parse_auth(auth_str):
    """Parse 'user:password' into (user, password). Raises ValueError if no colon."""
    if ":" not in auth_str:
        raise ValueError(
            "--auth / MAMBU_AUTH must be in user:password format; got no colon in provided value"
        )
    user, password = auth_str.split(":", 1)
    return (user, password)


def build_session(auth_tuple=None):
    """Create a requests session with retry and optional basic auth.

    auth_tuple: (user, password) or None.
    """
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.headers["User-Agent"] = "mambu-openapi-fetcher/1.0"
    if auth_tuple:
        session.auth = auth_tuple
    return session


def resolve_ref(spec, ref):
    """Resolve a $ref JSON pointer within the spec.

    Handles JSON-pointer escapes: ~1 -> /, ~0 -> ~ (in that order).
    Returns None if the ref is missing, invalid, or the target cannot be reached.
    Never raises.
    """
    if not ref or not ref.startswith("#/"):
        return None
    parts = ref[2:].split("/")
    obj = spec
    for part in parts:
        # Unescape JSON Pointer encoding (RFC 6901): ~1 first, then ~0
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(obj, list):
            try:
                idx = int(part)
                obj = obj[idx]
            except (ValueError, IndexError):
                return None
        elif isinstance(obj, dict):
            obj = obj.get(part)
            if obj is None:
                return None
        else:
            return None
    return obj if obj is not None else None


def github_anchor(label, seen_counts):
    """Produce a GitHub-style heading anchor slug from label.

    Lowercases, strips characters that are not alphanumeric/space/hyphen,
    replaces spaces with hyphens. Deduplicates by appending -1, -2, ...
    on second and subsequent occurrences (GitHub convention).

    seen_counts is a dict mutated in place to track usage counts.
    """
    slug = label.lower()
    slug = re.sub(r"[^\w\s-]", "", slug, flags=re.UNICODE)
    slug = re.sub(r"\s+", "-", slug)
    # \w keeps digits and underscores, which GitHub also keeps in anchors
    slug = slug.strip("-")

    base = slug
    count = seen_counts.get(base, 0)
    seen_counts[base] = count + 1
    if count == 0:
        return base
    return f"{base}-{count}"


def slugify_filename(label, seen_counts):
    """Produce a filesystem-safe slug from label for split .md filenames.

    Lowercases, replaces non-alphanumeric characters with hyphens,
    collapses runs of hyphens, strips leading/trailing hyphens.
    Deduplicates collisions with -1, -2 suffixes (first occurrence keeps no suffix).

    seen_counts is a dict mutated in place to track usage counts.
    """
    slug = label.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    # Collapse any internal runs (the regex above already collapses, but be safe)
    slug = re.sub(r"-+", "-", slug)
    if not slug:
        slug = "resource"

    base = slug
    count = seen_counts.get(base, 0)
    seen_counts[base] = count + 1
    if count == 0:
        return base
    return f"{base}-{count}"


def md_cell(value):
    """Coerce value to str and make it safe for a markdown table cell.

    Replaces newlines with spaces and escapes pipe characters as \\|.
    """
    s = str(value)
    s = s.replace("\n", " ")
    s = s.replace("|", "\\|")
    return s
