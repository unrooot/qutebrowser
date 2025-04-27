"""Roblox API documentation commands."""

from qutebrowser.api import cmdutils
from qutebrowser.utils import robloxapi, message, objreg
from qutebrowser.completion.models import miscmodels
from qutebrowser.qt.core import QUrl


@cmdutils.register(name='robloxapi', maxsplit=0)
@cmdutils.argument('pattern', completion=miscmodels.robloxapi)
def robloxapi_command(pattern: str, *, info=None, tab: bool = False) -> None:
    """Open Roblox API documentation.
    
    Args:
        pattern: The pattern to search for in the API documentation.
        info: The command info object containing window ID and other context.
        tab: Open in a new tab.
    """
    try:
        items = robloxapi.get_api_items()
        for item in items:
            if item.name == pattern:
                if info is None:
                    # If no info provided, get the first window
                    windows = list(objreg.window_registry)
                    if not windows:
                        message.error("No windows available")
                        return
                    win_id = windows[0]
                else:
                    win_id = info.win_id
                
                # Get the tabbed browser for the window
                tabbed_browser = objreg.get('tabbed-browser', scope='window', window=win_id)
                
                if tab:
                    # Open URL in a new tab
                    tabbed_browser.tabopen(QUrl(item.url))
                else:
                    # Open URL in current tab
                    tabbed_browser.load_url(QUrl(item.url), newtab=False)
                return
                
        message.error(f"No matching API item found: {pattern}")
    except Exception as e:
        message.error(f"Failed to open Roblox API documentation: {e}") 