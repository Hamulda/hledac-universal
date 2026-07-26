"""F11C EvidenceLog wire patch — phase 2"""

import re

with open("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/runtime/sprint_scheduler.py") as f:
    content = f.read()

EVIDENCE_HOOK = """
                    # Sprint F11C: Wire EvidenceLog — observation event
                    if self._evidence_log is not None:
                        try:
                            self._evidence_log.create_event(
                                "observation",
                                finding.model_dump() if hasattr(finding, "model_dump") else vars(finding),
                                source_ids=[finding.source_id] if hasattr(finding, "source_id") else [],
                                confidence=getattr(finding, "confidence", 0.5),
                            )
                        except Exception:
                            pass
                    inner.append(finding)"""

EVIDENCE_HOOK_RENAMED = """
                    # Sprint F11C: Wire EvidenceLog — observation event
                    if self._evidence_log is not None:
                        try:
                            self._evidence_log.create_event(
                                "observation",
                                canonical.model_dump() if hasattr(canonical, "model_dump") else vars(canonical),
                                source_ids=[canonical.source_id] if hasattr(canonical, "source_id") else [],
                                confidence=getattr(canonical, "confidence", 0.5),
                            )
                        except Exception:
                            pass
                    inner.append(finding)"""

changes = 0

# Patch 2: digital ghost — from actual repr
p2 = re.compile(
    r'(finding = CanonicalFinding\(\n+                        source_type=SourceType\.DIGITAL_GHOST_DETECTION,\n+                        ioc_type="file",\n+                        ioc_value=getattr\(r, \'file_path\', ""\),\n+                        confidence=getattr\(r, \'overall_confidence\', 0\.5\),\n+                    \)\n+                    inner\.append\(find)',
    re.MULTILINE
)
m2 = p2.search(content)
if m2:
    old_block = m2.group(1)
    new_block = old_block.replace("inner.append(find", EVIDENCE_HOOK + "\n                    inner.append(find")
    content = content[:m2.start()] + new_block + content[m2.end():]
    changes += 1
    print("Patch 2 (digital ghost) applied")
else:
    print("Patch 2 (digital ghost) NOT found")

# Patch 4: BGP — actual pattern from repr above
p4 = re.compile(
    r'(finding = CanonicalFinding\(\n+                            finding_id=f"bgp-\{r\.prefix or r\.asn\}",\n+                            source_type=SourceType\.BGP_INTELLIGENCE,\n+                            confidence=0\.75,\n+                            query=self\._query\[:128\],\n+                            ts=_time\.time\(\),\n+                            payload_text=f"ASN=\{r\.asn\} org=\{r\.asn_name\} prefix=\{r\.prefix\} country=\{r\.country_code\}",\n+                          \)\n+                    inner\.append\(find)',
    re.MULTILINE
)
m4 = p4.search(content)
if m4:
    old_block = m4.group(1)
    new_block = old_block.replace("inner.append(find", EVIDENCE_HOOK + "\n                    inner.append(find")
    content = content[:m4.start()] + new_block + content[m4.end():]
    changes += 1
    print("Patch 4 (BGP) applied")
else:
    print("Patch 4 (BGP) NOT found")

# Patch 5: research layer — find the actual canonical pattern
idx_res = content.find('finding_id=f"research-{src}-{int(ts_float * 1000)}"')
if idx_res >= 0:
    print("Research context:")
    print(repr(content[idx_res-100:idx_res+500]))
else:
    print("Research NOT found")

print(f"\nTotal changes: {changes}")
with open("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/runtime/sprint_scheduler.py", "w") as f:
    f.write(content)
