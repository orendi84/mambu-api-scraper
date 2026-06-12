import concurrent.futures
import json
import logging
import re
import threading

from .common import STREAMING_API_URL, build_session

log = logging.getLogger(__name__)


def fetch_resource_index(session, base_url):
    """Fetch the list of all API resources from Mambu's discovery endpoint."""
    url = f"{base_url}/api/swagger/resources"
    log.info(f"Fetching resource index from {url}")
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    # Response is {"items": [...]} on some tenants, plain list on others.
    if isinstance(data, dict):
        if "items" not in data:
            raise RuntimeError(
                f"Unexpected resource index shape: dict with no 'items' key. Keys: {list(data.keys())}"
            )
        resources = data["items"]
    else:
        resources = data
    if not isinstance(resources, list):
        raise RuntimeError(
            f"Unexpected resource index shape: expected list, got {type(resources).__name__}"
        )
    log.info(f"Found {len(resources)} API resources")
    return resources


_OPENAPI_PATH_RE = re.compile(r"^json/([^/]+)_v(\d+)_swagger\.json$")


def derive_openapi_path(json_path):
    """Derive the unauthenticated openapi path from the discovery jsonPath.

    Example: json/clients_v2_swagger.json -> openapi/resources/clients/v2

    Returns None if json_path does not match the expected pattern.
    """
    m = _OPENAPI_PATH_RE.match(json_path)
    if not m:
        return None
    name, version = m.group(1), m.group(2)
    return f"openapi/resources/{name}/v{version}"


# swagger-core serialization internals that Mambu's endpoints leak into
# schema objects. They are not valid OpenAPI 3.0 and break validators.
# jsonSchema is a full duplicate of the schema embedded by swagger-core.
_SWAGGER_ARTIFACT_KEYS = ("exampleSetFlag", "specVersion", "jsonSchema")


def strip_swagger_artifacts(node):
    """Remove swagger-core internal keys leaked into spec JSON, in place.

    Drops 'exampleSetFlag' and 'specVersion' everywhere; drops 'types' when
    it is redundant with a sibling 'type', or promotes a single-element
    'types' list to 'type' when 'type' is absent. Property NAMES are never
    touched: inside a 'properties' map only the property values are walked.
    """
    if isinstance(node, dict):
        for key in _SWAGGER_ARTIFACT_KEYS:
            node.pop(key, None)
        types = node.get("types")
        if isinstance(types, list):
            if "type" in node:
                node.pop("types", None)
            elif len(types) == 1 and isinstance(types[0], str):
                node["type"] = types[0]
                node.pop("types", None)
        # Some Mambu schemas carry duplicated enum values, which violates
        # the spec's uniqueItems; dedupe preserving first-seen order.
        enum = node.get("enum")
        if isinstance(enum, list) and len(enum) > 1:
            seen = set()
            deduped = []
            for v in enum:
                marker = json.dumps(v, sort_keys=True, ensure_ascii=False)
                if marker not in seen:
                    seen.add(marker)
                    deduped.append(v)
            if len(deduped) != len(enum):
                node["enum"] = deduped
        # swagger-core wraps vendor extensions in a literal 'extensions'
        # object instead of inlining x- keys; hoist them to the parent.
        ext = node.get("extensions")
        if isinstance(ext, dict) and all(isinstance(k, str) and k.startswith("x-") for k in ext):
            for k, v in ext.items():
                node.setdefault(k, v)
            node.pop("extensions", None)
        for key, value in node.items():
            # example/default payloads and x- extension values are opaque
            # user data, not schema objects; never recurse into or mutate
            # them even if they contain keys that look like leakage.
            if key in ("example", "examples", "default") or key.startswith("x-"):
                continue
            if key == "responses" and isinstance(value, dict):
                # 'default' inside a responses map is a response object
                # (status-code alias), not a default value; process all
                # members directly so the per-key 'default' skip above
                # does not exempt it.
                for resp in value.values():
                    strip_swagger_artifacts(resp)
                continue
            if key in ("properties", "schemas") and isinstance(value, dict):
                # swagger-core copies the property/schema name into the
                # object as 'name'; drop it only when it provably mirrors
                # the key (parameters keep theirs). Map keys themselves
                # are never touched.
                for sub_key, sub_value in value.items():
                    if isinstance(sub_value, dict) and sub_value.get("name") == sub_key:
                        sub_value.pop("name", None)
                    strip_swagger_artifacts(sub_value)
            else:
                strip_swagger_artifacts(value)
    elif isinstance(node, list):
        for item in node:
            strip_swagger_artifacts(item)
    return node


def fetch_spec(session, base_url, json_path, label):
    """Fetch a single OpenAPI spec via the unauthenticated openapi path.

    Falls back to the authenticated jsonPath if the openapi path returns 404
    or if derive_openapi_path cannot produce a valid path.
    Fetched specs are normalized via strip_swagger_artifacts.
    """
    # Try unauthenticated openapi path first
    openapi_path = derive_openapi_path(json_path)
    if openapi_path is None:
        log.debug(f"Cannot derive openapi path for {label} (jsonPath={json_path!r}); skipping openapi attempt")
    else:
        url = f"{base_url}/api/{openapi_path}"
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200:
                spec = resp.json()
                if "openapi" in spec or "swagger" in spec:
                    return strip_swagger_artifacts(spec)
        except Exception as e:
            log.debug(f"openapi path attempt failed for {url}: {e}")

    # Fall back to the jsonPath directly (may require auth)
    url = f"{base_url}/api/{json_path}"
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code == 200:
            spec = resp.json()
            if "openapi" in spec or "swagger" in spec:
                return strip_swagger_artifacts(spec)
    except Exception as e:
        log.debug(f"jsonPath fallback failed for {url}: {e}")

    log.warning(f"Failed to fetch spec for {label} from both paths")
    return None


def fetch_streaming_api(session):
    """Fetch the Streaming API spec from the static URL."""
    log.info(f"Fetching Streaming API spec from {STREAMING_API_URL}")
    try:
        resp = session.get(STREAMING_API_URL, timeout=30)
        resp.raise_for_status()
        spec = resp.json()
        log.info(f"Streaming API: {len(spec.get('paths', {}))} endpoints")
        return strip_swagger_artifacts(spec)
    except Exception as e:
        log.warning(f"Failed to fetch Streaming API: {e}")
        return None


def count_endpoints(spec):
    """Count total endpoints (path + method combinations) in a spec."""
    count = 0
    for path, methods in spec.get("paths", {}).items():
        if not isinstance(methods, dict):
            continue
        for method, details in methods.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete", "head", "options"):
                continue
            if not isinstance(details, dict):
                continue
            count += 1
    return count


def filter_resources(resources, csv_filter):
    """Filter resources list by comma-separated case-insensitive substring matches against labels.

    Returns (matched, available_labels).
    matched is the filtered list (preserving original order).
    available_labels is the list of all labels before filtering.
    """
    available_labels = [r.get("label", "") for r in resources]
    filters = [f.strip().lower() for f in csv_filter.split(",") if f.strip()]
    matched = [
        r for r in resources
        if any(f in r.get("label", "").lower() for f in filters)
    ]
    return matched, available_labels


def load_saved_output(path):
    """Load a previously saved output JSON file.

    Returns the parsed dict. Raises ValueError if required fields are missing.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object at top level, got {type(data).__name__}")
    if "resources" not in data:
        raise ValueError("Saved JSON missing 'resources' field")
    if not isinstance(data["resources"], list):
        raise ValueError("'resources' field must be a list")
    # Snapshots taken before artifact stripping existed may carry leaked
    # swagger-core internals; normalize on load so replay/diff/scorecard
    # see the same shape as freshly fetched specs.
    strip_swagger_artifacts(data["resources"])
    return data


def fetch_all_specs(resources, base_url, auth_tuple, workers):
    """Fetch all specs concurrently, returning (specs, failures, total_endpoints).

    Preserves original resource ordering. Uses threading.local() for per-thread sessions.
    specs is a list of dicts with label/json_path/endpoints/spec keys.
    failures is the count of failed/skipped resources.
    total_endpoints is the sum of endpoints across all fetched specs.
    """
    _thread_local = threading.local()

    def get_thread_session():
        if not hasattr(_thread_local, "session"):
            _thread_local.session = build_session(auth_tuple)
        return _thread_local.session

    def fetch_worker(args_tuple):
        """Fetch one spec; returns (index, label, json_path, spec_or_None)."""
        index, label, json_path = args_tuple
        if not json_path:
            return (index, label, json_path, None, "no_jsonpath")
        sess = get_thread_session()
        spec = fetch_spec(sess, base_url, json_path, label)
        return (index, label, json_path, spec, "ok")

    # Build work items - no-jsonPath resources still submitted and counted as failures
    work_items = []
    for i, resource in enumerate(resources):
        label = resource.get("label", f"Resource {i + 1}")
        json_path = resource.get("jsonPath", "")
        work_items.append((i, label, json_path))

    # denominator = all attempted resources (including no-jsonPath skips)
    resources_attempted = len(work_items)

    results = [None] * resources_attempted  # preserve original order

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_item = {executor.submit(fetch_worker, item): item for item in work_items}
        for future in concurrent.futures.as_completed(future_to_item):
            orig_index, label, json_path = future_to_item[future]
            try:
                orig_index, label, json_path, spec, status = future.result()
            except Exception as e:
                log.warning(f"Worker crashed fetching {label}: {e}")
                spec, status = None, "error"
            results[orig_index] = (label, json_path, spec, status)

    # Aggregate in original resource order (main thread only)
    failures = 0
    total_endpoints = 0
    specs = []

    for i, (label, json_path, spec, status) in enumerate(results):
        resource_num = i + 1
        if status == "no_jsonpath":
            log.warning(f"[{resource_num}/{resources_attempted}] Skipping {label}: no jsonPath found")
            failures += 1
        elif spec is not None:
            ep_count = count_endpoints(spec)
            total_endpoints += ep_count
            specs.append({
                "label": label,
                "json_path": json_path,
                "endpoints": ep_count,
                "spec": spec,
            })
            log.info(f"[{resource_num}/{resources_attempted}] {label} -> {ep_count} endpoints")
        else:
            failures += 1
            log.warning(f"[{resource_num}/{resources_attempted}] {label} -> FAILED")

    return specs, failures, total_endpoints
