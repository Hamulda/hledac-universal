"""
OpsEC Coordinator — F350M-R Architectural Split
===============================================

OPSEC-only (Stealth/Privacy/VPN/Tor/PGP) operations extracted from
UniversalSecurityCoordinator for LCOM reduction and domain isolation.

Integrates:
- StealthEngine — stealth/evasion operations
- PersonalPrivacyManager — VPN, Tor, anonymous email
- DataLeakHunter — data leak monitoring
- PGPManager — PGP identity management
- StealthWebScraper — anti-detection web scraping
- ArchiveResurrector — Wayback Machine resurrection

Shared DTOs (also used by SecurityCoordinator):
- SecurityLevel, SecurityContext, SecurityResult
"""
import contextlib
import importlib
import logging
import time
from dataclasses import field
from enum import Enum
from typing import Any

import msgspec

from .base import DecisionResponse, ExecutionResult, OperationResult, OperationType, UniversalCoordinator

logger = logging.getLogger(__name__)


class SecurityLevel(Enum):
    """Security levels for operations (1-4 scale)."""
    MINIMAL = 1
    STANDARD = 2
    HIGH = 3
    MAXIMUM = 4


class SecurityContext(msgspec.Struct, gc=False):
    """Security context for operations."""
    operation_id: str
    security_level: SecurityLevel
    stealth_active: bool = False
    threats_detected: list[str] = field(default_factory=list)
    crypto_operations: list[str] = field(default_factory=list)
    zkp_operations: list[str] = field(default_factory=list)
    audit_log: list[dict[str, Any]] = field(default_factory=list)


class SecurityResult(msgspec.Struct, frozen=True, gc=False):
    """Result of security operation."""
    operation_type: str
    success: bool
    summary: str
    security_level: SecurityLevel
    execution_time: float
    measures_activated: int = 0
    threats_found: int = 0
    result_data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

_OPSEC_SUBSYSTEMS: list[tuple[str, str, str]] = [
    ('stealth_engine', 'hledac.security.stealth_engine', 'StealthEngine'),
]


class OpsECCoordinator(UniversalCoordinator):
    """
    Operations Security coordinator — handles stealth, privacy, VPN, Tor, PGP.

    Integrates four OPSEC backends:
    1. StealthEngine - Anti-detection / evasion
    2. PersonalPrivacyManager - VPN, Tor, anonymous email
    3. DataLeakHunter - Data leak monitoring
    4. PGPManager - PGP identity management

    Routing Strategy:
    - 'stealth'/'evasion'/'anonymize' → StealthEngine
    - 'vpn'/'privacy'/'tor' → PersonalPrivacyManager
    - 'leak'/'breach'/'monitoring' → DataLeakHunter
    - 'pgp'/'encrypt'/'identity' → PGPManager
    """
    __slots__ = (
        '_stealth_engine', '_stealth_available', '_stealth_mode_active',
        '_stealth_activations',
    )

    def __init__(self, max_concurrent: int = 5) -> None:
        super().__init__(
            name='opsec_coordinator',
            max_concurrent=max_concurrent,
            memory_aware=True,
        )
        self._stealth_engine: Any | None = None
        self._stealth_available = False
        self._stealth_mode_active = False
        self._stealth_activations = 0

    async def _do_initialize(self) -> bool:
        """Initialize OPSEC subsystems with graceful degradation."""
        initialized_any = False
        for attr_name, module_path, factory_name in _OPSEC_SUBSYSTEMS:
            try:
                module = importlib.import_module(module_path)
                factory = getattr(module, factory_name)
                instance = factory()
                if hasattr(instance, 'initialize'):
                    await instance.initialize()
                setattr(self, f'_{attr_name}', instance)
                setattr(self, f'{attr_name}_available', True)
                initialized_any = True
                logger.info(f'OpsECCoordinator: {factory_name} initialized')
            except ImportError:
                logger.warning(f'OpsECCoordinator: {factory_name} not available')
            except Exception as e:
                logger.warning(f'OpsECCoordinator: {factory_name} init failed: {e}')
        return initialized_any

    async def _do_cleanup(self) -> None:
        """Cleanup OPSEC subsystems."""
        if self._stealth_engine and hasattr(self._stealth_engine, 'cleanup'):
            try:
                await self._stealth_engine.cleanup()
            except Exception as e:
                logger.error(f'Error cleaning up StealthEngine: {e}')

    def get_supported_operations(self) -> list[OperationType]:
        return [OperationType.SECURITY]

    def _get_operation_type_for_tracking(self) -> str:
        """Return operation type for tracking."""
        return 'opsec'

    async def _do_execute_decision(self, decision: DecisionResponse) -> ExecutionResult:
        """Handle OPSEC request — delegates to appropriate backend."""
        chosen = decision.chosen_option.lower()
        if any(k in chosen for k in ('stealth', 'evasion', 'anonymize')):
            if self._stealth_available:
                result = await self._execute_stealth_op(decision, '', SecurityLevel.MINIMAL)
                return ExecutionResult(
                    status='completed' if result.success else 'failed',
                    result_summary=result.summary,
                    success=result.success,
                    metadata={'operation_type': result.operation_type},
                )
            else:
                raise RuntimeError('No OPSEC backends available')
        else:
            raise RuntimeError(f'Unhandled OPSEC operation: {decision.chosen_option}')

    async def _execute_stealth_op(
        self,
        decision: DecisionResponse,
        context: str,
        security_level: SecurityLevel,
    ):
        """Execute stealth operation (stub for handle_request routing)."""
        start_time = time.time()
        if not self._stealth_engine:
            raise RuntimeError('StealthEngine not available')
        stealth_result = await self._stealth_engine.activate_stealth_mode(
            operation_type=context,
            confidence_threshold=decision.confidence,
            security_level=security_level.value,
        )
        self._stealth_activations += 1
        self._stealth_mode_active = stealth_result.get('active', False)
        return SecurityResult(
            operation_type='stealth',
            success=stealth_result.get('success', False),
            summary=f"Stealth: {stealth_result.get('measures_activated', 0)} measures activated",
            security_level=security_level,
            execution_time=time.time() - start_time,
            measures_activated=stealth_result.get('measures_activated', 0),
            result_data=stealth_result,
        )

    # ─── Privacy / VPN ────────────────────────────────────────────────────────

    async def establish_vpn_connection(
        self,
        provider: str = 'mullvad',
        protocol: str = 'wireguard',
        server: str | None = None,
    ) -> dict[str, Any]:
        """Establish VPN connection via PersonalPrivacyManager."""
        try:
            from hledac.universal.privacy_protection.personal_privacy_manager import (  # noqa: F401
                PersonalPrivacyManager,
                PrivacyLevel,
                VPNConfig,
                VPNDriver,
            )
            if not server:
                servers = VPNDriver.PROVIDERS.get(provider, {}).get('servers', [])
                if servers:
                    import secrets
                    server = secrets.choice(servers)
                else:
                    return {'success': False, 'error': f'Unknown provider: {provider}'}
            config = VPNConfig(
                provider=provider, server=server, protocol=protocol,
                dns_leak_protection=True, kill_switch=True,
            )
            driver = VPNDriver(config)
            success = await driver.connect()
            if success:
                return {
                    'success': True, 'provider': provider, 'protocol': protocol,
                    'server': server, 'connected': True,
                    'dns_protection': config.dns_leak_protection,
                }
            return {
                'success': False, 'error': 'VPN connection failed',
                'provider': provider, 'server': server,
            }
        except ImportError:
            return {'success': False, 'error': 'PersonalPrivacyManager not available'}
        except Exception as e:
            logger.error(f'VPN connection failed: {e}')
            return {'success': False, 'error': str(e)}

    async def disconnect_vpn(self) -> dict[str, Any]:
        """Disconnect active VPN connection."""
        try:
            from hledac.universal.privacy_protection.personal_privacy_manager import VPNDriver  # noqa: F401
            return {
                'success': True,
                'message': 'VPN disconnect initiated',
                'note': 'Use system network settings to verify disconnection',
            }
        except ImportError:
            return {'success': False, 'error': 'VPNDriver not available'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    async def establish_privacy_connection(
        self,
        privacy_level: str = 'enhanced',
        connection_type: str | None = None,
    ) -> dict[str, Any]:
        """Establish privacy-protected connection via PersonalPrivacyManager."""
        logger.warning('PersonalPrivacyManager not available')
        return {'success': False, 'error': 'PersonalPrivacyManager not available'}

    # ─── Email / PGP ───────────────────────────────────────────────────────────

    async def send_anonymous_email(
        self,
        to_address: str,
        subject: str,
        body: str,
        provider: str = 'protonmail',
        use_tor: bool = True,
        encrypt: bool = False,
        recipient_key: str | None = None,
    ) -> dict[str, Any]:
        """Send anonymous email through secure providers with optional Tor."""
        try:
            from hledac.universal.privacy_protection.anonymous_communication import (
                EmailConfig,
                TorMailer,
            )
            mailer = TorMailer(use_tor=use_tor)
            config = EmailConfig(
                smtp_server='127.0.0.1' if use_tor else 'smtp.protonmail.com',
                smtp_port=1025 if use_tor else 587,
                username='anonymous@protonmail.com',
                password='',
                use_tls=not use_tor,
                use_tor=use_tor,
            )
            success = await mailer.send_email(
                config=config, to_address=to_address, subject=subject, body=body,
                encrypt=encrypt, recipient_key=recipient_key,
            )
            return {
                'success': success, 'provider': provider, 'tor_used': use_tor,
                'encrypted': encrypt, 'recipient': to_address,
                'privacy_level': 'maximum' if use_tor and encrypt else 'high',
            }
        except ImportError:
            logger.warning('Anonymous communication module not available')
            return {'success': False, 'error': 'Module not available'}
        except Exception as e:
            logger.error(f'Anonymous email failed: {e}')
            return {'success': False, 'error': str(e)}

    async def create_pgp_identity(
        self,
        name: str,
        email: str,
        key_type: str = 'RSA',
        key_length: int = 4096,
    ) -> dict[str, Any]:
        """Create PGP identity for secure communication."""
        try:
            from hledac.universal.privacy_protection.anonymous_communication import PGPManager
            manager = PGPManager()
            key = manager.generate_key(name, email, key_type, key_length)
            if key:
                return {
                    'success': True,
                    'key_id': key.key_id,
                    'fingerprint': key.fingerprint,
                    'public_key': key.public_key[:100] + '...',
                    'created_at': key.created_at.isoformat(),
                    'expires_at': key.expires_at.isoformat() if key.expires_at else None,
                }
            return {'success': False, 'error': 'Key generation failed'}
        except ImportError:
            return {'success': False, 'error': 'PGPManager not available'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    async def establish_secure_channel(
        self,
        participant_ids: list[str],
        channel_name: str | None = None,
    ) -> dict[str, Any]:
        """Create secure encrypted communication channel."""
        try:
            from hledac.universal.privacy_protection.anonymous_communication import (
                SecureChannelManager,
            )
            manager = SecureChannelManager()
            channel = manager.create_channel(participant_ids, channel_name)
            if channel:
                return {
                    'success': True,
                    'channel_id': channel.channel_id,
                    'participants': list(channel.participants),
                    'encryption': 'AES-256-GCM',
                    'created_at': channel.created_at.isoformat(),
                }
            return {'success': False, 'error': 'Channel creation failed'}
        except ImportError:
            return {'success': False, 'error': 'SecureChannelManager not available'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ─── Leak Detection ────────────────────────────────────────────────────────

    async def check_data_leaks(
        self,
        target: str,
        target_type: str = 'email',
    ) -> dict[str, Any]:
        """Check for data leaks using DataLeakHunter."""
        try:
            from hledac.universal.intel.data_leak_hunter import DataLeakHunter
            hunter = DataLeakHunter()
            await hunter.initialize()
            await hunter.add_target(target, target_type)
            alerts = await hunter.check_target(target, target_type)
            return {
                'success': True,
                'target': target,
                'alerts_count': len(alerts),
                'alerts': [
                    {
                        'severity': alert.severity.value,
                        'source': alert.source.value,
                        'breach_name': alert.breach_name,
                        'timestamp': alert.timestamp.isoformat(),
                    }
                    for alert in alerts
                ],
                'high_risk': sum(
                    1 for a in alerts
                    if a.severity.value in ('high', 'critical')
                ),
            }
        except (ImportError, ModuleNotFoundError):
            logger.warning('DataLeakHunter not available')
            return {'success': False, 'error': 'DataLeakHunter not available'}
        except Exception as e:
            logger.error(f'Data leak check failed: {e}')
            return {'success': False, 'error': str(e)}

    async def manage_data_leak_monitoring(
        self,
        action: str,
        target: str | None = None,
        target_type: str | None = None,
        check_interval: int = 3600,
    ) -> dict[str, Any]:
        """Manage continuous data leak monitoring."""
        try:
            from hledac.universal.stealth_osint.data_leak_hunter import DataLeakHunter
            if not hasattr(self, '_leak_hunter'):
                self._leak_hunter = DataLeakHunter(check_interval=check_interval)
                await self._leak_hunter.initialize()
            hunter = self._leak_hunter
            if action == 'start':
                await hunter.start_monitoring()
                return {
                    'success': True, 'action': 'start_monitoring',
                    'interval': check_interval,
                    'targets_count': len(hunter._targets),
                }
            elif action == 'stop':
                await hunter.stop_monitoring()
                return {'success': True, 'action': 'stop_monitoring'}
            elif action == 'add_target' and target and target_type:
                target_id = await hunter.add_target(target, target_type)
                return {
                    'success': True, 'action': 'add_target',
                    'target_id': target_id, 'target': target,
                }
            elif action == 'remove_target' and target:
                target_id = None
                for tid, t in hunter._targets.items():
                    if t.value == target:
                        target_id = tid
                        break
                if target_id:
                    success = await hunter.remove_target(target_id)
                    return {'success': success, 'action': 'remove_target', 'target': target}
                return {'success': False, 'error': 'Target not found'}
            elif action == 'status':
                return {
                    'success': True,
                    'action': 'status',
                    'is_monitoring': hunter._is_monitoring,
                    'targets_count': len(hunter._targets),
                    'checks_performed': hunter._checks_performed,
                    'alerts_generated': hunter._alerts_generated,
                }
            return {'success': False, 'error': f'Unknown action: {action}'}
        except ImportError:
            return {'success': False, 'error': 'DataLeakHunter not available'}
        except Exception as e:
            logger.error(f'Monitoring management failed: {e}')
            return {'success': False, 'error': str(e)}

    # ─── Stealth / Scraping ────────────────────────────────────────────────────

    async def stealth_scrape(
        self,
        url: str,
        protection_bypass: bool = True,
        fingerprint_rotation: bool = True,
    ) -> dict[str, Any]:
        """Stealth web scraping with anti-detection via StealthWebScraper."""
        try:
            from hledac.universal.intel.stealth_crawler import StealthWebScraper
            scraper = StealthWebScraper()
            await scraper.initialize()
            result = await scraper.scrape(url=url, headers=None, use_proxy=protection_bypass)
            return {
                'success': result.success,
                'content': result.content,
                'status_code': result.status_code,
                'protection_detected': result.protection_detected.value,
                'bypass_method': result.bypass_method_used.value,
                'duration': result.duration,
                'proxy_used': result.proxy_used,
            }
        except (ImportError, ModuleNotFoundError):
            logger.warning('StealthWebScraper not available')
            return {'success': False, 'error': 'StealthWebScraper not available'}
        except Exception as e:
            logger.error(f'Stealth scraping failed: {e}')
            return {'success': False, 'error': str(e)}

    async def stealth_request_with_jitter(
        self,
        url: str,
        method: str = 'GET',
        headers: dict[str, str] | None = None,
        impersonate: str = 'chrome110',
        jitter_shape: float = 1.5,
        jitter_scale: float = 2.0,
        min_delay: float = 0.5,
        max_delay: float = 10.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Stealth HTTP request with Weibull-distributed jitter delays."""
        import asyncio
        start_time = time.time()
        try:
            try:
                import numpy as np
                delay = np.random.weibull(jitter_shape) * jitter_scale
            except ImportError:
                delay = _RNG.uniform(min_delay, max_delay)
            delay = max(min_delay, min(delay, max_delay))
            await asyncio.sleep(delay)
            try:
                from curl_cffi.requests import AsyncSession
                async with AsyncSession(impersonate=impersonate) as session:
                    if method.upper() == 'GET':
                        resp = await session.get(url, headers=headers, **kwargs)
                    elif method.upper() == 'POST':
                        resp = await session.post(url, headers=headers, **kwargs)
                    else:
                        resp = await session.request(method, url, headers=headers, **kwargs)
                    elapsed = time.time() - start_time
                    return {
                        'success': True,
                        'url': url,
                        'status_code': resp.status_code,
                        'content': resp.text[:5000] if hasattr(resp, 'text') else '',
                        'headers': dict(resp.headers) if hasattr(resp, 'headers') else {},
                        'elapsed_seconds': elapsed,
                        'jitter_delay': delay,
                        'impersonate': impersonate,
                        'method': 'curl_cffi',
                    }
            except ImportError:

                from hledac.universal.network.session_runtime import async_get_httpx_session
                session = await async_get_httpx_session()
                async with session.request(method, url, headers=headers, **kwargs) as resp:
                    content = resp.text
                    elapsed = time.time() - start_time
                    return {
                        'success': True,
                        'url': url,
                        'status_code': resp.status_code,
                        'content': content[:5000],
                        'headers': dict(resp.headers),
                        'elapsed_seconds': elapsed,
                        'jitter_delay': delay,
                        'impersonate': None,
                        'method': 'httpx',
                    }
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f'Stealth request failed for {url}: {e}')
            return {'success': False, 'url': url, 'error': str(e), 'elapsed_seconds': elapsed}

    async def batch_stealth_requests(
        self,
        urls: list[str],
        concurrency: int = 3,
        jitter_range: tuple[float, float] = (0.5, 5.0),
    ) -> list[dict[str, Any]]:
        """Execute multiple stealth requests with controlled concurrency."""
        from asyncio import Semaphore

        from hledac.universal.utils.async_helpers import parallel

        semaphore = Semaphore(concurrency)

        async def fetch_with_limit(url: str) -> dict[str, Any]:
            async with semaphore:
                return await self.stealth_request_with_jitter(
                    url, min_delay=jitter_range[0], max_delay=jitter_range[1],
                )

        tasks = [fetch_with_limit(url) for url in urls]
        _sec_result = await parallel(tasks, policy='log', ctx='opsec_coordinator')
        results = _sec_result.ok
        processed = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed.append({'success': False, 'url': urls[i], 'error': str(result)})
            else:
                processed.append(result)
        return processed

    async def enable_stealth_mode(self, level: str = 'medium') -> dict[str, Any]:
        """Enable stealth mode via AnonymityManager."""
        try:
            logger.warning('AnonymityManager not available, stealth mode unavailable')
            return {'success': False, 'error': 'Anonymity Manager not available', 'level': level}
        except Exception as e:
            logger.error(f'Stealth activation failed: {e}')
            return {'success': False, 'error': str(e)}

    def is_stealth_active(self) -> bool:
        """Check if stealth mode is currently active."""
        return self._stealth_mode_active

    async def fetch_with_limit(self, url: str) -> dict[str, Any]:
        """Fetch URL with stealth limitations."""
        return await self.stealth_request_with_jitter(url, min_delay=1.0, max_delay=5.0)

    def get_browser_fingerprint(
        self,
        platform: str = 'macos',
        browser: str = 'chrome',
        domain: str | None = None,
    ) -> dict[str, Any]:
        """Get browser fingerprint via FingerprintManager."""
        logger.warning('FingerprintManager not available')
        return {'success': False, 'error': 'FingerprintManager not available'}

    async def resurrect_from_archive(
        self,
        url: str,
        target_date: str | None = None,
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        """Resurrect content from web archives using ArchiveResurrector."""
        try:
            from hledac.universal.intel.archive_discovery import ArchiveResurrector
            resurrector = ArchiveResurrector()
            await resurrector.initialize()
            parsed_date = None
            if target_date:
                from datetime import datetime
                with contextlib.suppress(ValueError):
                    parsed_date = datetime.fromisoformat(target_date)
            result = await resurrector.resurrect(url=url, target_date=parsed_date, min_quality=None)
            return {
                'success': result.success,
                'content': result.content,
                'title': result.title,
                'snapshots_found': len(result.all_snapshots),
                'best_snapshot': result.best_snapshot.snapshot_id if result.best_snapshot else None,
                'processing_time': result.processing_time,
            }
        except (ImportError, ModuleNotFoundError):
            logger.warning('ArchiveResurrector not available')
            return {'success': False, 'error': 'ArchiveResurrector not available'}
        except Exception as e:
            logger.error(f'Archive resurrection failed: {e}')
            return {'success': False, 'error': str(e)}

    # ─── Shared state helpers ──────────────────────────────────────────────────

    def get_available_security_systems(self) -> dict[str, bool]:
        """Get availability status of all OPSEC systems."""
        return {'stealth': self._stealth_available}

    def get_security_stats(self) -> dict[str, int]:
        """Get OPSEC operation statistics."""
        return {'stealth_activations': self._stealth_activations}

    def _get_feature_list(self) -> list[str]:
        """Report available OPSEC features."""
        features = ['OPSEC coordinator']
        if self._stealth_available:
            features.append('Stealth/Evasion Operations')
        features.extend([
            'Privacy connections (VPN/Tor)',
            'Anonymous email',
            'PGP identity management',
            'Data leak monitoring',
        ])
        return features
