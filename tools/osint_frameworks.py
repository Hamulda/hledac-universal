"""
Wrapper pro externí OSINT nástroje (theHarvester, Sherlock, Maigret).
Sprint 46: Access to Unreachable Data (Sessions + Paywall + OSINT + Darknet)
"""

import asyncio
import contextlib
import json
import logging
import os
import tempfile
logger = logging.getLogger(__name__)

async def _reap_proc(proc: asyncio.subprocess.Process | None) -> None:
    """Fail-safe subprocess teardown — kill if alive, then wait to reap zombie.

    Critical on M1 8GB UMA: leaked subprocesses accumulate RSS pressure
    that the M1ResourceGovernor cannot reclaim.
    """
    if proc is None or proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    with contextlib.suppress(Exception):
        await proc.wait()

class OSINTFrameworkRunner:
    """Runner pro externí OSINT nástroje."""
    __slots__ = tuple(('_timeout',))

    def __init__(self):
        self._timeout = 30

    async def run_theharvester(self, target: str) -> list[dict]:
        """Spustí theHarvester na doménu/jméno."""
        proc_check: asyncio.subprocess.Process | None = None
        try:
            proc_check = await asyncio.create_subprocess_exec('theHarvester', '--help', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            async with asyncio.timeout(5):
                await proc_check.communicate()
        except (TimeoutError, FileNotFoundError) as e:
            logger.debug('[theHarvester] Probe failed (%s), skipping', type(e).__name__)
            await _reap_proc(proc_check)
            return []
        with tempfile.NamedTemporaryFile(suffix='', delete=False, dir=tempfile.gettempdir()) as f:
            out_file = f.name
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec('theHarvester', '-d', target, '-b', 'all', '-f', out_file, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            async with asyncio.timeout(self._timeout):
                stdout, stderr = await proc.communicate()
            findings = []
            for ext in ['.json', '.xml']:
                try:
                    file_path = out_file + ext
                    if os.path.exists(file_path):
                        with open(file_path) as f:
                            data = json.load(f)
                        if isinstance(data, dict):
                            for email in data.get('emails', []):
                                findings.append({'type': 'email', 'value': email if isinstance(email, str) else email.get('email', str(email)), 'source': 'theHarvester'})
                            for host in data.get('hosts', []):
                                findings.append({'type': 'host', 'value': host if isinstance(host, str) else host.get('host', str(host)), 'source': 'theHarvester'})
                        break
                except Exception as e:
                    logger.debug(f'[theHarvester] Parse error: {e}')
                    continue
            return findings
        except TimeoutError:
            logger.warning('[theHarvester] Timeout for %s after %.1fs', target, self._timeout, extra={'tool': 'theharvester', 'target': target, 'timeout_s': self._timeout})
            await _reap_proc(proc)
            return []
        except Exception as e:
            logger.warning(f'[theHarvester] Failed: {e}')
            await _reap_proc(proc)
            return []
        finally:
            for ext in ['', '.json', '.xml']:
                try:
                    if os.path.exists(out_file + ext):
                        os.unlink(out_file + ext)
                except FileNotFoundError:
                    pass

    async def run_sherlock(self, username: str) -> list[dict]:
        """Spustí Sherlock na username s --json flagem pro strukturální výstup."""
        proc_check: asyncio.subprocess.Process | None = None
        try:
            proc_check = await asyncio.create_subprocess_exec('sherlock', '--help', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            async with asyncio.timeout(5):
                await proc_check.communicate()
        except (TimeoutError, FileNotFoundError) as e:
            logger.debug('[Sherlock] Probe failed (%s), skipping', type(e).__name__)
            await _reap_proc(proc_check)
            return []
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec('sherlock', username, '--nsfw', '--timeout', '5', '--json', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            async with asyncio.timeout(self._timeout):
                stdout, stderr = await proc.communicate()
            findings = []
            try:
                data = json.loads(stdout.decode(errors='ignore'))
                for site, info in data.items():
                    if isinstance(info, dict) and info.get('url'):
                        findings.append({'type': 'profile', 'url': info['url'], 'site': site, 'source': 'sherlock'})
            except json.JSONDecodeError:
                for line in stdout.decode(errors='ignore').split('\n'):
                    if '[+]' in line:
                        parts = line.split()
                        if len(parts) > 1:
                            url = parts[1] if parts[1].startswith('http') else parts[0]
                            findings.append({'type': 'profile', 'url': url, 'source': 'sherlock'})
            return findings
        except TimeoutError:
            logger.warning('[Sherlock] Timeout for %s after %.1fs', username, self._timeout, extra={'tool': 'sherlock', 'target': username, 'timeout_s': self._timeout})
            await _reap_proc(proc)
            return []
        except Exception as e:
            logger.warning(f'[Sherlock] Failed: {e}')
            await _reap_proc(proc)
            return []

    async def run_maigret(self, username: str) -> list[dict]:
        """Spustí Maigret na username (modernější než Sherlock)."""
        proc_check: asyncio.subprocess.Process | None = None
        try:
            proc_check = await asyncio.create_subprocess_exec('maigret', '--help', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            async with asyncio.timeout(5):
                await proc_check.communicate()
        except (TimeoutError, FileNotFoundError) as e:
            logger.debug('[Maigret] Probe failed (%s), skipping', type(e).__name__)
            await _reap_proc(proc_check)
            return []
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec('maigret', username, '--timeout', '5', '-j', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            async with asyncio.timeout(self._timeout):
                stdout, stderr = await proc.communicate()
            findings = []
            try:
                data = json.loads(stdout.decode(errors='ignore'))
                if isinstance(data, dict):
                    for site, result in data.items():
                        if result.get('status') == 'found':
                            findings.append({'type': 'profile', 'url': result.get('url', site), 'source': 'maigret', 'username': username})
            except json.JSONDecodeError:
                pass
            return findings
        except TimeoutError:
            logger.warning('[Maigret] Timeout for %s after %.1fs', username, self._timeout, extra={'tool': 'maigret', 'target': username, 'timeout_s': self._timeout})
            await _reap_proc(proc)
            return []
        except Exception as e:
            logger.warning(f'[Maigret] Failed: {e}')
            await _reap_proc(proc)
            return []

    async def search_username(self, username: str) -> list[dict]:
        """Search username across all available tools."""
        results = []
        sherlock_results = await self.run_sherlock(username)
        results.extend(sherlock_results)
        maigret_results = await self.run_maigret(username)
        results.extend(maigret_results)
        return results

    async def search_domain(self, domain: str) -> list[dict]:
        """Search domain for emails and hosts."""
        return await self.run_theharvester(domain)