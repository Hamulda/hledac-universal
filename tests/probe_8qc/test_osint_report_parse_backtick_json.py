"""
Sprint 8QC D.3: Parse JSON in markdown backticks.
100% offline — no MLX, no network.
"""

import msgspec
from hledac.universal.brain.synthesis_runner import OSINTReport


class TestBacktickJSONParse:
    """D.3: "```json\\n{...}\\n```" → parse must succeed."""

    def test_parse_json_in_backticks(self):
        """JSON wrapped in ```json fences must parse successfully."""
        raw = '''```json
{
  "query": "ransomware lateral movement",
  "ioc_entities": [
    {"value": "2.2.2.2", "ioc_type": "ip", "severity": "high", "context": "C2 endpoint"}
  ],
  "threat_summary": "APT28 conducted lateral movement using Cobalt Strike.",
  "threat_actors": ["APT28", "Sandworm"],
  "confidence": 0.88,
  "sources_count": 4,
  "timestamp": 1743500000.5
}
```'''
        start = raw.find("{")
        end = raw.rfind("}") + 1
        assert start >= 0 and end > start
        clean = raw[start:end].strip().lstrip("`").strip()
        decoded = msgspec.json.decode(clean.encode(), type=OSINTReport)
        assert decoded.query == "ransomware lateral movement"
        assert decoded.threat_actors == ["APT28", "Sandworm"]
        assert decoded.ioc_entities[0].value == "2.2.2.2"

    def test_parse_leading_whitespace_in_json(self):
        """JSON with leading whitespace inside backticks must parse."""
        raw = '''```json

  {"query": "phishing", "ioc_entities": [], "threat_summary": "Phishing campaign.", "threat_actors": [], "confidence": 0.7, "sources_count": 2, "timestamp": 1743500001.0}

```'''
        start = raw.find("{")
        end = raw.rfind("}") + 1
        clean = raw[start:end].strip().lstrip("`").strip()
        decoded = msgspec.json.decode(clean.encode(), type=OSINTReport)
        assert decoded.query == "phishing"
        assert decoded.confidence == 0.7
