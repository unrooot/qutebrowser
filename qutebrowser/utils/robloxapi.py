"""Roblox API documentation fetcher and cache."""

import json
import urllib.request
import urllib.error
from typing import Dict, List, Optional
from dataclasses import dataclass
from pathlib import Path
import time
import threading
from collections import defaultdict

from qutebrowser.misc import sql
from qutebrowser.utils import log

VERSION_URL = "https://setup.rbxcdn.com/versionQTStudio"
DUMP_URL_TEMPLATE = "https://setup.rbxcdn.com/%s-API-Dump.json"
CACHE_DIR = Path.home() / ".cache" / "qutebrowser" / "robloxapi"
CACHE_FILE = CACHE_DIR / "api_dump.json"
DB_FILE = CACHE_DIR / "robloxapi.sqlite"
DB_MARKER_FILE = CACHE_DIR / "robloxapi.sqlite.marker"
CACHE_EXPIRY = 24 * 60 * 60  # 24 hours

@dataclass(frozen=True)  # Make immutable for caching
class ApiItem:
    """A single item in the Roblox API documentation."""
    name: str
    url: str
    description: str
    type: str  # "class", "property", "function", "event"

class ApiItems:
    """A collection of API items with optimized filtering."""
    
    def __init__(self, items: List[ApiItem]):
        self.all_items = items
        # Pre-group items by type for faster filtering
        self.by_type: Dict[str, List[ApiItem]] = {
            "class": [],
            "property": [],
            "function": [],
            "event": []
        }
        # Pre-build lowercase name index for faster searching
        self.name_index: Dict[str, List[ApiItem]] = defaultdict(list)
        
        for item in items:
            self.by_type[item.type].append(item)
            # Index by lowercase name for case-insensitive search
            self.name_index[item.name.lower()].append(item)
    
    def __iter__(self):
        """Make ApiItems iterable by iterating over all_items."""
        return iter(self.all_items)

# Global cache for parsed API items
_parsed_items_cache = None
_parsed_items_lock = threading.Lock()

def get_cached_api_dump() -> Optional[Dict]:
    """Get the cached API dump if it exists and is not expired."""
    if not CACHE_FILE.exists():
        log.completion.debug("No cache file found")
        return None
        
    if time.time() - CACHE_FILE.stat().st_mtime > CACHE_EXPIRY:
        log.completion.debug("Cache expired")
        return None
        
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log.completion.debug(f"Failed to load cache: {e}")
        return None

def cache_api_dump(dump: Dict) -> None:
    """Cache the API dump."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(dump, f)

def fetch_version() -> str:
    """Fetch the current Roblox Studio version."""
    try:
        with urllib.request.urlopen(VERSION_URL) as response:
            return response.read().decode().strip()
    except urllib.error.URLError as e:
        raise RuntimeError(f"Failed to fetch Roblox Studio version: {e}")

def fetch_api_dump(version: str) -> Dict:
    """Fetch the API dump for the given version."""
    url = DUMP_URL_TEMPLATE % version
    try:
        with urllib.request.urlopen(url) as response:
            return json.loads(response.read().decode())
    except urllib.error.URLError as e:
        raise RuntimeError(f"Failed to fetch API dump: {e}")

def parse_api_dump(dump: Dict) -> List[ApiItem]:
    """Parse the API dump into a list of ApiItem objects."""
    items = []
    
    # Debug: Print all class names and their superclasses
    log.completion.debug("Classes in API dump:")
    for class_data in dump.get("Classes", []):
        class_name = class_data["Name"]
        superclass = class_data.get("Superclass", "None")
        log.completion.debug(f"  {class_name} (superclass: {superclass})")
    
    # Process each class
    for class_data in dump.get("Classes", []):
        class_name = class_data["Name"]
        
        # Add class itself
        items.append(ApiItem(
            name=class_name,
            url=f"https://robloxapi.github.io/ref/class/{class_name}.html",
            description="",  # No description in the API dump
            type="class"
        ))
        
        # Process only direct members of this class
        for member_data in class_data.get("Members", []):
            member_name = member_data["Name"]
            member_type = member_data.get("MemberType", "member")
            
            # Skip deprecated members
            if "Deprecated" in member_data.get("Tags", []):
                continue
                
            # Create the item based on member type
            if member_type == "Property":
                items.append(ApiItem(
                    name=f"{class_name}.{member_name}",
                    url=f"https://robloxapi.github.io/ref/class/{class_name}.html#member-{member_name}",
                    description=member_data.get("Description", ""),
                    type="property"
                ))
            elif member_type == "Function":
                items.append(ApiItem(
                    name=f"{class_name}.{member_name}",
                    url=f"https://robloxapi.github.io/ref/class/{class_name}.html#member-{member_name}",
                    description=member_data.get("Description", ""),
                    type="function"
                ))
            elif member_type == "Event":
                items.append(ApiItem(
                    name=f"{class_name}.{member_name}",
                    url=f"https://robloxapi.github.io/ref/class/{class_name}.html#member-{member_name}",
                    description=member_data.get("Description", ""),
                    type="event"
                ))
    
    return items

def get_api_items() -> ApiItems:
    """Get the API items, either from cache or by fetching them."""
    global _parsed_items_cache
    
    # Check in-memory cache first
    if _parsed_items_cache is not None:
        return _parsed_items_cache
        
    with _parsed_items_lock:
        # Double-check after acquiring lock
        if _parsed_items_cache is not None:
            return _parsed_items_cache
            
        # Try to get from file cache
        cached_dump = get_cached_api_dump()
        if cached_dump:
            items = parse_api_dump(cached_dump)
            _parsed_items_cache = ApiItems(items)
            return _parsed_items_cache
            
        # Fetch new data
        version = fetch_version()
        dump = fetch_api_dump(version)
        cache_api_dump(dump)
        items = parse_api_dump(dump)
        _parsed_items_cache = ApiItems(items)
        return _parsed_items_cache


# Lazily-created SQLite store used by the completion model.
_db: Optional[sql.Database] = None
_table = None


def _get_table():
    """Return the (Database, SqlTable) for the Roblox API store, creating it once.

    Must be called from the GUI thread (Qt SQL is not thread-safe).
    """
    global _db, _table
    if _db is not None:
        return _db, _table

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _db = sql.Database(str(DB_FILE))
    _table = _db.table(
        "RobloxApi", ["name", "url", "type", "description"])
    # indexed lookups for the `type = ... AND name LIKE ...` completion queries
    _table.create_index("RobloxApiNameIdx", "name")
    _table.create_index("RobloxApiTypeIdx", "type")
    if _db.user_version_changed():
        _db.upgrade_user_version()
    return _db, _table


def _current_marker() -> str:
    """A marker that changes whenever the cached API dump is refreshed."""
    if CACHE_FILE.exists():
        return str(CACHE_FILE.stat().st_mtime_ns)
    return "0"


def build_sql_table():
    """Ensure the SQLite store is populated with the current API dump.

    Repopulates only when the table is empty or the cached dump changed, so it's
    cheap to call on every completion. Returns the (Database, SqlTable) pair.
    """
    db, table = _get_table()
    # get_api_items() refreshes CACHE_FILE if the 24h cache expired, so check the
    # marker afterwards to detect a new dump.
    items = get_api_items()
    marker = _current_marker()
    previous = (DB_MARKER_FILE.read_text(encoding="utf-8")
                if DB_MARKER_FILE.exists() else None)

    if len(table) == 0 or previous != marker:
        log.completion.debug("Rebuilding Roblox API SQL table")
        table.delete_all()
        table.insert_batch({
            "name": [item.name for item in items],
            "url": [item.url for item in items],
            "type": [item.type for item in items],
            "description": [item.description for item in items],
        })
        DB_MARKER_FILE.write_text(marker, encoding="utf-8")

    return db, table 