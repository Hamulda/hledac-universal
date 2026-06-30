# Stub for nodriver (undetectable Chrome automation).
# nodriver is a WebDriver-free Chrome automation library.
# It is lazy-imported and only used when HLEDAC_ENABLE_NODRIVER=1.

from typing import Any

# Module-level entry point
async def start(
    headless: bool = True,
    no_sandbox: bool = True,
    disable_dev_shem: bool = True,
    user_data_dir: str | None = None,
    **kwargs: Any,
) -> "Browser":
    """Launch a headless Chrome browser instance."""
    ...

# Alias
async def launch(
    headless: bool = True,
    no_sandbox: bool = True,
    disable_dev_shem: bool = True,
    user_data_dir: str | None = None,
    **kwargs: Any,
) -> "Browser":
    """Alias for start()."""
    ...

class Browser:
    """Headless Chrome browser instance."""
    async def __aenter__(self) -> Browser: ...
    async def __aexit__(self, *args: Any) -> None: ...
    async def close(self) -> None: ...
    async def new_page(self) -> Page: ...
    @property
    def version(self) -> str: ...

class Page:
    """A single tab/page in the browser."""
    url: str
    async def __aenter__(self) -> Page: ...
    async def __aexit__(self, *args: Any) -> None: ...
    async def close(self) -> None: ...
    async def evaluate(self, script: str) -> Any: ...
    async def get_content(self) -> str: ...
    async def wait_for_selector(self, selector: str, timeout: float = 30.0) -> Any: ...
    async def send_keys(self, keys: str) -> None: ...
    def find(self, selector: str) -> Element | None: ...
    def find_all(self, selector: str) -> list[Element]: ...

class Element:
    """DOM element within a page."""
    async def click(self) -> None: ...
    async def text(self) -> str: ...
    def attr(self, name: str) -> str | None: ...

async def start(
    headless: bool = True,
    no_sandbox: bool = True,
    disable_dev_shem: bool = True,
    user_data_dir: str | None = None,
    **kwargs: Any,
) -> Browser:
    """Launch a headless Chrome browser instance."""
    ...

# Alias
async def launch(
    headless: bool = True,
    no_sandbox: bool = True,
    disable_dev_shem: bool = True,
    user_data_dir: str | None = None,
    **kwargs: Any,
) -> Browser:
    """Alias for start()."""
    ...

# Alias for compatibility with code that imports uc
uc = Any  # type: ignore[misc]
