"""
Security Coordinator — F350M-R Architectural Split
===================================================

Threat intelligence + Quantum Crypto + ZKP operations.
OpsEC (stealth/privacy/VPN) extracted to OpsECCoordinator.

Shared DTOs (SecurityLevel, SecurityContext, SecurityResult) are defined
in opsec_coordinator.py and re-exported from here for convenience.
"""
import importlib
import logging
import secrets
import time
from typing import Any

from .base import DecisionResponse, OperationResult, OperationType, UniversalCoordinator
from .opsec_coordinator import (
    OpsECCoordinator,
    SecurityContext,
    SecurityLevel,
    SecurityResult,
)

logger = logging.getLogger(__name__)

_SECURITY_SUBSYSTEMS: list[tuple[str, str, str]] = [
    ('threat_intelligence', 'compat.security_threat_intelligence', 'ThreatIntelligence'),
    ('zkp_engine', 'compat.security_zkp_research_engine', 'ZKPResearchEngine'),
]


class SecurityCoordinator(UniversalCoordinator):
    """
    Security coordinator — threat intelligence, quantum crypto, ZKP.

    Integrates three security backends:
    1. ThreatIntelligence - Threat detection and analysis
    2. QuantumResistantCrypto - Post-quantum cryptography
    3. ZKPResearchEngine - Zero-Knowledge Proofs

    Routing Strategy:
    - 'threat'/'intelligence'/'detect' → ThreatIntelligence
    - 'quantum'/'crypto'/'encrypt' → QuantumResistantCrypto
    - 'zkp'/'proof'/'verify' → ZKPResearchEngine
    """
    __slots__ = tuple((
        '_crypto_operations', '_global_threat_level', '_max_contexts',
        '_pq_backend', '_security_contexts',
        '_threat_analyses', '_threat_available', '_threat_intelligence',
        '_zkp_available', '_zkp_engine', '_zkp_operations', '_crypto_available',
    ))

    def __init__(self, max_concurrent: int = 5):
        super().__init__(
            name='security_coordinator',
            max_concurrent=max_concurrent,
            memory_aware=True,
        )
        self._threat_intelligence: Any | None = None
        self._pq_backend: Any | None = None
        self._zkp_engine: Any | None = None
        self._threat_available = False
        self._crypto_available = False
        self._zkp_available = False
        self._security_contexts: dict[str, SecurityContext] = {}
        self._max_contexts = 50
        self._threat_analyses = 0
        self._crypto_operations = 0
        self._zkp_operations = 0
        self._global_threat_level = 0.0

    async def _do_initialize(self) -> bool:
        """Initialize security subsystems with graceful degradation."""
        initialized_any = False
        for attr_name, module_path, factory_name in _SECURITY_SUBSYSTEMS:
            try:
                module = importlib.import_module(module_path)
                factory = getattr(module, factory_name)
                instance = factory()
                if hasattr(instance, 'initialize'):
                    await instance.initialize()
                setattr(self, f'_{attr_name}', instance)
                setattr(self, f'{attr_name}_available', True)
                initialized_any = True
                logger.info(f'SecurityCoordinator: {factory_name} initialized')
            except ImportError:
                logger.warning(f'SecurityCoordinator: {factory_name} not available')
            except Exception as e:
                logger.warning(f'SecurityCoordinator: {factory_name} init failed: {e}')
        try:
            from hledac.universal.security.pq_crypto import PQAvailability, create_post_quantum_backend
            self._pq_backend, pq_status = await create_post_quantum_backend(
                enabled=True, key_id='hledac.security.v1',
            )
            self._crypto_available = pq_status.availability.value in (
                'available', 'signed', 'fail_soft',
            )
            initialized_any = True
            logger.info(
                f'SecurityCoordinator: PQ backend initialized '
                f'({pq_status.availability.value})',
            )
        except ImportError:
            logger.warning('SecurityCoordinator: PQ backend not available')
        except Exception as e:
            logger.warning(f'SecurityCoordinator: PQ backend init failed: {e}')
        return initialized_any

    async def _do_cleanup(self) -> None:
        """Cleanup security subsystems."""
        if self._threat_intelligence and hasattr(self._threat_intelligence, 'cleanup'):
            try:
                await self._threat_intelligence.cleanup()
            except Exception as e:
                logger.error(f'Error cleaning up ThreatIntelligence: {e}')
        if self._pq_backend and hasattr(self._pq_backend, 'cleanup'):
            try:
                await self._pq_backend.cleanup()
            except Exception as e:
                logger.error(f'Error cleaning up PQ backend: {e}')
        if self._zkp_engine and hasattr(self._zkp_engine, 'cleanup'):
            try:
                await self._zkp_engine.cleanup()
            except Exception as e:
                logger.error(f'Error cleaning up ZKP: {e}')
        self._security_contexts.clear()

    def get_supported_operations(self) -> list[OperationType]:
        return [OperationType.SECURITY]

    async def handle_request(
        self,
        operation_ref: str,
        decision: DecisionResponse,
    ) -> OperationResult:
        """Handle security request — routes to threat/crypto/ZKP backend."""
        start_time = time.time()
        operation_id = self.generate_operation_id()
        try:
            self.track_operation(
                operation_id,
                {'operation_ref': operation_ref, 'decision': decision, 'type': 'security'},
            )
            result = await self._execute_security_decision(decision)
            operation_result = OperationResult(
                operation_id=operation_id,
                status='completed' if result.success else 'failed',
                result_summary=result.summary,
                execution_time=time.time() - start_time,
                success=result.success,
                metadata={
                    'security_operation': result.operation_type,
                    'security_level': result.security_level.value,
                    'measures_activated': result.measures_activated,
                    'threats_found': result.threats_found,
                },
            )
        except Exception as e:
            operation_result = OperationResult(
                operation_id=operation_id,
                status='failed',
                result_summary=f'Security operation failed: {str(e)}',
                execution_time=time.time() - start_time,
                success=False,
                error_message=str(e),
            )
        finally:
            self.untrack_operation(operation_id)
        self.record_operation_result(operation_result)
        return operation_result

    async def _execute_security_decision(
        self,
        decision: DecisionResponse,
    ) -> SecurityResult:
        """Route security decision to appropriate sub-backend."""
        chosen = decision.chosen_option.lower()
        context = decision.reasoning or decision.metadata.get('context', '')
        security_level = self._confidence_to_security_level(decision.confidence)
        if any(k in chosen for k in ('threat', 'intelligence', 'detect')):
            if self._threat_available:
                return await self._execute_threat_analysis(decision, context, security_level)
        elif any(k in chosen for k in ('quantum', 'crypto', 'encrypt')):
            if self._crypto_available:
                return await self._execute_crypto_operation(decision, security_level)
        elif any(k in chosen for k in ('zkp', 'proof', 'verify')):
            if self._zkp_available:
                return await self._execute_zkp_operation(decision, context, security_level)
        if self._threat_available:
            return await self._execute_threat_analysis(decision, context, security_level)
        return SecurityResult(
            operation_type='none',
            success=False,
            summary='No security backends available',
            security_level=security_level,
            execution_time=0.0,
            error='No security subsystems initialized',
        )

    # ─── PII ─────────────────────────────────────────────────────────────────

    async def detect_pii(self, text: str) -> dict[str, Any]:
        """Detect PII in text using SecurityGate."""
        try:
            from hledac.universal.security.pii_gate import SecurityGate
            gate = SecurityGate()
            result = gate.sanitize(text, mask_pii=False, return_matches=True)
            return {
                'success': True,
                'detections': [
                    {
                        'text': m.text,
                        'label': m.category.value,
                        'score': m.confidence,
                        'start': m.start,
                        'end': m.end,
                    }
                    for m in result.pii_found
                ],
                'risk_analysis': {
                    'risk_level': result.risk_level,
                    'risk_score': result.risk_score,
                },
                'detections_count': result.pii_count,
            }
        except Exception as e:
            logger.error(f'PII detection failed: {e}')
            return {'success': False, 'error': str(e), 'detections': []}

    async def redact_pii(self, text: str) -> dict[str, Any]:
        """Redact PII from text using SecurityGate."""
        try:
            from hledac.universal.security.pii_gate import SecurityGate
            gate = SecurityGate()
            result = gate.sanitize(text, mask_pii=True, return_matches=True)
            return {
                'success': True,
                'original': text,
                'redacted': result.sanitized_text,
                'detections_count': result.pii_count,
                'redactions_applied': result.pii_count,
            }
        except Exception as e:
            logger.error(f'PII redaction failed: {e}')
            return {'success': False, 'error': str(e), 'text': text}

    async def sanitize_outbound(
        self,
        content: str,
        force_fallback: bool = False,
    ) -> dict[str, Any]:
        """Early privacy gate for outbound content."""
        from hledac.universal.security.pii_gate import SecurityGate, fallback_sanitize
        try:
            if force_fallback:
                sanitized = fallback_sanitize(
                    content[:10000] if len(content) > 10000 else content,
                )
                return {
                    'success': True,
                    'sanitized': sanitized,
                    'method': 'fallback',
                    'gate': 'early_privacy',
                    'boundary': 'outbound',
                    'truncated': len(content) > 10000,
                    'original_length': len(content),
                }
            gate = SecurityGate()
            result = gate.sanitize(content, mask_pii=True, return_matches=False)
            return {
                'success': True,
                'sanitized': result.sanitized_text,
                'method': 'security_gate',
                'gate': 'early_privacy',
                'boundary': 'outbound',
                'pii_count': result.pii_count,
                'risk_level': result.risk_level,
            }
        except Exception as e:
            sanitized = fallback_sanitize(
                content[:10000] if len(content) > 10000 else content,
            )
            return {
                'success': True,
                'sanitized': sanitized,
                'method': 'fallback_on_error',
                'gate': 'early_privacy',
                'boundary': 'outbound',
                'error': str(e),
                'truncated': len(content) > 10000,
                'original_length': len(content),
            }

    # ─── Threat Intelligence ──────────────────────────────────────────────────

    async def _execute_threat_analysis(
        self,
        decision: DecisionResponse,
        context: str,
        security_level: SecurityLevel,
    ) -> SecurityResult:
        """Execute threat intelligence analysis."""
        start_time = time.time()
        if not self._threat_intelligence:
            raise RuntimeError('ThreatIntelligence not available')
        threat_result = await self._threat_intelligence.analyze_threats(
            context=context,
            priority_level=decision.confidence,
            security_level=security_level.value,
        )
        execution_time = time.time() - start_time
        self._threat_analyses += 1
        threats = threat_result.get('threats', [])
        self._global_threat_level = threat_result.get('threat_level', 0.0)
        return SecurityResult(
            operation_type='threat',
            success=True,
            summary=f'Threat analysis: {len(threats)} threats identified',
            security_level=security_level,
            execution_time=execution_time,
            threats_found=len(threats),
            result_data=threat_result,
        )

    async def analyze_threat_intelligence(
        self,
        context: str,
        priority: float = 0.7,
    ) -> dict[str, Any]:
        """Analyze threat intelligence data."""
        if not self._threat_intelligence:
            return {'success': False, 'error': 'ThreatIntelligence not available'}
        try:
            result = await self._threat_intelligence.analyze_threats(
                context=context,
                priority_level=priority,
                security_level=3,
            )
            return {'success': True, 'result': result}
        except Exception as e:
            logger.error(f'Threat analysis failed: {e}')
            return {'success': False, 'error': str(e)}

    async def correlate_threats(self, iocs: list[str]) -> dict[str, Any]:
        """Correlate threat indicators across sources."""
        if not self._threat_intelligence:
            return {'success': False, 'error': 'ThreatIntelligence not available'}
        try:
            if hasattr(self._threat_intelligence, 'correlate_iocs'):
                result = await self._threat_intelligence.correlate_iocs(iocs)
                return {'success': True, 'correlations': result}
            return {'success': False, 'error': 'correlate_iocs not available'}
        except Exception as e:
            logger.error(f'Threat correlation failed: {e}')
            return {'success': False, 'error': str(e)}

    async def threat_intel_lookup(self, indicator: str) -> dict[str, Any]:
        """Look up a single threat intelligence indicator."""
        if not self._threat_intelligence:
            return {'success': False, 'error': 'ThreatIntelligence not available'}
        try:
            if hasattr(self._threat_intelligence, 'lookup_ioc'):
                result = await self._threat_intelligence.lookup_ioc(indicator)
                return {'success': True, 'result': result}
            return {'success': False, 'error': 'lookup_ioc not available'}
        except Exception as e:
            logger.error(f'Threat intel lookup failed: {e}')
            return {'success': False, 'error': str(e)}

    # ─── Cryptography ─────────────────────────────────────────────────────────

    async def _execute_crypto_operation(
        self,
        decision: DecisionResponse,
        security_level: SecurityLevel,
    ) -> SecurityResult:
        """Execute quantum-resistant cryptographic operation."""
        from hledac.universal.security.pq_crypto import PQAvailability
        start_time = time.time()
        backend = self._pq_backend
        pq_status = backend.pq_status()
        if pq_status.availability == PQAvailability.DISABLED:
            raise RuntimeError('PQ backend not available')
        execution_time = time.time() - start_time
        self._crypto_operations += 1
        return SecurityResult(
            operation_type='crypto',
            success=pq_status.availability.value in ('available', 'signed', 'fail_soft'),
            summary=(
                f'PQ: {pq_status.backend_name} ({pq_status.availability.value}), '
                f'ML-DSA={backend.has_mldsa()}'
            ),
            security_level=security_level,
            execution_time=execution_time,
            result_data={
                'availability': pq_status.availability.value,
                'backend': pq_status.backend_name,
                'has_mldsa': backend.has_mldsa(),
                'mldsa_key_id': pq_status.mldsa_key_id,
            },
        )

    # ─── ZKP ─────────────────────────────────────────────────────────────────

    async def _execute_zkp_operation(
        self,
        decision: DecisionResponse,
        context: str,
        security_level: SecurityLevel,
    ) -> SecurityResult:
        """Execute zero-knowledge proof operation."""
        start_time = time.time()
        if not self._zkp_engine:
            raise RuntimeError('ZKPResearchEngine not available')
        proof_type = decision.metadata.get('proof_type', 'membership')
        verify = decision.metadata.get('verify', False)
        if verify:
            zkp_result = await self._zkp_engine.verify_proof(
                statement=context,
                proof=decision.metadata.get('proof'),
                proof_type=proof_type,
            )
        else:
            zkp_result = await self._zkp_engine.generate_proof(
                statement=context,
                proof_type=proof_type,
                confidence=decision.confidence,
            )
        execution_time = time.time() - start_time
        self._zkp_operations += 1
        return SecurityResult(
            operation_type='zkp',
            success=zkp_result.get('valid', zkp_result.get('success', False)),
            summary=f"ZKP: {proof_type} proof {('verified' if verify else 'generated')}",
            security_level=security_level,
            execution_time=execution_time,
            result_data=zkp_result,
        )

    # ─── Shared ───────────────────────────────────────────────────────────────

    def _confidence_to_security_level(self, confidence: float) -> SecurityLevel:
        """Map confidence (0.0-1.0) to security level (1-4)."""
        if confidence >= 0.9:
            return SecurityLevel.MAXIMUM
        elif confidence >= 0.7:
            return SecurityLevel.HIGH
        elif confidence >= 0.4:
            return SecurityLevel.STANDARD
        return SecurityLevel.MINIMAL

    async def create_secure_vault(self, size_mb: int = 256) -> dict[str, Any]:
        """Create secure RAM disk vault."""
        try:
            from hledac.universal.hledac.supreme.security.ram_disk_vault import RamDiskVault
            vault = RamDiskVault(size_mb=size_mb)
            mount_point = vault.mount()
            return {
                'success': True,
                'mount_point': mount_point,
                'size_mb': size_mb,
                'type': 'ram_disk',
            }
        except ImportError:
            logger.warning('RAM Disk Vault not available')
            return {'success': False, 'error': 'RAM Disk Vault not available'}
        except Exception as e:
            logger.error(f'Vault creation failed: {e}')
            return {'success': False, 'error': str(e)}

    def create_security_context(
        self,
        operation_id: str,
        security_level: SecurityLevel,
    ) -> SecurityContext:
        """Create and track security context."""
        context = SecurityContext(operation_id=operation_id, security_level=security_level)
        self._security_contexts[operation_id] = context
        while len(self._security_contexts) > self._max_contexts:
            oldest = next(iter(self._security_contexts))
            del self._security_contexts[oldest]
        return context

    def get_security_context(self, operation_id: str) -> SecurityContext | None:
        """Retrieve security context."""
        return self._security_contexts.get(operation_id)

    def audit_log(self, operation_id: str, event: str, details: dict[str, Any]) -> None:
        """Add audit log entry to security context."""
        if operation_id in self._security_contexts:
            self._security_contexts[operation_id].audit_log.append(
                {'timestamp': time.time(), 'event': event, 'details': details},
            )

    def get_global_security_state(self) -> dict[str, Any]:
        """Get global security state summary."""
        return {
            'global_threat_level': self._global_threat_level,
            'active_contexts': len(self._security_contexts),
            'threat_analyses': self._threat_analyses,
            'crypto_operations': self._crypto_operations,
            'zkp_operations': self._zkp_operations,
        }

    def get_threat_level(self) -> float:
        """Get current global threat level."""
        return self._global_threat_level

    def _get_feature_list(self) -> list[str]:
        """Report available features."""
        features = ['Security coordinator']
        if self._threat_available:
            features.append('Threat Intelligence Analysis')
        if self._crypto_available:
            features.append('Quantum-Resistant Cryptography')
        if self._zkp_available:
            features.append('Zero-Knowledge Proofs')
        features.extend([
            'Security level escalation (1-4)',
            'Comprehensive security operations',
            'Security context preservation',
            'Audit logging',
            'Global threat monitoring',
        ])
        return features

    def get_available_security_systems(self) -> dict[str, bool]:
        """Get availability status of all security systems."""
        return {
            'threat_intelligence': self._threat_available,
            'quantum_crypto': self._crypto_available,
            'zkp': self._zkp_available,
        }

    def get_security_stats(self) -> dict[str, int]:
        """Get security operation statistics."""
        return {
            'threat_analyses': self._threat_analyses,
            'crypto_operations': self._crypto_operations,
            'zkp_operations': self._zkp_operations,
        }


# ─── UniversalSecurityCoordinator — Composite ─────────────────────────────────


class UniversalSecurityCoordinator(UniversalCoordinator):
    """
    Universal security coordinator — composes OpsECCoordinator + SecurityCoordinator.

    Provides full backward compatibility: all original public methods are preserved.
    Internally delegates to the appropriate sub-coordinator.

    Routing Strategy:
    - 'stealth'/'evasion'/'anonymize'/'privacy'/'vpn'/'pgp' → OpsECCoordinator
    - 'threat'/'intelligence'/'detect'/'crypto'/'zkp' → SecurityCoordinator
    """
    __slots__ = tuple(('_opsec', '_security'))

    def __init__(self, max_concurrent: int = 5):
        super().__init__(
            name='universal_security_coordinator',
            max_concurrent=max_concurrent,
            memory_aware=True,
        )
        self._opsec: OpsECCoordinator = OpsECCoordinator(max_concurrent=max_concurrent)
        self._security: SecurityCoordinator = SecurityCoordinator(max_concurrent=max_concurrent)

    async def _do_initialize(self) -> bool:
        """Initialize both sub-coordinators."""
        ok_opsec = await self._opsec._do_initialize()
        ok_sec = await self._security._do_initialize()
        return bool(ok_opsec or ok_sec)

    async def _do_cleanup(self) -> None:
        """Cleanup both sub-coordinators."""
        await self._opsec._do_cleanup()
        await self._security._do_cleanup()

    def get_supported_operations(self) -> list[OperationType]:
        return [OperationType.SECURITY]

    # ─── Delegation ───────────────────────────────────────────────────────────

    async def handle_request(
        self,
        operation_ref: str,
        decision: DecisionResponse,
    ) -> OperationResult:
        """Route security request to appropriate sub-coordinator."""
        start_time = time.time()
        operation_id = self.generate_operation_id()
        try:
            self.track_operation(
                operation_id,
                {'operation_ref': operation_ref, 'decision': decision, 'type': 'security'},
            )
            result = await self._execute_security_decision(decision)
            operation_result = OperationResult(
                operation_id=operation_id,
                status='completed' if result.success else 'failed',
                result_summary=result.summary,
                execution_time=time.time() - start_time,
                success=result.success,
                metadata={
                    'security_operation': result.operation_type,
                    'security_level': result.security_level.value,
                    'measures_activated': result.measures_activated,
                    'threats_found': result.threats_found,
                },
            )
        except Exception as e:
            operation_result = OperationResult(
                operation_id=operation_id,
                status='failed',
                result_summary=f'Security operation failed: {str(e)}',
                execution_time=time.time() - start_time,
                success=False,
                error_message=str(e),
            )
        finally:
            self.untrack_operation(operation_id)
        self.record_operation_result(operation_result)
        return operation_result

    async def _execute_security_decision(self, decision: DecisionResponse) -> SecurityResult:
        """Route security decision to appropriate sub-coordinator."""
        chosen = decision.chosen_option.lower()
        context = decision.reasoning or decision.metadata.get('context', '')
        security_level = self._security._confidence_to_security_level(decision.confidence)

        if any(k in chosen for k in ('stealth', 'evasion', 'anonymize', 'privacy', 'vpn', 'pgp')):
            if self._opsec._stealth_available:
                return await self._opsec._execute_stealth_operation(decision, context, security_level)
        elif any(k in chosen for k in ('threat', 'intelligence', 'detect')):
            if self._security._threat_available:
                return await self._security._execute_threat_analysis(decision, context, security_level)
        elif any(k in chosen for k in ('quantum', 'crypto', 'encrypt')):
            if self._security._crypto_available:
                return await self._security._execute_crypto_operation(decision, security_level)
        elif any(k in chosen for k in ('zkp', 'proof', 'verify')):
            if self._security._zkp_available:
                return await self._security._execute_zkp_operation(decision, context, security_level)

        # Fallback to OpsEC
        if self._opsec._stealth_available:
            return await self._opsec._execute_stealth_operation(decision, context, security_level)
        elif self._security._threat_available:
            return await self._security._execute_threat_analysis(decision, context, security_level)

        return SecurityResult(
            operation_type='none',
            success=False,
            summary='No security backends available',
            security_level=security_level,
            execution_time=0.0,
            error='No security subsystems initialized',
        )

    async def _execute_stealth_operation(
        self,
        decision: DecisionResponse,
        context: str,
        security_level: SecurityLevel,
    ) -> SecurityResult:
        """Execute stealth operation via OpsECCoordinator."""
        import time as _time
        start_time = _time.time()
        if not self._opsec._stealth_engine:
            raise RuntimeError('StealthEngine not available')
        stealth_result = await self._opsec._stealth_engine.activate_stealth_mode(
            operation_type=context,
            confidence_threshold=decision.confidence,
            security_level=security_level.value,
        )
        execution_time = _time.time() - start_time
        self._opsec._stealth_activations += 1
        self._opsec._stealth_mode_active = stealth_result.get('active', False)
        return SecurityResult(
            operation_type='stealth',
            success=stealth_result.get('success', False),
            summary=f"Stealth: {stealth_result.get('measures_activated', 0)} measures activated",
            security_level=security_level,
            execution_time=execution_time,
            measures_activated=stealth_result.get('measures_activated', 0),
            result_data=stealth_result,
        )

    # ─── OpsEC passthrough ───────────────────────────────────────────────────

    @property
    def _stealth_engine(self) -> Any | None:
        return self._opsec._stealth_engine

    @property
    def _stealth_available(self) -> bool:
        return self._opsec._stealth_available

    @property
    def _stealth_mode_active(self) -> bool:
        return self._opsec._stealth_mode_active

    @property
    def _stealth_activations(self) -> int:
        return self._opsec._stealth_activations

    async def stealth_request_with_jitter(self, **kwargs) -> dict[str, Any]:
        return await self._opsec.stealth_request_with_jitter(**kwargs)

    async def stealth_scrape(self, **kwargs) -> dict[str, Any]:
        return await self._opsec.stealth_scrape(**kwargs)

    async def batch_stealth_requests(self, **kwargs) -> list[dict[str, Any]]:
        return await self._opsec.batch_stealth_requests(**kwargs)

    async def enable_stealth_mode(self, **kwargs) -> dict[str, Any]:
        return await self._opsec.enable_stealth_mode(**kwargs)

    def is_stealth_active(self) -> bool:
        return self._opsec.is_stealth_active()

    async def fetch_with_limit(self, url: str) -> dict[str, Any]:
        return await self._opsec.fetch_with_limit(url)

    def get_browser_fingerprint(self, **kwargs) -> dict[str, Any]:
        return self._opsec.get_browser_fingerprint(**kwargs)

    async def resurrect_from_archive(self, **kwargs) -> dict[str, Any]:
        return await self._opsec.resurrect_from_archive(**kwargs)

    async def establish_vpn_connection(self, **kwargs) -> dict[str, Any]:
        return await self._opsec.establish_vpn_connection(**kwargs)

    async def disconnect_vpn(self) -> dict[str, Any]:
        return await self._opsec.disconnect_vpn()

    async def establish_privacy_connection(self, **kwargs) -> dict[str, Any]:
        return await self._opsec.establish_privacy_connection(**kwargs)

    async def send_anonymous_email(self, **kwargs) -> dict[str, Any]:
        return await self._opsec.send_anonymous_email(**kwargs)

    async def check_data_leaks(self, **kwargs) -> dict[str, Any]:
        return await self._opsec.check_data_leaks(**kwargs)

    async def manage_data_leak_monitoring(self, **kwargs) -> dict[str, Any]:
        return await self._opsec.manage_data_leak_monitoring(**kwargs)

    async def create_pgp_identity(self, **kwargs) -> dict[str, Any]:
        return await self._opsec.create_pgp_identity(**kwargs)

    async def establish_secure_channel(self, **kwargs) -> dict[str, Any]:
        return await self._opsec.establish_secure_channel(**kwargs)

    # ─── Security passthrough ────────────────────────────────────────────────

    @property
    def _threat_intelligence(self) -> Any | None:
        return self._security._threat_intelligence

    @property
    def _threat_available(self) -> bool:
        return self._security._threat_available

    @property
    def _pq_backend(self) -> Any | None:
        return self._security._pq_backend

    @property
    def _crypto_available(self) -> bool:
        return self._security._crypto_available

    @property
    def _zkp_engine(self) -> Any | None:
        return self._security._zkp_engine

    @property
    def _zkp_available(self) -> bool:
        return self._security._zkp_available

    @property
    def _threat_analyses(self) -> int:
        return self._security._threat_analyses

    @property
    def _crypto_operations(self) -> int:
        return self._security._crypto_operations

    @property
    def _zkp_operations(self) -> int:
        return self._security._zkp_operations

    @property
    def _global_threat_level(self) -> float:
        return self._security._global_threat_level

    @property
    def _security_contexts(self) -> dict:
        return self._security._security_contexts

    async def detect_pii(self, **kwargs) -> dict[str, Any]:
        return await self._security.detect_pii(**kwargs)

    async def redact_pii(self, **kwargs) -> dict[str, Any]:
        return await self._security.redact_pii(**kwargs)

    async def sanitize_outbound(self, content: str, force_fallback: bool = False) -> dict[str, Any]:
        return await self._security.sanitize_outbound(content=content, force_fallback=force_fallback)

    async def analyze_threat_intelligence(self, **kwargs) -> dict[str, Any]:
        return await self._security.analyze_threat_intelligence(**kwargs)

    async def correlate_threats(self, **kwargs) -> dict[str, Any]:
        return await self._security.correlate_threats(**kwargs)

    async def threat_intel_lookup(self, **kwargs) -> dict[str, Any]:
        return await self._security.threat_intel_lookup(**kwargs)

    async def create_secure_vault(self, **kwargs) -> dict[str, Any]:
        return await self._security.create_secure_vault(**kwargs)

    # ─── Shared state passthrough ─────────────────────────────────────────────

    def create_security_context(self, operation_id: str, security_level: SecurityLevel) -> SecurityContext:
        return self._security.create_security_context(operation_id, security_level)

    def get_security_context(self, operation_id: str) -> SecurityContext | None:
        return self._security.get_security_context(operation_id)

    def audit_log(self, operation_id: str, event: str, details: dict[str, Any]) -> None:
        self._security.audit_log(operation_id, event, details)

    def get_global_security_state(self) -> dict[str, Any]:
        return {
            'stealth_mode_active': self._opsec._stealth_mode_active,
            **self._security.get_global_security_state(),
            'stealth_activations': self._opsec._stealth_activations,
        }

    def get_threat_level(self) -> float:
        return self._security.get_threat_level()

    def _get_feature_list(self) -> list[str]:
        return self._security._get_feature_list() + self._opsec._get_feature_list()

    def get_available_security_systems(self) -> dict[str, bool]:
        return {**self._security.get_available_security_systems(), **self._opsec.get_available_security_systems()}

    def get_security_stats(self) -> dict[str, int]:
        return {
            **self._security.get_security_stats(),
            'stealth_activations': self._opsec._stealth_activations,
        }

    # ─── execute_comprehensive_security (requires both sub-coordinators) ────────

    async def execute_comprehensive_security(
        self,
        context: str,
        target_security_level: SecurityLevel = SecurityLevel.HIGH,
    ) -> dict[str, Any]:
        """Execute comprehensive multi-layer security operation."""
        results = []
        start_time = time.time()

        # Level 1: Stealth
        if self._opsec._stealth_available and target_security_level.value >= 1:
            try:
                stealth_result = await self._execute_stealth_operation(
                    DecisionResponse(
                        decision_id='comp_stealth',
                        chosen_option='stealth',
                        confidence=0.8,
                        reasoning=context,
                    ),
                    context,
                    SecurityLevel.MINIMAL,
                )
                results.append(stealth_result)
            except Exception as e:
                logger.warning(f'Comprehensive security: Stealth failed: {e}')

        # Level 2: Threat
        if self._security._threat_available and target_security_level.value >= 2:
            try:
                threat_result = await self._security._execute_threat_analysis(
                    DecisionResponse(
                        decision_id='comp_threat',
                        chosen_option='threat',
                        confidence=0.8,
                        reasoning=context,
                    ),
                    context,
                    SecurityLevel.STANDARD,
                )
                results.append(threat_result)
            except Exception as e:
                logger.warning(f'Comprehensive security: Threat analysis failed: {e}')

        # Level 3: Crypto
        if self._security._crypto_available and target_security_level.value >= 3:
            try:
                crypto_result = await self._security._execute_crypto_operation(
                    DecisionResponse(
                        decision_id='comp_crypto',
                        chosen_option='quantum',
                        confidence=0.9,
                        reasoning=context,
                        metadata={'operation': 'key_generation'},
                    ),
                    SecurityLevel.HIGH,
                )
                results.append(crypto_result)
            except Exception as e:
                logger.warning(f'Comprehensive security: Crypto failed: {e}')

        # Level 4: ZKP
        if self._security._zkp_available and target_security_level.value >= 4:
            try:
                zkp_result = await self._security._execute_zkp_operation(
                    DecisionResponse(
                        decision_id='comp_zkp',
                        chosen_option='zkp',
                        confidence=0.95,
                        reasoning=context,
                        metadata={'proof_type': 'identity'},
                    ),
                    context,
                    SecurityLevel.MAXIMUM,
                )
                results.append(zkp_result)
            except Exception as e:
                logger.warning(f'Comprehensive security: ZKP failed: {e}')

        total_time = time.time() - start_time
        successful = sum((1 for r in results if r.success))
        return {
            'success': successful > 0,
            'summary': f'Comprehensive security: {successful}/{len(results)} layers active',
            'target_level': target_security_level.value,
            'layers_activated': successful,
            'total_layers': len(results),
            'execution_time': total_time,
            'stealth_active': any(
                r.operation_type == 'stealth' and r.success for r in results
            ),
            'threats_detected': sum(
                r.threats_found for r in results if r.operation_type == 'threat'
            ),
            'results': [
                {
                    'type': r.operation_type,
                    'success': r.success,
                    'summary': r.summary,
                    'level': r.security_level.value,
                }
                for r in results
            ],
        }
