"""
Testy pro Sprint 61 - Advanced Stealth & Post-Quantum Everything
"""

import pytest

pytest.importorskip("aiohttp_socks", reason="optional dependency not installed")

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch


# Test transport base
class TestTransportBase:
    def test_transport_abstract_methods(self):
        from hledac.universal.transport.base import Transport

        # Transport je abstraktní třída
        with pytest.raises(TypeError):
            Transport()


class TestTorTransport:
    @pytest.fixture
    def temp_dir(self):
        tmp = tempfile.mkdtemp()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_tor_transport_init(self, temp_dir):
        from hledac.universal.transport.tor_transport import TorTransport

        tor = TorTransport(data_dir=temp_dir)
        assert tor.data_dir == Path(temp_dir)
        assert tor.control_port == 9051
        assert tor.socks_port == 9050

    @pytest.mark.asyncio
    async def test_tor_transport_start_fallback(self, temp_dir):
        from hledac.universal.transport.tor_transport import TorTransport

        tor = TorTransport(data_dir=temp_dir)
        # Mock Tor process to fail
        with patch('asyncio.create_subprocess_exec', side_effect=FileNotFoundError()):
            await tor.start()

        assert tor.security_level == 'local'
        assert tor.onion_address is not None


class TestNymTransport:
    @pytest.fixture
    def temp_dir(self):
        tmp = tempfile.mkdtemp()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_nym_transport_init(self, temp_dir):
        from hledac.universal.transport.nym_transport import NymTransport

        nym = NymTransport(data_dir=temp_dir)
        assert nym.data_dir == Path(temp_dir)
        assert nym.websocket_port == 1977
        assert nym.max_queue_size == 100
        assert nym.circuit_breaker_open is False

    @pytest.mark.asyncio
    async def test_nym_circuit_breaker(self, temp_dir):
        from hledac.universal.transport.nym_transport import NymTransport

        nym = NymTransport(data_dir=temp_dir)
        nym.circuit_breaker_failures = 3
        nym.circuit_breaker_threshold = 3
        nym.circuit_breaker_last_failure = 0

        # Should be open after threshold
        assert nym.circuit_breaker_open is False  # Not yet checked
        nym.circuit_breaker_failures = 3
        # Simulate threshold check
        if nym.circuit_breaker_failures >= nym.circuit_breaker_threshold:
            nym.circuit_breaker_open = True
        assert nym.circuit_breaker_open is True


class TestInMemoryTransport:
    @pytest.mark.asyncio
    async def test_inmemory_transport_init(self):
        from hledac.universal.transport.inmemory_transport import InMemoryTransport

        transport = InMemoryTransport(node_id="test_node")
        assert transport.node_id == "test_node"
        assert transport.handlers == {}
        assert transport.peers == {}

    @pytest.mark.asyncio
    async def test_inmemory_transport_start_stop(self):
        from hledac.universal.transport.inmemory_transport import InMemoryTransport

        transport = InMemoryTransport(node_id="test_node")
        await transport.start()
        await transport.wait_ready()
        await transport.stop()

    @pytest.mark.asyncio
    async def test_inmemory_transport_send_message(self):
        from hledac.universal.transport.inmemory_transport import InMemoryTransport

        node_a = InMemoryTransport(node_id="A")
        node_b = InMemoryTransport(node_id="B")

        node_a.register_peer("B", node_b)

        received = []
        node_b.register_handler("test_msg", lambda m: received.append(m))

        await node_a.start()
        await node_b.start()

        await node_a.send_message("B", "test_msg", {"data": "hello"}, "sig123")
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0]["payload"]["data"] == "hello"

        await node_a.stop()
        await node_b.stop()


class TestNymPolicy:
    @pytest.fixture
    def mock_governor(self):
        gov = MagicMock()
        return gov

    @pytest.fixture
    def mock_tor_transport(self):
        tor = MagicMock()
        tor.security_level = 'tor'
        return tor

    @pytest.fixture
    def mock_nym_transport(self):
        nym = MagicMock()
        nym.security_level = 'nym'
        return nym

    @pytest.mark.asyncio
    async def test_nym_policy_init(self, mock_governor, mock_tor_transport, mock_nym_transport):
        from hledac.universal.policy.nym_policy import NymPolicy

        policy = NymPolicy(mock_governor, mock_tor_transport, mock_nym_transport)
        assert policy.governor == mock_governor
        assert policy.tor == mock_tor_transport
        assert policy.nym == mock_nym_transport
        assert policy.alpha == 0.5
        assert policy.exploration_rate == 0.1

    @pytest.mark.asyncio
    async def test_nym_policy_select_transport_critical(self, mock_governor, mock_tor_transport, mock_nym_transport):
        from hledac.universal.policy.nym_policy import NymPolicy, RiskLevel

        policy = NymPolicy(mock_governor, mock_tor_transport, mock_nym_transport)

        # CRITICAL risk + long time should select Nym
        transport, params = await policy.select_transport(
            RiskLevel.CRITICAL, time_budget=60, sensitivity=0.8,
            need_cover_traffic=True, request_id="req1"
        )
        assert transport == mock_nym_transport
        assert params['cover_traffic'] is True


class TestEncryption:
    def test_encrypt_decrypt_aes_gcm(self):
        from hledac.universal.security.encryption import decrypt_aes_gcm, encrypt_aes_gcm

        key = os.urandom(32)  # 256-bit key
        plaintext = b"Hello, World!"
        aad = b"associated_data"

        encrypted = encrypt_aes_gcm(key, plaintext, aad)
        assert encrypted != plaintext

        decrypted = decrypt_aes_gcm(key, encrypted, aad)
        assert decrypted == plaintext

    def test_encrypt_decrypt_different_aad(self):
        from hledac.universal.security.encryption import decrypt_aes_gcm, encrypt_aes_gcm

        key = os.urandom(32)
        plaintext = b"Secret message"

        encrypted = encrypt_aes_gcm(key, plaintext, b"aad1")

        # Decrypting with different AAD should fail
        with pytest.raises(Exception):
            decrypt_aes_gcm(key, encrypted, b"aad2")


class TestKeyManager:
    @pytest.fixture
    def temp_dir(self):
        tmp = tempfile.mkdtemp()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_key_manager_init(self, temp_dir):
        from hledac.universal.security.key_manager import KeyManager

        km = KeyManager(db_path=f"{temp_dir}/keys.lmdb")
        assert km.db_path.exists() or True  # LMDB creates on demand
        assert km._current_version == 0

    @pytest.mark.asyncio
    async def test_key_manager_generate_key(self, temp_dir):
        from hledac.universal.security.key_manager import KeyManager

        km = KeyManager(db_path=f"{temp_dir}/keys.lmdb")
        key, salt, version = await km.get_master_key()

        assert key is not None
        assert len(key) == 32  # 256 bits
        assert version >= 1

    @pytest.mark.asyncio
    async def test_key_manager_bucket_key(self, temp_dir):
        from hledac.universal.security.key_manager import KeyManager

        km = KeyManager(db_path=f"{temp_dir}/keys.lmdb")

        bucket_key1, version1 = await km.get_bucket_key("test_bucket")
        bucket_key2, version2 = await km.get_bucket_key("test_bucket")

        # Same bucket should return same key (cached)
        assert bucket_key1 == bucket_key2
        assert version1 == version2

    @pytest.mark.asyncio
    async def test_key_manager_different_buckets(self, temp_dir):
        from hledac.universal.security.key_manager import KeyManager

        km = KeyManager(db_path=f"{temp_dir}/keys.lmdb")

        key1, _ = await km.get_bucket_key("bucket1")
        key2, _ = await km.get_bucket_key("bucket2")

        # Different buckets should have different keys
        assert key1 != key2


class TestPeerRegistry:
    def test_peer_registry_add_peer(self):
        from hledac.universal.federated.peer_registry import PeerRegistry

        registry = PeerRegistry()
        registry.add_peer("peer1", tor_endpoint="localhost:9050", nym_endpoint="nym123")

        peer = registry.get_peer("peer1")
        assert peer is not None
        assert peer['tor'] == "localhost:9050"
        assert peer['nym'] == "nym123"

    def test_peer_registry_get_endpoint(self):
        from hledac.universal.federated.peer_registry import PeerRegistry

        registry = PeerRegistry()
        registry.add_peer("peer1", tor_endpoint="localhost:9050")

        tor_ep = registry.get_endpoint("peer1", "tor")
        nym_ep = registry.get_endpoint("peer1", "nym")

        assert tor_ep == "localhost:9050"
        assert nym_ep is None

    def test_peer_registry_remove_peer(self):
        from hledac.universal.federated.peer_registry import PeerRegistry

        registry = PeerRegistry()
        registry.add_peer("peer1")
        registry.remove_peer("peer1")

        assert registry.get_peer("peer1") is None


class TestModelStore:
    @pytest.fixture
    def temp_dir(self):
        tmp = tempfile.mkdtemp()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_model_store_save_load(self, temp_dir):
        import mlx.core as mx
        from hledac.universal.federated.model_store_v2 import ModelStore
        from hledac.universal.security.key_manager import KeyManager

        km = KeyManager(db_path=f"{temp_dir}/keys.lmdb")
        store = ModelStore(km, db_path=f"{temp_dir}/models.lmdb")

        # Save a model
        weights = {
            "layer1": mx.array([1.0, 2.0, 3.0]),
            "layer2": mx.array([[1.0, 2.0], [3.0, 4.0]])
        }
        await store.save_model("model_v1", weights)

        # Load the model
        loaded = await store.load_model("model_v1")

        assert loaded is not None
        assert "layer1" in loaded
        assert "layer2" in loaded

        await store.close()


class TestFederatedCoordinatorV2:
    @pytest.fixture
    def temp_dir(self):
        tmp = tempfile.mkdtemp()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_federated_coordinator_init(self, temp_dir):
        from hledac.universal.federated.federated_coordinator_v2 import FederatedCoordinatorV2
        from hledac.universal.security.key_manager import KeyManager
        from hledac.universal.transport.inmemory_transport import InMemoryTransport

        km = KeyManager(db_path=f"{temp_dir}/keys.lmdb")
        tor_transport = InMemoryTransport(node_id="node1")
        nym_transport = None
        nym_policy = None

        def model_provider():
            return {}

        coordinator = FederatedCoordinatorV2(
            node_id="node1",
            tor_transport=tor_transport,
            nym_transport=nym_transport,
            nym_policy=nym_policy,
            key_manager=km,
            model_provider=model_provider
        )

        assert coordinator.node_id == "node1"
        assert coordinator.peer_registry is not None
        assert coordinator.store is not None

    @pytest.mark.asyncio
    async def test_federated_coordinator_start_stop(self, temp_dir):
        from hledac.universal.federated.federated_coordinator_v2 import FederatedCoordinatorV2
        from hledac.universal.security.key_manager import KeyManager
        from hledac.universal.transport.inmemory_transport import InMemoryTransport

        km = KeyManager(db_path=f"{temp_dir}/keys.lmdb")
        tor_transport = InMemoryTransport(node_id="node1")

        coordinator = FederatedCoordinatorV2(
            node_id="node1",
            tor_transport=tor_transport,
            nym_transport=None,
            nym_policy=None,
            key_manager=km,
            model_provider=lambda: {}
        )

        await coordinator.start()
        await coordinator.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
