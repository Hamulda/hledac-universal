"""
Wrapper pro externí OSINT nástroje (theHarvester, Sherlock, Maigret).
Sprint 46: Access to Unreachable Data (Sessions + Paywall + OSINT + Darknet)
"""

import asyncio
import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


class OSINTFrameworkRunner:
    """Runner pro externí OSINT nástroje."""

    def __init__(self):
        self._timeout = 30

    async def run_theharvester(self, target: str) -> list[dict]:
        """Spustí theHarvester na doménu/jméno."""
        # Check if theHarvester is available
        try:
            proc_check = await asyncio.create_subprocess_exec(
                'theHarvester', '--help',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(proc_check.communicate(), timeout=5)
        except (TimeoutError, FileNotFoundError):
            logger.debug("[theHarvester] Not installed, skipping")
            return []

        with tempfile.NamedTemporaryFile(suffix='', delete=False, dir=tempfile.gettempdir()) as f:
            out_file = f.name

        try:
            proc = await asyncio.create_subprocess_exec(
                'theHarvester', '-d', target, '-b', 'all', '-f', out_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)

            findings = []
            # Try to parse JSON output
            for ext in ['.json', '.xml']:
                try:
                    file_path = out_file + ext
                    if os.path.exists(file_path):
                        with open(file_path) as f:
                            data = json.load(f)
                        # Handle different output formats
                        if isinstance(data, dict):
                            for email in data.get('emails', []):
                                findings.append({
                                    'type': 'email',
                                    'value': email if isinstance(email, str) else email.get('email', str(email)),
                                    'source': 'theHarvester'
                                })
                            for host in data.get('hosts', []):
                                findings.append({
                                    'type': 'host',
                                    'value': host if isinstance(host, str) else host.get('host', str(host)),
                                    'source': 'theHarvester'
                                })
                        break
                except Exception as e:
                    logger.debug(f"[theHarvester] Parse error: {e}")
                    continue

            return findings
        except TimeoutError:
            logger.warning(f"[theHarvester] Timeout for {target}")
            return []
        except Exception as e:
            logger.warning(f"[theHarvester] Failed: {e}")
            return []
        finally:
            # Cleanup temp files
            for ext in ['', '.json', '.xml']:
                try:
                    if os.path.exists(out_file + ext):
                        os.unlink(out_file + ext)
                except FileNotFoundError:
                    pass

    async def run_sherlock(self, username: str) -> list[dict]:
        """Spustí Sherlock na username s --json flagem pro strukturální výstup."""
        # Check if sherlock is available
        try:
            proc_check = await asyncio.create_subprocess_exec(
                'sherlock', '--help',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(proc_check.communicate(), timeout=5)
        except (TimeoutError, FileNotFoundError):
            logger.debug("[Sherlock] Not installed, skipping")
            return []

        try:
            # Sprint 47: Use --json for structured output from stdout
            proc = await asyncio.create_subprocess_exec(
                'sherlock', username, '--nsfw', '--timeout', '5', '--json',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)

            findings = []
            # Sprint 47: Parse JSON from stdout instead of text parsing
            try:
                data = json.loads(stdout.decode(errors='ignore'))
                for site, info in data.items():
                    if isinstance(info, dict) and info.get('url'):
                        findings.append({
                            'type': 'profile',
                            'url': info['url'],
                            'site': site,
                            'source': 'sherlock'
                        })
            except json.JSONDecodeError:
                # Fallback to text parsing if JSON fails
                for line in stdout.decode(errors='ignore').split('\n'):
                    if '[+]' in line:
                        parts = line.split()
                        if len(parts) > 1:
                            url = parts[1] if parts[1].startswith('http') else parts[0]
                            findings.append({
                                'type': 'profile',
                                'url': url,
                                'source': 'sherlock'
                            })

            return findings
        except TimeoutError:
            logger.warning(f"[Sherlock] Timeout for {username}")
            return []
        except Exception as e:
            logger.warning(f"[Sherlock] Failed: {e}")
            return []

    async def run_maigret(self, username: str) -> list[dict]:
        """Spustí Maigret na username (modernější než Sherlock)."""
        try:
            proc_check = await asyncio.create_subprocess_exec(
                'maigret', '--help',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(proc_check.communicate(), timeout=5)
        except (TimeoutError, FileNotFoundError):
            logger.debug("[Maigret] Not installed, skipping")
            return []

        try:
            proc = await asyncio.create_subprocess_exec(
                'maigret', username, '--timeout', '5', '-j',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)

            findings = []
            try:
                data = json.loads(stdout.decode(errors='ignore'))
                if isinstance(data, dict):
                    for site, result in data.items():
                        if result.get('status') == 'found':
                            findings.append({
                                'type': 'profile',
                                'url': result.get('url', site),
                                'source': 'maigret',
                                'username': username
                            })
            except json.JSONDecodeError:
                pass

            return findings
        except TimeoutError:
            logger.warning(f"[Maigret] Timeout for {username}")
            return []
        except Exception as e:
            logger.warning(f"[Maigret] Failed: {e}")
            return []

    async def search_username(self, username: str) -> list[dict]:
        """Search username across all available tools."""
        results = []

        # Run sherlock
        sherlock_results = await self.run_sherlock(username)
        results.extend(sherlock_results)

        # Run maigret
        maigret_results = await self.run_maigret(username)
        results.extend(maigret_results)

        return results

    async def search_domain(self, domain: str) -> list[dict]:
        """Search domain for emails and hosts."""
        return await self.run_theharvester(domain)
