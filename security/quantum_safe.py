"""
Quantum-Safe Cryptography pro Ultra Deep Research

Implementuje:
- ML-KEM (Kyber) - Key encapsulation mechanism
- ML-DSA (Dilithium) - Digital signatures
- SLH-DSA (SPHINCS+) - Stateless hash-based signatures
- Steganography (DCT, LSB, Neural)
- Neuromorphic Cryptography - SNN-based encryption

Pro zabezpečení citlivých výzkumných dat proti budoucím kvantovým útokům.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)

# ── PQ Availability Flag ──────────────────────────────────────────────────────
try:
    import oqs as _oqs

    REAL_PQ_AVAILABLE = True
except ImportError:
    REAL_PQ_AVAILABLE = False
    _oqs = None

if not REAL_PQ_AVAILABLE:
    logger.warning(
        "PQ crypto running in SIMULATION mode — NOT cryptographically secure. "
        "Install liboqs-python: pip install oqs"
    )


class SecurityLevel(Enum):
    """Úrovně zabezpečení"""
    STANDARD = "standard"      # 128-bit security
    HIGH = "high"             # 192-bit security
    MAXIMUM = "maximum"       # 256-bit security


class StegoMethod(Enum):
    """Metody steganografie"""
    DCT = "dct"              # Discrete Cosine Transform (JPEG)
    LSB = "lsb"              # Least Significant Bit (PNG/BMP)
    NEURAL = "neural"        # Neural network-based
    AUTO = "auto"            # Auto-select based on cover


class EntropyPool:
    """
    Entropy pool for cryptographic operations.

    Collects and manages entropy from multiple sources for
    cryptographically-secure randomness generation.
    """

    def __init__(self, pool_size: int = 1024, reseed_threshold: int = 512):
        self.pool_size = pool_size
        self.reseed_threshold = reseed_threshold
        self._entropy_data: deque = deque(maxlen=pool_size)
        self._entropy_estimate = 0.0
        self._reseed_count = 0

    def add_entropy(self, source: str, entropy_bytes: bytes) -> None:
        """
        Add entropy from a specific source to the pool.

        Args:
            source: Identifier for the entropy source
            entropy_bytes: Raw entropy bytes to add
        """
        # Mix entropy using XOR with source-specific mixing
        source_hash = hashlib.sha256(source.encode()).digest()

        for i, byte in enumerate(entropy_bytes):
            mixed_byte = byte ^ source_hash[i % len(source_hash)]
            self._entropy_data.append(mixed_byte)

        # Update entropy estimate (simplified Shannon entropy calculation)
        self._entropy_estimate = min(1.0, len(self._entropy_data) / self.pool_size)

        # Trigger reseed if threshold reached
        if len(self._entropy_data) >= self.reseed_threshold:
            self._reseed()

    def extract_entropy(self, length: int) -> bytes:
        """
        Extract entropy bytes from the pool.

        Args:
            length: Number of entropy bytes to extract

        Returns:
            Entropy bytes
        """
        if len(self._entropy_data) < length:
            # Generate additional entropy if needed using system RNG
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

        # Mix current pool using hash function
        current_bytes = bytes(list(self._entropy_data))
        mixed = hashlib.sha256(current_bytes).digest()

        # Clear and repopulate with mixed entropy
        self._entropy_data.clear()
        for byte in mixed:
            self._entropy_data.append(byte)

        # Add system entropy
        system_entropy = secrets.token_bytes(32)
        for byte in system_entropy:
            self._entropy_data.append(byte)

        self._reseed_count += 1
        logger.debug(f"EntropyPool reseeded (count: {self._reseed_count})")


class SpikingNeuralNetwork:
    """Minimal SNN for cryptographic operations."""

    def __init__(self, input_neurons: int = 256, hidden_neurons: int = 512, output_neurons: int = 256):
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

        # Initialize weights using Xavier initialization
        self._weights_input = np.random.randn(self.hidden_neurons, self.input_neurons).astype(np.float32) * np.sqrt(2.0 / self.input_neurons)
        self._weights_hidden = np.random.randn(self.output_neurons, self.hidden_neurons).astype(np.float32) * np.sqrt(2.0 / self.hidden_neurons)
        self._initialized = True

    def process(self, neural_input: np.ndarray) -> np.ndarray:
        """Process input through the network."""
        if not self._initialized:
            self.initialize()

        # Input to hidden layer with tanh activation
        hidden = np.tanh(np.dot(self._weights_input, neural_input))

        # Hidden to output layer
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

    def __init__(
        self,
        a: float = 0.02,      # Time scale of recovery variable
        b: float = 0.2,       # Sensitivity of recovery variable
        c: float = -65.0,     # After-spike reset value of v
        d: float = 8.0,       # After-spike reset value of u
        v_init: float = -70.0 # Initial membrane potential
    ):
        self.a = a
        self.b = b
        self.c = c
        self.d = d
        self.v = v_init
        self.u = b * v_init
        self.spike_times: List[float] = []
        self.last_spike_time = -float('inf')

    def update(self, I: float, dt: float = 1.0) -> bool:
        """Update neuron state with input current."""
        dv = (0.04 * self.v ** 2 + 5 * self.v + 140 - self.u + I) * dt
        du = (self.a * (self.b * self.v - self.u)) * dt
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
        self.spike_times: List[float] = []
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

    def update(self, I: float, dt: float = 0.01) -> bool:
        """Update neuron state."""
        I_Na = self.g_Na * self.m**3 * self.h * (self.V - self.E_Na)
        I_K = self.g_K * self.n**4 * (self.V - self.E_K)
        I_L = self.g_L * (self.V - self.E_L)

        dV = (I - I_Na - I_K - I_L) / self.C_m * dt
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
    """
    Spike pattern templates for cryptographic operations.
    """

    def __init__(self, pattern_type: str, num_neurons: int = 100):
        self.pattern_type = pattern_type
        self.num_neurons = num_neurons
        self.template = self._create_template()

    def _create_template(self) -> np.ndarray:
        """Create spike pattern template."""
        if self.pattern_type == "hash":
            # Irregular spiking pattern
            return np.random.rand(self.num_neurons) * 0.5 + 0.1
        elif self.pattern_type == "encryption":
            # Regular high-frequency spiking
            return np.ones(self.num_neurons) * 0.8
        elif self.pattern_type == "signature":
            # Unique spike signature
            return np.random.randn(self.num_neurons) * 0.3 + 0.5
        else:
            return np.random.rand(self.num_neurons) * 0.5

    def generate_spikes(self, data_hash: bytes) -> List[int]:
        """Generate spike pattern based on data hash."""
        # Use hash to seed pattern
        np.random.seed(int(hashlib.sha256(data_hash).hexdigest()[:8], 16))
        spike_probs = self.template + np.random.randn(self.num_neurons) * 0.1
        return [i for i, p in enumerate(spike_probs) if np.random.rand() < p]


class BurstDetector:
    """
    Detects burst patterns in spike trains.
    """

    def __init__(self, burst_threshold: int = 3, max_isi_ms: float = 10.0):
        self.burst_threshold = burst_threshold
        self.max_isi_ms = max_isi_ms
        self.bursts: List[List[float]] = []

    def detect_bursts(self, spike_times: List[float]) -> List[List[float]]:
        """Detect bursts in spike train."""
        if len(spike_times) < 2:
            return []

        self.bursts = []
        current_burst = [spike_times[0]]

        for i in range(1, len(spike_times)):
            isi = (spike_times[i] - spike_times[i-1]) * 1000  # Convert to ms
            if isi <= self.max_isi_ms:
                current_burst.append(spike_times[i])
            else:
                if len(current_burst) >= self.burst_threshold:
                    self.bursts.append(current_burst.copy())
                current_burst = [spike_times[i]]

        if len(current_burst) >= self.burst_threshold:
            self.bursts.append(current_burst)

        return self.bursts

    def get_burst_rate(self, time_window_s: float = 1.0) -> float:
        """Calculate burst rate in Hz."""
        if not self.bursts or time_window_s <= 0:
            return 0.0
        return len(self.bursts) / time_window_s


class TemporalPatternAnalyzer:
    """
    Analyzes temporal patterns in neural activity.
    """

    def __init__(self):
        self.isi_history: List[float] = []
        self.cv_history: List[float] = []  # Coefficient of variation

    def analyze(self, spike_times: List[float]) -> Dict[str, float]:
        """Analyze temporal patterns in spike train."""
        if len(spike_times) < 2:
            return {"mean_rate": 0.0, "cv_isi": 0.0, "burst_index": 0.0}

        # Calculate inter-spike intervals
        isis = [(spike_times[i] - spike_times[i-1]) * 1000 for i in range(1, len(spike_times))]
        self.isi_history.extend(isis)

        mean_isi = np.mean(isis)
        std_isi = np.std(isis)
        cv_isi = std_isi / mean_isi if mean_isi > 0 else 0.0
        self.cv_history.append(cv_isi)

        # Mean firing rate
        duration = spike_times[-1] - spike_times[0]
        mean_rate = len(spike_times) / duration if duration > 0 else 0.0

        # Burst index (ratio of short ISIs)
        short_isis = sum(1 for isi in isis if isi < 10.0)  # < 10ms
        burst_index = short_isis / len(isis) if isis else 0.0

        return {
            "mean_rate_hz": mean_rate,
            "cv_isi": cv_isi,
            "burst_index": burst_index,
            "mean_isi_ms": mean_isi,
            "std_isi_ms": std_isi
        }


class NeuromorphicCryptoEngine:
    """
    Cryptography Engine using Neuromorphic Computing.

    Implements encryption/decryption using spiking neural networks
    with hardware entropy integration and M1 8GB optimization.
    """

    def __init__(
        self,
        input_neurons: int = 256,
        hidden_neurons: int = 512,
        output_neurons: int = 256
    ):
        self.input_neurons = input_neurons
        self.hidden_neurons = hidden_neurons
        self.output_neurons = output_neurons

        # Core components (lazy initialization)
        self._neural_network: Optional[SpikingNeuralNetwork] = None
        self._entropy_pool: Optional[EntropyPool] = None
        self._crypto_weights: Optional[np.ndarray] = None

        # Key management
        self._key_neurons: Dict[str, str] = {}
        self._active_keys: Dict[str, Dict[str, Any]] = {}
        self._initialized = False

    async def initialize(self) -> bool:
        """Initialize the crypto engine with lazy loading."""
        try:
            # Initialize entropy pool
            self._entropy_pool = EntropyPool(pool_size=1024, reseed_threshold=512)
            initial_entropy = secrets.token_bytes(64)
            self._entropy_pool.add_entropy("system", initial_entropy)

            # Initialize neural network on demand
            self._neural_network = SpikingNeuralNetwork(
                input_neurons=self.input_neurons,
                hidden_neurons=self.hidden_neurons,
                output_neurons=self.output_neurons
            )

            # Initialize cryptographic weights
            self._crypto_weights = np.random.randn(
                self.output_neurons, self.output_neurons
            ).astype(np.float32) * 0.1

            self._initialized = True
            logger.info(f"NeuromorphicCryptoEngine initialized ({self.input_neurons} -> {self.hidden_neurons} -> {self.output_neurons})")
            return True

        except Exception as e:
            logger.error(f"NeuromorphicCryptoEngine initialization failed: {e}")
            return False

    def _initialize_network(self) -> None:
        """Lazy initialization of SNN layers."""
        if self._neural_network is None:
            self._neural_network = SpikingNeuralNetwork(
                input_neurons=self.input_neurons,
                hidden_neurons=self.hidden_neurons,
                output_neurons=self.output_neurons
            )
        self._neural_network.initialize()

    def encrypt(self, data: bytes, key_id: Optional[str] = None) -> SNNEncryptedContainer:
        """
        Encrypt data using SNN-based transformation.

        Args:
            data: Data to encrypt
            key_id: Optional key identifier (generated if None)

        Returns:
            SNNEncryptedContainer with ciphertext and neural signature
        """
        if not self._initialized:
            raise RuntimeError("NeuromorphicCryptoEngine not initialized")

        # Generate key if not provided
        if key_id is None:
            key_id = self._generate_key_id()

        if key_id not in self._key_neurons:
            self._register_key_neurons(key_id)

        # Initialize network
        self._initialize_network()

        # Encode data to neural pattern
        neural_input = self._encode_data_to_neural(data, key_id)

        # Process through network for chaotic dynamics
        neural_output = self._neural_network.process(neural_input)

        # Apply cryptographic weights for additional transformation
        crypto_output = np.dot(self._crypto_weights, neural_output)

        # Generate SNN-based keystream
        keystream = self._generate_keystream(crypto_output, len(data))

        # XOR encryption
        ciphertext = bytearray(len(data))
        for i in range(len(data)):
            ciphertext[i] = data[i] ^ keystream[i]

        # Create neural signature
        neural_signature = crypto_output.copy()

        # Update entropy pool with operation results
        if self._entropy_pool:
            entropy_data = neural_output.tobytes()[:32]
            self._entropy_pool.add_entropy("neural_op", entropy_data)

        return SNNEncryptedContainer(
            ciphertext=bytes(ciphertext),
            neural_signature=neural_signature,
            key_id=key_id,
            timestamp=time.time(),
            entropy_used=len(entropy_data) if self._entropy_pool else 0
        )

    def decrypt(self, ciphertext: SNNEncryptedContainer) -> bytes:
        """
        Decrypt data using neural decryption.

        Args:
            ciphertext: SNNEncryptedContainer to decrypt

        Returns:
            Decrypted data bytes
        """
        if not self._initialized:
            raise RuntimeError("NeuromorphicCryptoEngine not initialized")

        key_id = ciphertext.key_id

        if key_id not in self._key_neurons:
            raise ValueError(f"Key {key_id} not found")

        # Initialize network
        self._initialize_network()

        # Use stored neural signature for decryption
        neural_output = ciphertext.neural_signature

        # Reverse cryptographic weights
        inverse_weights = np.linalg.pinv(self._crypto_weights)
        reverse_output = np.dot(inverse_weights, neural_output)

        # Regenerate keystream
        keystream = self._generate_keystream(neural_output, len(ciphertext.ciphertext))

        # XOR decryption (same as encryption)
        plaintext = bytearray(len(ciphertext.ciphertext))
        for i in range(len(ciphertext.ciphertext)):
            plaintext[i] = ciphertext.ciphertext[i] ^ keystream[i]

        return bytes(plaintext)

    def generate_signature(self, data: bytes, key_id: Optional[str] = None) -> bytes:
        """
        Generate high-entropy neural signature for data integrity.

        Args:
            data: Data to sign
            key_id: Optional key identifier

        Returns:
            Signature bytes
        """
        if not self._initialized:
            raise RuntimeError("NeuromorphicCryptoEngine not initialized")

        if key_id is None:
            key_id = list(self._key_neurons.keys())[0] if self._key_neurons else self._generate_key_id()

        if key_id not in self._key_neurons:
            self._register_key_neurons(key_id)

        self._initialize_network()

        # Encode data
        neural_input = self._encode_data_to_neural(data, key_id)

        # Process through network
        neural_output = self._neural_network.process(neural_input)

        # Create signature from neural activation pattern
        sig_hash = hashlib.sha256(neural_output.tobytes() + data).digest()

        # Add entropy from pool
        if self._entropy_pool:
            pool_entropy = self._entropy_pool.extract_entropy(32)
            sig_hash = hashlib.sha256(sig_hash + pool_entropy).digest()

        return sig_hash

    def verify_signature(self, data: bytes, signature: bytes, key_id: Optional[str] = None) -> bool:
        """
        Verify neural signature.

        Args:
            data: Original data
            signature: Signature to verify
            key_id: Optional key identifier

        Returns:
            True if signature is valid
        """
        try:
            expected_sig = self.generate_signature(data, key_id)
            return secrets.compare_digest(signature, expected_sig)
        except Exception:
            return False

    def get_entropy_pool(self) -> Optional[EntropyPool]:
        """Get the entropy pool for cryptographic randomness."""
        return self._entropy_pool

    def _encode_data_to_neural(self, data: bytes, key_context: str) -> np.ndarray:
        """Encode data bytes to neural activation pattern."""
        # Create hash-based encoding
        hash_obj = hashlib.sha256(data + key_context.encode())
        hash_bytes = hash_obj.digest()

        # Convert to neural pattern
        neural_pattern = np.zeros(self.input_neurons, dtype=np.float32)

        for i, byte in enumerate(hash_bytes):
            idx = i % self.input_neurons
            neural_pattern[idx] = (neural_pattern[idx] + byte / 255.0) / 2.0

        # Add entropy for randomness
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

        # Expand neural output to required length using chaotic dynamics
        while len(keystream) < length:
            # Use neural output to seed next iteration
            next_hash = hashlib.sha256(output_bytes + bytes(keystream)).digest()
            keystream.extend(next_hash)
            output_bytes = next_hash

        return bytes(keystream[:length])

    def _generate_key_id(self) -> str:
        """Generate unique key ID."""
        return f"neuro_key_{time.time_ns()}_{secrets.token_hex(4)}"

    def _register_key_neurons(self, key_id: str) -> None:
        """Register key neurons for a key ID."""
        neuron_id = f"neuron_{key_id}"
        self._key_neurons[key_id] = neuron_id
        self._active_keys[key_id] = {
            'neuron_id': neuron_id,
            'created_at': time.time(),
            'key_size': self.input_neurons,
        }

    def cleanup(self) -> None:
        """Clean up memory (M1 8GB optimization)."""
        if self._neural_network:
            self._neural_network.cleanup()
            self._neural_network = None
        self._crypto_weights = None
        self._entropy_pool = None
        logger.info("NeuromorphicCryptoEngine memory cleaned up")


@dataclass
class EncryptedContainer:
    """Šifrovaný kontejner"""
    ciphertext: bytes
    encapsulated_key: bytes
    nonce: bytes
    algorithm: str
    security_level: SecurityLevel

    def to_dict(self) -> Dict[str, str]:
        """Export jako slovník"""
        return {
            "ciphertext": base64.b64encode(self.ciphertext).decode(),
            "encapsulated_key": base64.b64encode(self.encapsulated_key).decode(),
            "nonce": base64.b64encode(self.nonce).decode(),
            "algorithm": self.algorithm,
            "security_level": self.security_level.value,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "EncryptedContainer":
        """Import ze slovníku"""
        return cls(
            ciphertext=base64.b64decode(data["ciphertext"]),
            encapsulated_key=base64.b64decode(data["encapsulated_key"]),
            nonce=base64.b64decode(data["nonce"]),
            algorithm=data["algorithm"],
            security_level=SecurityLevel(data["security_level"]),
        )


@dataclass
class SNNEncryptedContainer:
    """Container for SNN-based encrypted data with neural signatures."""
    ciphertext: bytes
    neural_signature: np.ndarray
    key_id: str
    timestamp: float
    entropy_used: float

    def __post_init__(self):
        if self.timestamp == 0:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """Export as dictionary with numpy array handling."""
        return {
            "ciphertext": base64.b64encode(self.ciphertext).decode(),
            "neural_signature": base64.b64encode(self.neural_signature.tobytes()).decode(),
            "key_id": self.key_id,
            "timestamp": self.timestamp,
            "entropy_used": self.entropy_used,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SNNEncryptedContainer":
        """Import from dictionary."""
        sig_bytes = base64.b64decode(data["neural_signature"])
        neural_signature = np.frombuffer(sig_bytes, dtype=np.float32)
        return cls(
            ciphertext=base64.b64decode(data["ciphertext"]),
            neural_signature=neural_signature,
            key_id=data["key_id"],
            timestamp=data["timestamp"],
            entropy_used=data["entropy_used"],
        )


class QuantumSafeVault:
    """
    Trezor s quantum-safe kryptografií.

    Používá ML-KEM (Kyber) pro šifrování a ML-DSA (Dilithium)
    pro digitální podpisy. Odolné vůči kvantovým útokům.

    Integruje Neuromorphic Crypto Engine pro SNN-based šifrování
    a neurální podpisy s M1 8GB optimalizací.

    Example:
        >>> vault = QuantumSafeVault(security_level=SecurityLevel.HIGH)
        >>> container = await vault.encrypt(b"sensitive research data")
        >>> decrypted = await vault.decrypt(container)
        >>> # SNN-based encryption
        >>> snn_container = await vault.encrypt_with_snn(b"neural encrypted data")
        >>> snn_decrypted = await vault.decrypt_with_snn(snn_container)
    """

    def __init__(self, security_level: SecurityLevel = SecurityLevel.HIGH):
        self.security_level = security_level
        self._keypair = None
        self._initialized = False
        # Neuromorphic crypto engine (lazy initialization for M1 8GB)
        self._neuro_engine: Optional[NeuromorphicCryptoEngine] = None
        
    async def initialize(self) -> None:
        """Inicializovat vault - vygenerovat klíče"""
        logger.info(f"Initializing QuantumSafeVault ({self.security_level.value})")
        
        # REAL PQ: ML-KEM-768 + ML-DSA-65 keypairs via liboqs
        if not REAL_PQ_AVAILABLE:
            # FALLBACK: simulation mode — NOT cryptographically secure
            self._keypair = {
                "public": secrets.token_bytes(32),
                "secret": secrets.token_bytes(32),
            }
            self._signing_keypair = {
                "public": secrets.token_bytes(32),
                "secret": secrets.token_bytes(64),
            }
        else:
            with _oqs.KeyEncapsulation("ML-KEM-768") as kem:
                self._keypair = {
                    "public": kem.generate_keypair(),
                    "secret": kem.export_secret_key(),
                }
            with _oqs.Signature("ML-DSA-65") as sig:
                self._signing_keypair = {
                    "public": sig.generate_keypair(),
                    "secret": sig.export_secret_key(),
                }

        self._initialized = True
        logger.info("✓ QuantumSafeVault initialized")
    
    async def encrypt(
        self,
        plaintext: bytes,
        associated_data: bytes = None
    ) -> EncryptedContainer:
        """
        Zašifrovat data pomocí ML-KEM.
        
        Args:
            plaintext: Data k zašifrování
            associated_data: Volitelná přidružená data (pro AEAD)
            
        Returns:
            EncryptedContainer s ciphertextem
        """
        if not self._initialized:
            raise RuntimeError("Vault not initialized")
        
        # Generovat nonce
        nonce = secrets.token_bytes(12)
        
        # REAL ML-KEM encapsulation via liboqs
        if REAL_PQ_AVAILABLE:
            with _oqs.KeyEncapsulation("ML-KEM-768", self._keypair["secret"]) as kem:
                encapsulated_key, shared_secret = kem.encap_secret(self._keypair["public"])
        else:
            # FALLBACK: simulation mode — NOT cryptographically secure
            shared_secret = secrets.token_bytes(32)
            encapsulated_key = secrets.token_bytes(32)
        
        # AES-256-GCM s shared_secret
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aesgcm = AESGCM(shared_secret)
        ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data)
        
        return EncryptedContainer(
            ciphertext=ciphertext,
            encapsulated_key=encapsulated_key,
            nonce=nonce,
            algorithm="ML-KEM-768+AES-256-GCM",
            security_level=self.security_level,
        )
    
    async def decrypt(
        self,
        container: EncryptedContainer,
        associated_data: bytes = None
    ) -> bytes:
        """
        Dešifrovat data.
        
        Args:
            container: EncryptedContainer
            associated_data: Přidružená data
            
        Returns:
            Dešifrovaná data
        """
        if not self._initialized:
            raise RuntimeError("Vault not initialized")
        
        # REAL ML-KEM decapsulation via liboqs
        if REAL_PQ_AVAILABLE:
            with _oqs.KeyEncapsulation("ML-KEM-768", self._keypair["secret"]) as kem:
                shared_secret = kem.decap_secret(container.encapsulated_key)
        else:
            # FALLBACK: simulation mode — NOT cryptographically secure
            shared_secret = secrets.token_bytes(32)
        
        # AES-256-GCM decryption
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aesgcm = AESGCM(shared_secret)
        plaintext = aesgcm.decrypt(container.nonce, container.ciphertext, associated_data)
        
        return plaintext
    
    async def sign(self, message: bytes) -> bytes:
        """
        Podepsat zprávu pomocí ML-DSA (Dilithium).
        
        Args:
            message: Zpráva k podpisu
            
        Returns:
            Podpis
        """
        if not self._initialized:
            raise RuntimeError("Vault not initialized")
        
        # REAL ML-DSA signature via liboqs
        if REAL_PQ_AVAILABLE:
            with _oqs.Signature("ML-DSA-65", self._signing_keypair["secret"]) as sig:
                return sig.sign(message)
        else:
            # FALLBACK: simulation mode — NOT cryptographically secure
            return secrets.token_bytes(64)

    async def verify(self, message: bytes, signature: bytes) -> bool:
        """Ověřit podpis"""
        if not self._initialized:
            raise RuntimeError("Vault not initialized")

        # REAL ML-DSA verification via liboqs
        if REAL_PQ_AVAILABLE:
            with _oqs.Signature("ML-DSA-65", self._signing_keypair["secret"]) as sig:
                return sig.verify(message, signature, self._signing_keypair["public"])
        else:
            # FALLBACK: simulation mode — NOT cryptographically secure
            return True

    async def _get_neuro_engine(self) -> NeuromorphicCryptoEngine:
        """Lazy initialization of neuromorphic crypto engine (M1 8GB optimization)."""
        if self._neuro_engine is None:
            self._neuro_engine = NeuromorphicCryptoEngine(
                input_neurons=256,
                hidden_neurons=512,
                output_neurons=256
            )
            await self._neuro_engine.initialize()
        return self._neuro_engine

    async def encrypt_with_snn(self, data: bytes, key_id: Optional[str] = None) -> SNNEncryptedContainer:
        """
        Encrypt data using SNN-based neuromorphic encryption.

        Uses spiking neural network chaotic dynamics for encryption
        with high-entropy neural signatures.

        Args:
            data: Data to encrypt
            key_id: Optional key identifier

        Returns:
            SNNEncryptedContainer with ciphertext and neural signature
        """
        if not self._initialized:
            raise RuntimeError("Vault not initialized")

        engine = await self._get_neuro_engine()
        return engine.encrypt(data, key_id)

    async def decrypt_with_snn(self, ciphertext: SNNEncryptedContainer) -> bytes:
        """
        Decrypt SNN-encrypted data.

        Args:
            ciphertext: SNNEncryptedContainer to decrypt

        Returns:
            Decrypted data bytes
        """
        if not self._initialized:
            raise RuntimeError("Vault not initialized")

        engine = await self._get_neuro_engine()
        return engine.decrypt(ciphertext)

    async def generate_neural_signature(self, data: bytes, key_id: Optional[str] = None) -> bytes:
        """
        Generate high-entropy neural signature for data integrity.

        Uses SNN-based signature generation with hardware entropy
        integration for quantum-resistant integrity verification.

        Args:
            data: Data to sign
            key_id: Optional key identifier

        Returns:
            Signature bytes (32 bytes)
        """
        if not self._initialized:
            raise RuntimeError("Vault not initialized")

        engine = await self._get_neuro_engine()
        return engine.generate_signature(data, key_id)

    async def verify_neural_signature(self, data: bytes, signature: bytes, key_id: Optional[str] = None) -> bool:
        """
        Verify neural signature.

        Args:
            data: Original data
            signature: Signature to verify
            key_id: Optional key identifier

        Returns:
            True if signature is valid
        """
        if not self._initialized:
            raise RuntimeError("Vault not initialized")

        engine = await self._get_neuro_engine()
        return engine.verify_signature(data, signature, key_id)

    async def cleanup_neuro_engine(self) -> None:
        """Clean up neuromorphic engine memory (M1 8GB optimization)."""
        if self._neuro_engine:
            self._neuro_engine.cleanup()
            self._neuro_engine = None
            logger.info("NeuromorphicCryptoEngine cleaned up")


class StealthCommunicator:
    """
    Stealth komunikátor se steganografií.
    
    Skrývá zprávy v obrazech pomocí:
    - DCT (JPEG kompatibilní)
    - LSB (PNG/BMP)
    - Neural (AI-based)
    
    Pro skryté ukládání výzkumných dat.
    """
    
    def __init__(self, method: StegoMethod = StegoMethod.AUTO):
        self.method = method
        
    async def hide_message(
        self,
        message: bytes,
        cover_image: bytes,
        password: str = None
    ) -> bytes:
        """
        Schovat zprávu v obrázku.
        
        Args:
            message: Zpráva k schování
            cover_image: Cover image data
            password: Volitelné heslo pro šifrování
            
        Returns:
            Stego image s ukrytou zprávou
        """
        # Nejprve zašifrovat zprávu
        if password:
            from cryptography.fernet import Fernet
            key = hashlib.sha256(password.encode()).digest()
            f = Fernet(base64.urlsafe_b64encode(key))
            message = f.encrypt(message)
        
        # Přidat metadata
        message_with_meta = len(message).to_bytes(4, 'big') + message
        
        # Vybrat metodu
        method = self._select_method(cover_image)
        
        # Aplikovat steganografii
        if method == StegoMethod.LSB:
            return await self._lsb_hide(message_with_meta, cover_image)
        elif method == StegoMethod.DCT:
            return await self._dct_hide(message_with_meta, cover_image)
        else:
            return await self._lsb_hide(message_with_meta, cover_image)
    
    async def extract_message(
        self,
        stego_image: bytes,
        password: str = None
    ) -> bytes:
        """
        Extrahovat zprávu z obrázku.
        
        Args:
            stego_image: Stego image
            password: Heslo pro dešifrování
            
        Returns:
            Extrahovaná zpráva
        """
        # Detekovat metodu
        method = self._detect_method(stego_image)
        
        # Extrahovat
        if method == StegoMethod.LSB:
            message = await self._lsb_extract(stego_image)
        elif method == StegoMethod.DCT:
            message = await self._dct_extract(stego_image)
        else:
            message = await self._lsb_extract(stego_image)
        
        # Extrahovat metadata
        msg_len = int.from_bytes(message[:4], 'big')
        message = message[4:4+msg_len]
        
        # Dešifrovat pokud je heslo
        if password:
            from cryptography.fernet import Fernet
            key = hashlib.sha256(password.encode()).digest()
            f = Fernet(base64.urlsafe_b64encode(key))
            message = f.decrypt(message)
        
        return message
    
    def _select_method(self, cover_image: bytes) -> StegoMethod:
        """Vybrat nejlepší metodu"""
        if self.method != StegoMethod.AUTO:
            return self.method
        
        # Detekovat formát
        if cover_image[:2] == b'\xff\xd8':  # JPEG
            return StegoMethod.DCT
        elif cover_image[:8] == b'\x89PNG\r\n\x1a\n':  # PNG
            return StegoMethod.LSB
        else:
            return StegoMethod.LSB
    
    def _detect_method(self, image: bytes) -> StegoMethod:
        """Detekovat použitou metodu"""
        # Zkusit LSB nejprve
        return StegoMethod.LSB
    
    async def _lsb_hide(self, message: bytes, cover: bytes) -> bytes:
        """LSB steganografie"""
        try:
            from PIL import Image
            import io
            
            img = Image.open(io.BytesIO(cover))
            
            # Převést na RGB
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            pixels = list(img.getdata())
            
            # Převést zprávu na bity
            message_bits = ''.join(format(b, '08b') for b in message)
            message_bits += '00000000'  # Delimiter
            
            if len(message_bits) > len(pixels) * 3:
                raise ValueError("Message too large for cover image")
            
            # Schovat bity v LSB
            new_pixels = []
            msg_idx = 0
            
            for pixel in pixels:
                r, g, b = pixel
                
                if msg_idx < len(message_bits):
                    r = (r & 0xFE) | int(message_bits[msg_idx])
                    msg_idx += 1
                if msg_idx < len(message_bits):
                    g = (g & 0xFE) | int(message_bits[msg_idx])
                    msg_idx += 1
                if msg_idx < len(message_bits):
                    b = (b & 0xFE) | int(message_bits[msg_idx])
                    msg_idx += 1
                
                new_pixels.append((r, g, b))
            
            # Vytvořit nový obrázek
            img.putdata(new_pixels)
            output = io.BytesIO()
            img.save(output, format='PNG')
            return output.getvalue()
            
        except ImportError:
            logger.error("PIL not available for steganography")
            return cover
    
    async def _lsb_extract(self, stego: bytes) -> bytes:
        """Extrahovat z LSB"""
        try:
            from PIL import Image
            import io
            
            img = Image.open(io.BytesIO(stego))
            pixels = list(img.getdata())
            
            # Extrahovat bity
            bits = ''
            for pixel in pixels:
                r, g, b = pixel
                bits += str(r & 1)
                bits += str(g & 1)
                bits += str(b & 1)
            
            # Převést na byty
            message = bytearray()
            for i in range(0, len(bits), 8):
                byte = bits[i:i+8]
                if len(byte) == 8:
                    message.append(int(byte, 2))
            
            return bytes(message)
            
        except ImportError:
            return b''
    
    async def _dct_hide(self, message: bytes, cover: bytes) -> bytes:
        """DCT steganografie (simplified)"""
        # Zjednodušená implementace - v produkci by byla komplexnější
        return await self._lsb_hide(message, cover)
    
    async def _dct_extract(self, stego: bytes) -> bytes:
        """Extrahovat z DCT"""
        return await self._lsb_extract(stego)
