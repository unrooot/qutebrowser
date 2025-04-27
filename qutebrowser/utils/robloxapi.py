"""Roblox API documentation fetcher and cache."""

import json
import urllib.request
import urllib.error
from typing import Dict, List, Tuple, Optional, Set
from dataclasses import dataclass
from pathlib import Path
import time
from functools import lru_cache
import threading
from collections import defaultdict

from qutebrowser.utils import log

VERSION_URL = "https://setup.rbxcdn.com/versionQTStudio"
DUMP_URL_TEMPLATE = "https://setup.rbxcdn.com/%s-API-Dump.json"
CACHE_DIR = Path.home() / ".cache" / "qutebrowser" / "robloxapi"
CACHE_FILE = CACHE_DIR / "api_dump.json"
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
    
    def _fuzzy_match(self, text: str, target: str) -> Tuple[bool, float]:
        """Fuzzy match text against target string.
        
        Returns:
            Tuple of (matched, score) where:
            - matched: True if text is found in target
            - score: Higher score means better match (closer to start, more consecutive chars)
        """
        text = text.lower()
        target = target.lower()
        
        if not text:
            return True, 0.0
            
        if text in target:
            # Exact substring match - score based on position
            pos = target.find(text)
            return True, 1.0 - (pos / len(target))
            
        # Check for partial matches
        text_idx = 0
        target_idx = 0
        consecutive = 0
        max_consecutive = 0
        first_match_pos = -1
        
        while text_idx < len(text) and target_idx < len(target):
            if text[text_idx] == target[target_idx]:
                if first_match_pos == -1:
                    first_match_pos = target_idx
                consecutive += 1
                max_consecutive = max(max_consecutive, consecutive)
                text_idx += 1
            else:
                consecutive = 0
            target_idx += 1
            
        if text_idx == len(text):
            # All characters matched
            score = 0.5  # Base score for partial match
            if first_match_pos != -1:
                score += 0.3 * (1.0 - (first_match_pos / len(target)))  # Position bonus
            score += 0.2 * (max_consecutive / len(text))  # Consecutive bonus
            return True, score
            
        return False, 0.0
    
    @lru_cache(maxsize=100)  # Cache filtered results
    def filter(self, text: str, types: Optional[Set[str]] = None) -> List[ApiItem]:
        """Filter items by text and optionally by type using fuzzy matching."""
        if not text:
            return self.all_items
            
        # Get items to search through
        if types:
            items = []
            for type_ in types:
                items.extend(self.by_type[type_])
        else:
            items = self.all_items
            
        # Score and filter items
        scored_items = []
        for item in items:
            matched, score = self._fuzzy_match(text, item.name)
            if matched:
                scored_items.append((score, item))
                
        # Sort by score (highest first)
        scored_items.sort(reverse=True, key=lambda x: x[0])
        return [item for _, item in scored_items]

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