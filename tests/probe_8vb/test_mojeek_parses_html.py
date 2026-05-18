import pytest

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    pytest.skip("beautifulsoup4 not installed (legacy-html extra required)", allow_module_level=True)

def test_mojeek_parses_html():
    html = ('<ul class="results-standard">'
            '<li><a class="ob" href="http://t.com">T</a>'
            '<p class="s">Snip</p></li></ul>')
    soup = BeautifulSoup(html, "html.parser")
    a = soup.select_one("ul.results-standard li a.ob")
    assert a["href"] == "http://t.com"
