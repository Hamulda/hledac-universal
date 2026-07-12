import os
import secrets
import time
from dataclasses import dataclass
import msgspec
from typing import Any
import numpy as np
if not os.getenv('HLEDAC_ENABLE_NEURO_CRYPTO'):
    raise ImportError('NeuromorphicCryptoEngine requires HLEDAC_ENABLE_NEURO_CRYPTO=1. This module is EXPERIMENTAL and has not been security-reviewed.')
import base64
import hashlib
import logging
logger = logging.getLogger(__name__)

class SpikingNeuralNetwork:
    """Minimal SNN for cryptographic operations."""
    __slots__ = tuple(('_initialized', '_weights_hidden', '_weights_input', 'hidden_neurons', 'input_neurons', 'output_neurons'))

    def __init__(self, input_neurons: int=256, hidden_neurons: int=512, output_neurons: int=256):
        self.input_neurons = input_neurons
        self.hidden_neurons = hidden_neurons
        self.output_neurons = output_neurons
        self._weights_input = None
        self._weights_hidden = None
        self._initialized = False

    def initialize(self):
        """Initialize network weights lazily."""
        if self._initialized:
            return
        self._weights_input = np.random.randn(self.hidden_neurons, self.input_neurons).astype(np.float32) * np.sqrt(2.0 / self.input_neurons)
        self._weights_hidden = np.random.randn(self.output_neurons, self.hidden_neurons).astype(np.float32) * np.sqrt(2.0 / self.hidden_neurons)
        self._initialized = True

    def process(self, neural_input: np.ndarray) -> np.ndarray:
        """Process input through the network."""
        if not self._initialized:
            self.initialize()
        assert self._weights_input is not None and self._weights_hidden is not None
        hidden = np.tanh(np.dot(self._weights_input, neural_input))
        output = np.tanh(np.dot(self._weights_hidden, hidden))
        return output.astype(np.float32)

    def cleanup(self):
        """Clean up memory."""
        self._weights_input = None
        self._weights_hidden = None
        self._initialized = False

class IzhikevichNeuron:
    """
    Izhikevich neuron model - computationally efficient yet biologically plausible.
    Capable of reproducing many types of cortical neuron spiking behaviors.
    """
    __slots__ = tuple(('a', 'b', 'c', 'd', 'last_spike_time', 'spike_times', 'u', 'v'))

    def __init__(self, a: float=0.02, b: float=0.2, c: float=-65.0, d: float=8.0, v_init: float=-70.0):
        self.a = a
        self.b = b
        self.c = c
        self.d = d
        self.v = v_init
        self.u = b * v_init
        self.spike_times: list[float] = []
        self.last_spike_time = -float('inf')

    def update(self, i_val: float, dt: float=1.0) -> bool:
        """Update neuron state with input current."""
        dv = (0.04 * self.v ** 2 + 5 * self.v + 140 - self.u + i_val) * dt
        du = self.a * (self.b * self.v - self.u) * dt
        self.v += dv
        self.u += du
        if self.v >= 30.0:
            self.v = self.c
            self.u += self.d
            self.last_spike_time = time.time()
            self.spike_times.append(self.last_spike_time)
            return True
        return False

    def reset(self):
        """Reset neuron to initial state."""
        self.v = -70.0
        self.u = self.b * self.v
        self.spike_times.clear()
        self.last_spike_time = -float('inf')

class HodgkinHuxleyNeuron:
    """
    Hodgkin-Huxley neuron model - biophysically accurate.
    Models action potentials using ion channel dynamics.
    """
    __slots__ = tuple(('C_m', 'E_K', 'E_L', 'E_Na', 'V', 'g_K', 'g_L', 'g_Na', 'h', 'last_spike_time', 'm', 'n', 'spike_times'))

    def __init__(self):
        self.C_m = 1.0
        self.g_Na = 120.0
        self.g_K = 36.0
        self.g_L = 0.3
        self.E_Na = 50.0
        self.E_K = -77.0
        self.E_L = -54.387
        self.V = -65.0
        self.m = 0.05
        self.h = 0.6
        self.n = 0.32
        self.spike_times: list[float] = []
        self.last_spike_time = -float('inf')

    def _alpha_m(self, V: float) -> float:
        return 0.1 * (V + 40) / (1 - np.exp(-(V + 40) / 10))

    def _beta_m(self, V: float) -> float:
        return 4.0 * np.exp(-(V + 65) / 18)

    def _alpha_h(self, V: float) -> float:
        return 0.07 * np.exp(-(V + 65) / 20)

    def _beta_h(self, V: float) -> float:
        return 1.0 / (1 + np.exp(-(V + 35) / 10))

    def _alpha_n(self, V: float) -> float:
        return 0.01 * (V + 55) / (1 - np.exp(-(V + 55) / 10))

    def _beta_n(self, V: float) -> float:
        return 0.125 * np.exp(-(V + 65) / 80)

    def update(self, i_val: float, dt: float=0.01) -> bool:
        """Update neuron state."""
        I_Na = self.g_Na * self.m ** 3 * self.h * (self.V - self.E_Na)
        I_K = self.g_K * self.n ** 4 * (self.V - self.E_K)
        I_L = self.g_L * (self.V - self.E_L)
        dV = (i_val - I_Na - I_K - I_L) / self.C_m * dt
        self.V += dV
        dm = (self._alpha_m(self.V) * (1 - self.m) - self._beta_m(self.V) * self.m) * dt
        dh = (self._alpha_h(self.V) * (1 - self.h) - self._beta_h(self.V) * self.h) * dt
        dn = (self._alpha_n(self.V) * (1 - self.n) - self._beta_n(self.V) * self.n) * dt
        self.m += dm
        self.h += dh
        self.n += dn
        if self.V >= 30.0:
            self.last_spike_time = time.time()
            self.spike_times.append(self.last_spike_time)
            return True
        return False

    def reset(self):
        """Reset neuron to initial state."""
        self.V = -65.0
        self.m = 0.05
        self.h = 0.6
        self.n = 0.32
        self.spike_times.clear()
        self.last_spike_time = -float('inf')

class SpikePatternTemplate:
    """Spike pattern templates for cryptographic operations."""
    __slots__ = tuple(('num_neurons', 'pattern_type', 'template'))

    def __init__(self, pattern_type: str, num_neurons: int=100):
        self.pattern_type = pattern_type
        self.num_neurons = num_neurons
        self.template = self._create_template()

    def _create_template(self) -> np.ndarray:
        """Create spike pattern template."""
        if self.pattern_type == 'hash':
            return np.random.rand(self.num_neurons) * 0.5 + 0.1
        elif self.pattern_type == 'encryption':
            return np.ones(self.num_neurons) * 0.8
        elif self.pattern_type == 'signature':
            return np.random.randn(self.num_neurons) * 0.3 + 0.5
        else:
            return np.random.rand(self.num_neurons) * 0.5

    def generate_spikes(self, data_hash: bytes) -> list[int]:
        """Generate spike pattern based on data hash."""
        np.random.seed(int(hashlib.sha256(data_hash).hexdigest()[:8], 16))
        spike_probs = self.template + np.random.randn(self.num_neurons) * 0.1
        return [i for i, p in enumerate(spike_probs) if np.random.rand() < p]

class BurstDetector:
    """Detects burst patterns in spike trains."""
    __slots__ = tuple(('burst_threshold', 'bursts', 'max_isi_ms'))

    def __init__(self, burst_threshold: int=3, max_isi_ms: float=10.0):
        self.burst_threshold = burst_threshold
        self.max_isi_ms = max_isi_ms
        self.bursts: list[list[float]] = []

    def detect_bursts(self, spike_times: list[float]) -> list[list[float]]:
        """Detect bursts in spike train."""
        if len(spike_times) < 2:
            return []
        self.bursts = []
        current_burst = [spike_times[0]]
        for i in range(1, len(spike_times)):
            isi = (spike_times[i] - spike_times[i - 1]) * 1000
            if isi <= self.max_isi_ms:
                current_burst.append(spike_times[i])
            else:
                if len(current_burst) >= self.burst_threshold:
                    self.bursts.append(current_burst.copy())
                current_burst = [spike_times[i]]
        if len(current_burst) >= self.burst_threshold:
            self.bursts.append(current_burst)
        return self.bursts

    def get_burst_rate(self, time_window_s: float=1.0) -> float:
        """Calculate burst rate in Hz."""
        if not self.bursts or time_window_s <= 0:
            return 0.0
        return len(self.bursts) / time_window_s

class TemporalPatternAnalyzer:
    """Analyzes temporal patterns in neural activity."""
    __slots__ = tuple(('cv_history', 'isi_history'))

    def __init__(self):
        self.isi_history: list[float] = []
        self.cv_history: list[float] = []

    def analyze(self, spike_times: list[float]) -> dict[str, float]:
        """Analyze temporal patterns in spike train."""
        if len(spike_times) < 2:
            return {'mean_rate': 0.0, 'cv_isi': 0.0, 'burst_index': 0.0}
        isis = [(spike_times[i] - spike_times[i - 1]) * 1000 for i in range(1, len(spike_times))]
        self.isi_history.extend(isis)
        mean_isi = float(np.mean(isis))
        std_isi = float(np.std(isis))
        cv_isi = std_isi / mean_isi if mean_isi > 0 else 0.0
        self.cv_history.append(cv_isi)
        duration = spike_times[-1] - spike_times[0]
        mean_rate = float(len(spike_times) / duration) if duration > 0 else 0.0
        short_isis = sum((1 for isi in isis if isi < 10.0))
        burst_index = short_isis / len(isis) if isis else 0.0
        return {'mean_rate_hz': mean_rate, 'cv_isi': cv_isi, 'burst_index': burst_index, 'mean_isi_ms': mean_isi, 'std_isi_ms': std_isi}

class EntropyPool:
    """
    Entropy pool for cryptographic operations.
    Collects and manages entropy from multiple sources.
    """
    __slots__ = tuple(('_entropy_data', '_entropy_estimate', '_reseed_count', 'pool_size', 'reseed_threshold'))

    def __init__(self, pool_size: int=1024, reseed_threshold: int=512):
        from collections import deque
        self.pool_size = pool_size
        self.reseed_threshold = reseed_threshold
        self._entropy_data: deque = deque(maxlen=pool_size)
        self._entropy_estimate = 0.0
        self._reseed_count = 0

    def add_entropy(self, source: str, entropy_bytes: bytes) -> None:
        """Add entropy from a specific source to the pool."""
        source_hash = hashlib.sha256(source.encode()).digest()
        for i, byte in enumerate(entropy_bytes):
            mixed_byte = byte ^ source_hash[i % len(source_hash)]
            self._entropy_data.append(mixed_byte)
        self._entropy_estimate = min(1.0, len(self._entropy_data) / self.pool_size)
        if len(self._entropy_data) >= self.reseed_threshold:
            self._reseed()

    def extract_entropy(self, length: int) -> bytes:
        """Extract entropy bytes from the pool."""
        if len(self._entropy_data) < length:
            additional = secrets.token_bytes(length - len(self._entropy_data))
            for byte in additional:
                self._entropy_data.append(byte)
        result = bytearray()
        for _ in range(length):
            if self._entropy_data:
                result.append(self._entropy_data.popleft())
            else:
                result.append(secrets.randbelow(256))
        return bytes(result)

    def get_entropy_estimate(self) -> float:
        """Get current entropy pool fullness estimate (0.0 - 1.0)."""
        return self._entropy_estimate

    def _reseed(self) -> None:
        """Internal reseed operation to mix pool entropy."""
        if len(self._entropy_data) < 32:
            return
        current_bytes = bytes(list(self._entropy_data))
        mixed = hashlib.sha256(current_bytes).digest()
        self._entropy_data.clear()
        for byte in mixed:
            self._entropy_data.append(byte)
        system_entropy = secrets.token_bytes(32)
        for byte in system_entropy:
            self._entropy_data.append(byte)
        self._reseed_count += 1
        logger.debug(f'EntropyPool reseeded (count: {self._reseed_count})')

@dataclass(slots=True)
class SNNEncryptedContainer:
    """Container for SNN-based encrypted data with neural signatures."""
    ciphertext: bytes
    neural_signature: np.ndarray
    key_id: str
    timestamp: float
    entropy_used: float

    def __post_init__(self) -> None:
        if self.timestamp == 0:
            self.timestamp = time.time()

    def to_dict(self) -> dict[str, Any]:
        """Export as dictionary with numpy array handling."""
        return {'ciphertext': base64.b64encode(self.ciphertext).decode(), 'neural_signature': base64.b64encode(self.neural_signature.tobytes()).decode(), 'key_id': self.key_id, 'timestamp': self.timestamp, 'entropy_used': self.entropy_used}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SNNEncryptedContainer:
        """Import from dictionary."""
        sig_bytes = base64.b64decode(data['neural_signature'])
        neural_signature = np.frombuffer(sig_bytes, dtype=np.float32)
        return cls(ciphertext=base64.b64decode(data['ciphertext']), neural_signature=neural_signature, key_id=data['key_id'], timestamp=data['timestamp'], entropy_used=data['entropy_used'])

class NeuromorphicCryptoEngine:
    """
    Cryptography Engine using Neuromorphic Computing.
    Implements encryption/decryption using spiking neural networks
    with hardware entropy integration and M1 8GB optimization.

    EXPERIMENTAL: Not for production use. Not security-reviewed.
    Requires HLEDAC_EXPERIMENTAL_NEURO_CRYPTO=1 to instantiate.
    """
    __slots__ = tuple(('_active_keys', '_crypto_weights', '_entropy_pool', '_initialized', '_key_neurons', '_neural_network', 'hidden_neurons', 'input_neurons', 'output_neurons'))

    def __init__(self, input_neurons: int=256, hidden_neurons: int=512, output_neurons: int=256):
        assert os.environ.get('HLEDAC_EXPERIMENTAL_NEURO_CRYPTO') == '1', 'NeuromorphicCryptoEngine is EXPERIMENTAL and not security-reviewed. Set HLEDAC_EXPERIMENTAL_NEURO_CRYPTO=1 to enable.'
        self.input_neurons = input_neurons
        self.hidden_neurons = hidden_neurons
        self.output_neurons = output_neurons
        self._neural_network: SpikingNeuralNetwork | None = None
        self._entropy_pool: EntropyPool | None = None
        self._crypto_weights: np.ndarray | None = None
        self._key_neurons: dict[str, str] = {}
        self._active_keys: dict[str, dict[str, Any]] = {}
        self._initialized = False

    async def initialize(self) -> bool:
        """Initialize the crypto engine with lazy loading."""
        try:
            self._entropy_pool = EntropyPool(pool_size=1024, reseed_threshold=512)
            initial_entropy = secrets.token_bytes(64)
            self._entropy_pool.add_entropy('system', initial_entropy)
            self._neural_network = SpikingNeuralNetwork(input_neurons=self.input_neurons, hidden_neurons=self.hidden_neurons, output_neurons=self.output_neurons)
            self._crypto_weights = np.random.randn(self.output_neurons, self.output_neurons).astype(np.float32) * 0.1
            self._initialized = True
            logger.info(f'NeuromorphicCryptoEngine initialized ({self.input_neurons} -> {self.hidden_neurons} -> {self.output_neurons})')
            return True
        except Exception as e:
            logger.error(f'NeuromorphicCryptoEngine initialization failed: {e}')
            return False

    def _initialize_network(self) -> None:
        """Lazy initialization of SNN layers."""
        if self._neural_network is None:
            self._neural_network = SpikingNeuralNetwork(input_neurons=self.input_neurons, hidden_neurons=self.hidden_neurons, output_neurons=self.output_neurons)
        self._neural_network.initialize()

    def encrypt(self, data: bytes, key_id: str | None=None) -> SNNEncryptedContainer:
        """Encrypt data using SNN-based transformation."""
        if not self._initialized:
            raise RuntimeError('NeuromorphicCryptoEngine not initialized')
        if key_id is None:
            key_id = self._generate_key_id()
        if key_id not in self._key_neurons:
            self._register_key_neurons(key_id)
        self._initialize_network()
        neural_input = self._encode_data_to_neural(data, key_id)
        assert self._neural_network is not None and self._crypto_weights is not None
        neural_output = self._neural_network.process(neural_input)
        crypto_output = np.dot(self._crypto_weights, neural_output)
        keystream = self._generate_keystream(crypto_output, len(data))
        ciphertext = bytearray(len(data))
        for i, (d, k) in enumerate(zip(data, keystream)):
            ciphertext[i] = d ^ k
        neural_signature = crypto_output.copy()
        if self._entropy_pool:
            entropy_data = neural_output.tobytes()[:32]
            self._entropy_pool.add_entropy('neural_op', entropy_data)
        return SNNEncryptedContainer(ciphertext=bytes(ciphertext), neural_signature=neural_signature, key_id=key_id, timestamp=time.time(), entropy_used=len(entropy_data) if self._entropy_pool else 0)

    def decrypt(self, ciphertext: SNNEncryptedContainer) -> bytes:
        """Decrypt data using neural decryption."""
        if not self._initialized:
            raise RuntimeError('NeuromorphicCryptoEngine not initialized')
        key_id = ciphertext.key_id
        if key_id not in self._key_neurons:
            raise ValueError(f'Key {key_id} not found')
        self._initialize_network()
        neural_output = ciphertext.neural_signature
        assert self._crypto_weights is not None
        inverse_weights = np.linalg.pinv(self._crypto_weights)
        np.dot(inverse_weights, neural_output)
        keystream = self._generate_keystream(neural_output, len(ciphertext.ciphertext))
        plaintext = bytearray(len(ciphertext.ciphertext))
        for i, (c, k) in enumerate(zip(ciphertext.ciphertext, keystream)):
            plaintext[i] = c ^ k
        return bytes(plaintext)

    def generate_signature(self, data: bytes, key_id: str | None=None) -> bytes:
        """Generate high-entropy neural signature for data integrity."""
        if not self._initialized:
            raise RuntimeError('NeuromorphicCryptoEngine not initialized')
        if key_id is None:
            key_id = list(self._key_neurons.keys())[0] if self._key_neurons else self._generate_key_id()
        if key_id not in self._key_neurons:
            self._register_key_neurons(key_id)
        self._initialize_network()
        neural_input = self._encode_data_to_neural(data, key_id)
        assert self._neural_network is not None
        neural_output = self._neural_network.process(neural_input)
        sig_hash = hashlib.sha256(neural_output.tobytes() + data).digest()
        if self._entropy_pool:
            pool_entropy = self._entropy_pool.extract_entropy(32)
            sig_hash = hashlib.sha256(sig_hash + pool_entropy).digest()
        return sig_hash

    def verify_signature(self, data: bytes, signature: bytes, key_id: str | None=None) -> bool:
        """Verify neural signature."""
        try:
            expected_sig = self.generate_signature(data, key_id)
            return secrets.compare_digest(signature, expected_sig)
        except Exception:
            return False

    def get_entropy_pool(self) -> EntropyPool | None:
        """Get the entropy pool for cryptographic randomness."""
        return self._entropy_pool

    def _encode_data_to_neural(self, data: bytes, key_context: str) -> np.ndarray:
        """Encode data bytes to neural activation pattern."""
        hash_obj = hashlib.sha256(data + key_context.encode())
        hash_bytes = hash_obj.digest()
        neural_pattern = np.zeros(self.input_neurons, dtype=np.float32)
        for i, byte in enumerate(hash_bytes):
            idx = i % self.input_neurons
            neural_pattern[idx] = (neural_pattern[idx] + byte / 255.0) / 2.0
        if self._entropy_pool:
            entropy = self._entropy_pool.extract_entropy(32)
            for i, byte in enumerate(entropy):
                idx = i % self.input_neurons
                neural_pattern[idx] = (neural_pattern[idx] + byte / 255.0) / 2.0
        return neural_pattern

    def _generate_keystream(self, neural_output: np.ndarray, length: int) -> bytes:
        """Generate SNN-based keystream for encryption."""
        keystream = bytearray()
        output_bytes = neural_output.tobytes()
        while len(keystream) < length:
            next_hash = hashlib.sha256(output_bytes + bytes(keystream)).digest()
            keystream.extend(next_hash)
            output_bytes = next_hash
        return bytes(keystream[:length])

    def _generate_key_id(self) -> str:
        """Generate unique key ID."""
        return f'neuro_key_{time.time_ns()}_{secrets.token_hex(4)}'

    def _register_key_neurons(self, key_id: str) -> None:
        """Register key neurons for a key ID."""
        neuron_id = f'neuron_{key_id}'
        self._key_neurons[key_id] = neuron_id
        self._active_keys[key_id] = {'neuron_id': neuron_id, 'created_at': time.time(), 'key_size': self.input_neurons}

    def cleanup(self) -> None:
        """Clean up memory (M1 8GB optimization)."""
        if self._neural_network:
            self._neural_network.cleanup()
            self._neural_network = None
        self._crypto_weights = None
        self._entropy_pool = None
        logger.info('NeuromorphicCryptoEngine memory cleaned up')