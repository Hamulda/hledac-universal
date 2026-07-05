# Issue #42: HTML Parsing — selectolax / lxml / BeautifulSoup Koordinace

## Problém
Nekoordinované použití třech HTML parserů napříč kódem:
- `selectolax` — Rust C backend, nejrychlejší (~3-5ms vs 7-15ms feedparser)
- `lxml` — C libxml2, rychlý, ale větší memory footprint
- `BeautifulSoup` — pure Python, nejpomalejší, používán zbytečně

## Jasná Politika

### Tier 1: selectolax (PRIMÁRNÍ — hot path)
**Použití:** Všude kde jde o rychlost a jednoduchý CSS selektor.

```python
from selectolax.parser import HTMLParser as _SelectolaxParser

tree = _SelectolaxParser(html)
for tag in tree.css("script, style"):
    tag.decompose()
text = tree.body.text(separator=" ", strip=True)
```

**Výhody na M1 8GB:**
- Rust C backend — rychlý, low memory
- ~3-5ms/fed vs bs4 ~15-30ms
- Zero-copy parsedown možný

### Tier 2: lxml (POUZE komplexní XPath)
**Použití:** Pouze když selectolax nestačí — např. komplexní XPath expressions, namespaces.

```python
from lxml import html as lxml_html

tree = lxml_html.fromstring(html)
# Pouze pro komplexní XPath! Ne CSS selektory!
elements = tree.xpath('//div[@class="content"]//span[contains(@class, "text")]')
```

**Kdy NE:**
- ❌ Jednoduché CSS selektory (selectolax stačí)
- ❌ `.text` extrakce (selectolax body.text() rychlejší)
- ❌ `find_all` s CSS (selectolax css() je rychlejší)

### Tier 3: BeautifulSoup (POSLEDNÍ FALLBACK)
**Použití:** Pouze když selhaly selectolax i lxml.

```python
from bs4 import BeautifulSoup

soup = BeautifulSoup(html, 'html.parser')  # NE 'lxml'!
# Pouze pro DOM traversal kde selectolax selhal
```

**Kdy NE:**
- ❌ `html_to_text` — `html_text_fast()` vždy první volba
- ❌ RSS/Atom feed parsing — `parsing/feed_parser.py` má vlastní selectolax implementaci
- ❌ Jednoduché extrakce — selectolax stačí

## Současný Stav (před opravou)

| Soubor | Problém | Akce |
|--------|---------|------|
| `intelligence/dark_web_intelligence.py` | 4× BeautifulSoup/lxml | Nahraď selectolax |
| `intelligence/archive_discovery.py` | bs4 fallback | Nahraď selectolax |
| `tools/content_miner.py` | lxml dostupný bez politiky | Dokumentuj Tier 2 |
| `coordinators/validation_coordinator.py` | bs4 pro markdown | Nahraď selectolax |
| `intelligence/stealth_crawler.py` | lxml import | Nahraď selectolax |

## Migrace Checklist

- [ ] `dark_web_intelligence.py` — `_parse_content()` → selectolax-first
- [ ] `archive_discovery.py` — bs4 fallback → selectolax
- [ ] `content_miner.py` — dokumentovat lxml pro XPath only
- [ ] `validation_coordinator.py` — markdown → selectolax
- [ ] `stealth_crawler.py` — lxml → selectolax

## Fail-Soft Pattern

Vždy používej try/except s fallback:

```python
if SELECTOLAX_AVAILABLE:
    try:
        tree = _SelectolaxParser(html)
        # ... selectolax path
    except Exception:
        # fallback na lxml nebo bs4
        pass

if LXML_AVAILABLE and COMPLEX_XPATH_NEEDED:
    try:
        # lxml XPath path
    except Exception:
        pass

# bs4 ultimate fallback
soup = BeautifulSoup(html, 'html.parser')
```

## M1 8GB Memory Budget

- selectolax: ~2-3 MB RSS (C backend)
- lxml: ~5-8 MB RSS (libxml2)
- BeautifulSoup: ~10-15 MB RSS (pure Python +比你慢)

=> **selectolax = vždy první volba na M1 8GB**
