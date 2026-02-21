"""
============================================================================
Netflix Real-Time LLM Personalization & Inference Platform
Model Artifact & Data Encryption
============================================================================

WHY THIS MODULE EXISTS:
    The inference platform handles proprietary model artifacts that represent
    millions of dollars in R&D investment, plus user personalization data
    subject to GDPR and CCPA. This module ensures:

    1. Model artifacts are encrypted at rest before storage (S3, EFS)
    2. Personalization data is encrypted in transit and at rest
    3. Encryption keys are managed through KMS with automatic rotation
    4. The envelope encryption pattern keeps data keys close to data
       while master keys stay in KMS

DESIGN DECISIONS:
    - AES-256-GCM chosen for authenticated encryption: it provides both
      confidentiality and integrity in a single operation, and is the
      NIST-recommended AEAD cipher
    - Envelope encryption: Each artifact gets a unique data encryption key (DEK),
      which is encrypted by the KMS master key (KEK). This means:
      (a) Re-encrypting under a new master key does not require re-encrypting
          all data
      (b) Data keys never leave the encryption boundary in plaintext
    - 96-bit nonces (12 bytes) per GCM recommendation for random nonces
    - Key metadata tracks creation, rotation, and usage for compliance

SECURITY IMPLICATIONS:
    - Master keys live in KMS (mocked for development; production uses
      AWS KMS or HashiCorp Vault)
    - Data keys are generated per-artifact and stored encrypted alongside
      the ciphertext
    - Key rotation generates a new master key; existing data is NOT
      re-encrypted immediately (lazy re-encryption on next access)
    - GCM authentication tags prevent ciphertext tampering

ALTERNATIVES CONSIDERED:
    - AES-256-CBC + HMAC: Requires two separate operations and is prone to
      padding oracle attacks if not implemented carefully
    - ChaCha20-Poly1305: Excellent choice but less hardware acceleration
      support on Intel/AMD platforms compared to AES-NI
    - NaCl/libsodium secretbox: Higher-level API but less control over
      key management integration
============================================================================
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Use the cryptography library for AES-256-GCM
# WHY: The 'cryptography' package is the de facto standard for Python
# cryptographic operations. It wraps OpenSSL, provides safe defaults, and
# is audited by third-party security firms.
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# AES-256 requires a 256-bit (32-byte) key
_AES_KEY_SIZE_BYTES: int = 32

# GCM recommends 96-bit (12-byte) nonces when using random nonces
_GCM_NONCE_SIZE_BYTES: int = 12

# GCM produces a 128-bit (16-byte) authentication tag
_GCM_TAG_SIZE_BYTES: int = 16

# Encrypted artifact file extension
_ENCRYPTED_EXTENSION: str = ".enc"

# Metadata file extension (stored alongside encrypted artifacts)
_METADATA_EXTENSION: str = ".enc.meta"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class KeyMetadata:
    """
    Metadata for a cryptographic key managed by the encryption system.

    WHY: Key lifecycle tracking is essential for compliance. We need to know
    when a key was created, when it was last rotated, how many times it has
    been used, and whether it is still active.

    Attributes:
        key_id: Unique identifier for this key.
        created_at: When the key was generated (UTC).
        rotated_at: When the key was last rotated (UTC), or None if never.
        is_active: Whether the key is currently in use for new encryptions.
        usage_count: How many encrypt/decrypt operations used this key.
        algorithm: The encryption algorithm this key is used with.
        key_size_bits: The key size in bits.
        version: Key version number (incremented on rotation).
    """
    key_id: str
    created_at: datetime
    rotated_at: Optional[datetime] = None
    is_active: bool = True
    usage_count: int = 0
    algorithm: str = "AES-256-GCM"
    key_size_bits: int = 256
    version: int = 1


@dataclass
class EncryptedArtifact:
    """
    The result of encrypting a model artifact using envelope encryption.

    WHY: This structure bundles all the information needed to decrypt the
    artifact later: the ciphertext, the encrypted data key, the nonce,
    and metadata about which master key was used.

    Attributes:
        artifact_id: Unique identifier for this encrypted artifact.
        encrypted_data_key: The data encryption key, encrypted by the master key.
        nonce: The random nonce used for GCM encryption.
        ciphertext_path: Filesystem path to the encrypted artifact.
        original_size: Size of the original plaintext in bytes.
        encrypted_size: Size of the ciphertext in bytes.
        master_key_id: ID of the master key used to encrypt the data key.
        created_at: When this artifact was encrypted (UTC).
        checksum: SHA-256 hash of the original plaintext for integrity verification.
    """
    artifact_id: str
    encrypted_data_key: bytes
    nonce: bytes
    ciphertext_path: str
    original_size: int
    encrypted_size: int
    master_key_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    checksum: str = ""


# ---------------------------------------------------------------------------
# Mock KMS client
# ---------------------------------------------------------------------------

class _MockKMSClient:
    """
    Mock KMS client for development and testing environments.

    WHY: Production uses AWS KMS (or HashiCorp Vault), but local development
    and CI/CD need a functional encryption system without cloud dependencies.
    This mock replicates the KMS API surface:
    - generate_data_key: returns a plaintext key and its encrypted form
    - encrypt: encrypts a plaintext key using the master key
    - decrypt: decrypts an encrypted key using the master key

    IMPORTANT: This mock stores the master key in memory. In production,
    the master key NEVER leaves the KMS HSM boundary.
    """

    def __init__(self) -> None:
        self._master_keys: Dict[str, bytes] = {}
        self._active_key_id: Optional[str] = None
        self._initialize_master_key()

    def _initialize_master_key(self) -> None:
        """Generate the initial master key."""
        key_id = f"kms-key-{uuid.uuid4().hex[:12]}"
        self._master_keys[key_id] = secrets.token_bytes(_AES_KEY_SIZE_BYTES)
        self._active_key_id = key_id
        logger.info("mock_kms_initialized", extra={"key_id": key_id})

    @property
    def active_key_id(self) -> str:
        """Return the ID of the currently active master key."""
        assert self._active_key_id is not None
        return self._active_key_id

    def generate_data_key(self) -> Tuple[bytes, bytes, str]:
        """
        Generate a new data encryption key and return it in both plaintext
        and encrypted form.

        Returns:
            Tuple of (plaintext_key, encrypted_key, master_key_id).
        """
        plaintext_key = secrets.token_bytes(_AES_KEY_SIZE_BYTES)
        encrypted_key = self._encrypt_with_master(plaintext_key)
        return plaintext_key, encrypted_key, self.active_key_id

    def decrypt_data_key(self, encrypted_key: bytes, master_key_id: str) -> bytes:
        """
        Decrypt a data encryption key using the specified master key.

        Args:
            encrypted_key: The encrypted data key.
            master_key_id: The ID of the master key that encrypted it.

        Returns:
            The plaintext data key.

        Raises:
            ValueError: If the master key ID is unknown.
        """
        master_key = self._master_keys.get(master_key_id)
        if master_key is None:
            raise ValueError(f"Master key '{master_key_id}' not found in KMS")
        return self._decrypt_with_master(encrypted_key, master_key)

    def rotate_master_key(self) -> str:
        """
        Generate a new master key and make it the active key.

        WHY: Key rotation limits the blast radius of a compromised key.
        Old master keys are retained so existing encrypted data keys can
        still be decrypted (lazy re-encryption).

        Returns:
            The ID of the new active master key.
        """
        new_key_id = f"kms-key-{uuid.uuid4().hex[:12]}"
        self._master_keys[new_key_id] = secrets.token_bytes(_AES_KEY_SIZE_BYTES)
        self._active_key_id = new_key_id
        logger.info(
            "mock_kms_key_rotated",
            extra={"new_key_id": new_key_id, "total_keys": len(self._master_keys)},
        )
        return new_key_id

    # ---- Internal helpers ----

    def _encrypt_with_master(self, plaintext: bytes) -> bytes:
        """Encrypt data with the active master key using AES-256-GCM."""
        master_key = self._master_keys[self.active_key_id]
        nonce = secrets.token_bytes(_GCM_NONCE_SIZE_BYTES)
        aesgcm = AESGCM(master_key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)
        # Prepend nonce to ciphertext so we can extract it during decryption
        return nonce + ciphertext

    def _decrypt_with_master(self, data: bytes, master_key: bytes) -> bytes:
        """Decrypt data with a master key using AES-256-GCM."""
        nonce = data[:_GCM_NONCE_SIZE_BYTES]
        ciphertext = data[_GCM_NONCE_SIZE_BYTES:]
        aesgcm = AESGCM(master_key)
        return aesgcm.decrypt(nonce, ciphertext, None)


# ---------------------------------------------------------------------------
# Encryption Manager
# ---------------------------------------------------------------------------

class EncryptionManager:
    """
    Model artifact and data encryption using AES-256-GCM with envelope encryption.

    WHY: Centralized encryption management ensures consistent cryptographic
    practices across the platform. Every model artifact and sensitive data
    payload flows through this manager, guaranteeing:
    - AES-256-GCM authenticated encryption
    - Unique data keys per artifact (envelope encryption)
    - KMS-managed master keys (mocked for development)
    - Full key lifecycle tracking for compliance

    Envelope Encryption Pattern:
    1. Request a new data encryption key (DEK) from KMS
    2. KMS returns the DEK in plaintext and encrypted form
    3. Encrypt the artifact with the plaintext DEK
    4. Store the encrypted DEK alongside the ciphertext
    5. Discard the plaintext DEK from memory
    6. To decrypt: ask KMS to decrypt the DEK, then use it to decrypt the artifact

    Usage:
        mgr = EncryptionManager()
        result = mgr.encrypt_model_artifact("/models/recommendation-v2.pt")
        mgr.decrypt_model_artifact(result.ciphertext_path)
    """

    def __init__(self, kms_client: Optional[_MockKMSClient] = None) -> None:
        """
        Initialize the encryption manager with a KMS client.

        WHY: Dependency injection of the KMS client enables testing with mocks
        and switching between mock/real KMS based on environment.

        Args:
            kms_client: Optional KMS client. If None, a mock client is created.
        """
        self._kms = kms_client or _MockKMSClient()
        self._key_metadata: Dict[str, KeyMetadata] = {}
        self._artifact_registry: Dict[str, EncryptedArtifact] = {}

        # Register the initial master key metadata
        self._register_key_metadata(self._kms.active_key_id)

        logger.info(
            "encryption_manager_initialized",
            extra={"master_key_id": self._kms.active_key_id},
        )

    # ------------------------------------------------------------------
    # Model artifact encryption
    # ------------------------------------------------------------------

    def encrypt_model_artifact(self, artifact_path: str) -> EncryptedArtifact:
        """
        Encrypt a model artifact file using envelope encryption with AES-256-GCM.

        WHY: Model artifacts (PyTorch checkpoints, ONNX models, TensorRT engines)
        are high-value intellectual property. Encrypting them at rest ensures
        they cannot be exfiltrated from storage in usable form.

        Process:
        1. Read the plaintext artifact from disk
        2. Compute a SHA-256 checksum for integrity verification
        3. Request a new data encryption key (DEK) from KMS
        4. Encrypt the artifact with the DEK using AES-256-GCM
        5. Write the ciphertext to disk with .enc extension
        6. Write metadata (encrypted DEK, nonce, checksums) to a sidecar file
        7. Return the EncryptedArtifact record

        Args:
            artifact_path: Path to the plaintext model artifact file.

        Returns:
            An EncryptedArtifact containing all information needed for decryption.

        Raises:
            FileNotFoundError: If the artifact file does not exist.
            OSError: If there are filesystem permission issues.
        """
        path = Path(artifact_path)
        if not path.exists():
            raise FileNotFoundError(f"Artifact not found: {artifact_path}")

        # Step 1: Read the plaintext artifact
        plaintext = path.read_bytes()
        original_size = len(plaintext)

        # Step 2: Compute integrity checksum
        checksum = hashlib.sha256(plaintext).hexdigest()

        # Step 3: Generate a unique data encryption key via KMS
        plaintext_dek, encrypted_dek, master_key_id = self._kms.generate_data_key()

        # Step 4: Encrypt the artifact with the DEK
        nonce = secrets.token_bytes(_GCM_NONCE_SIZE_BYTES)
        aesgcm = AESGCM(plaintext_dek)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        # Step 5: Write ciphertext to disk
        encrypted_path = str(path) + _ENCRYPTED_EXTENSION
        Path(encrypted_path).write_bytes(ciphertext)

        # Step 6: Build the artifact record
        artifact_id = f"artifact-{uuid.uuid4().hex[:12]}"
        artifact = EncryptedArtifact(
            artifact_id=artifact_id,
            encrypted_data_key=encrypted_dek,
            nonce=nonce,
            ciphertext_path=encrypted_path,
            original_size=original_size,
            encrypted_size=len(ciphertext),
            master_key_id=master_key_id,
            checksum=checksum,
        )

        # Step 7: Write metadata sidecar
        metadata_path = str(path) + _METADATA_EXTENSION
        self._write_artifact_metadata(metadata_path, artifact)

        # Register the artifact and update key usage
        self._artifact_registry[artifact_id] = artifact
        self._increment_key_usage(master_key_id)

        # Zero out the plaintext DEK from memory (best-effort in Python)
        # WHY: Minimizes the window where the plaintext key exists in process memory.
        # Note: Python's garbage collector may retain copies; for true key
        # zeroization, use a C extension or mlock'd memory.
        plaintext_dek = b"\x00" * _AES_KEY_SIZE_BYTES  # noqa: F841

        logger.info(
            "model_artifact_encrypted",
            extra={
                "artifact_id": artifact_id,
                "original_size": original_size,
                "encrypted_size": len(ciphertext),
                "master_key_id": master_key_id,
            },
        )

        return artifact

    def decrypt_model_artifact(self, encrypted_path: str) -> bytes:
        """
        Decrypt a previously encrypted model artifact.

        WHY: Model artifacts must be decrypted at inference time so they can
        be loaded into GPU memory. This method reverses the envelope encryption:
        1. Read the metadata sidecar to get the encrypted DEK and nonce
        2. Ask KMS to decrypt the DEK
        3. Use the plaintext DEK to decrypt the artifact
        4. Verify the integrity checksum

        Args:
            encrypted_path: Path to the encrypted artifact file (.enc).

        Returns:
            The decrypted plaintext bytes of the model artifact.

        Raises:
            FileNotFoundError: If the encrypted file or metadata sidecar is missing.
            ValueError: If the integrity checksum does not match.
        """
        enc_path = Path(encrypted_path)
        if not enc_path.exists():
            raise FileNotFoundError(f"Encrypted artifact not found: {encrypted_path}")

        # Derive the metadata path
        # Handle both .enc and non-.enc paths
        if encrypted_path.endswith(_ENCRYPTED_EXTENSION):
            base_path = encrypted_path[: -len(_ENCRYPTED_EXTENSION)]
        else:
            base_path = encrypted_path
        metadata_path = base_path + _METADATA_EXTENSION

        # Step 1: Load metadata
        metadata = self._read_artifact_metadata(metadata_path)

        encrypted_dek = base64.b64decode(metadata["encrypted_data_key"])
        nonce = base64.b64decode(metadata["nonce"])
        master_key_id = metadata["master_key_id"]
        expected_checksum = metadata["checksum"]

        # Step 2: Decrypt the data encryption key via KMS
        plaintext_dek = self._kms.decrypt_data_key(encrypted_dek, master_key_id)

        # Step 3: Read and decrypt the ciphertext
        ciphertext = enc_path.read_bytes()
        aesgcm = AESGCM(plaintext_dek)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)

        # Step 4: Verify integrity
        actual_checksum = hashlib.sha256(plaintext).hexdigest()
        if actual_checksum != expected_checksum:
            raise ValueError(
                f"Integrity check failed for '{encrypted_path}': "
                f"expected {expected_checksum}, got {actual_checksum}"
            )

        # Update key usage tracking
        self._increment_key_usage(master_key_id)

        # Zero out the plaintext DEK (best-effort)
        plaintext_dek = b"\x00" * _AES_KEY_SIZE_BYTES  # noqa: F841

        logger.info(
            "model_artifact_decrypted",
            extra={
                "encrypted_path": encrypted_path,
                "decrypted_size": len(plaintext),
                "master_key_id": master_key_id,
            },
        )

        return plaintext

    # ------------------------------------------------------------------
    # In-memory data encryption
    # ------------------------------------------------------------------

    def encrypt_data(self, data: bytes) -> Dict[str, str]:
        """
        Encrypt arbitrary data in memory using envelope encryption.

        WHY: Personalization features, user embeddings, and inference request
        payloads may contain sensitive data that must be encrypted before
        caching (Redis) or logging. This method provides a simple API for
        encrypting arbitrary byte payloads.

        The returned dictionary is JSON-serializable (all values are
        base64-encoded strings) so it can be stored in Redis, DynamoDB,
        or any key-value store.

        Args:
            data: The plaintext bytes to encrypt.

        Returns:
            A dictionary containing base64-encoded ciphertext, encrypted DEK,
            nonce, master key ID, and a SHA-256 checksum.
        """
        # Generate a per-payload data encryption key
        plaintext_dek, encrypted_dek, master_key_id = self._kms.generate_data_key()

        # Encrypt the data
        nonce = secrets.token_bytes(_GCM_NONCE_SIZE_BYTES)
        aesgcm = AESGCM(plaintext_dek)
        ciphertext = aesgcm.encrypt(nonce, data, None)

        # Compute checksum for integrity
        checksum = hashlib.sha256(data).hexdigest()

        # Update key usage
        self._increment_key_usage(master_key_id)

        # Zero out the plaintext DEK (best-effort)
        plaintext_dek = b"\x00" * _AES_KEY_SIZE_BYTES  # noqa: F841

        logger.info(
            "data_encrypted",
            extra={
                "data_size": len(data),
                "ciphertext_size": len(ciphertext),
                "master_key_id": master_key_id,
            },
        )

        return {
            "ciphertext": base64.b64encode(ciphertext).decode("utf-8"),
            "encrypted_data_key": base64.b64encode(encrypted_dek).decode("utf-8"),
            "nonce": base64.b64encode(nonce).decode("utf-8"),
            "master_key_id": master_key_id,
            "checksum": checksum,
            "algorithm": "AES-256-GCM",
        }

    def decrypt_data(self, encrypted_data: Dict[str, str]) -> bytes:
        """
        Decrypt data that was encrypted by encrypt_data().

        WHY: Retrieves the original plaintext from the encrypted payload
        structure. Used when reading cached personalization data from Redis
        or loading encrypted feature vectors.

        Args:
            encrypted_data: The dictionary returned by encrypt_data(), containing
                base64-encoded ciphertext, encrypted DEK, nonce, and metadata.

        Returns:
            The decrypted plaintext bytes.

        Raises:
            ValueError: If the integrity checksum does not match.
            KeyError: If required fields are missing from the encrypted_data dict.
        """
        # Decode all fields from base64
        ciphertext = base64.b64decode(encrypted_data["ciphertext"])
        encrypted_dek = base64.b64decode(encrypted_data["encrypted_data_key"])
        nonce = base64.b64decode(encrypted_data["nonce"])
        master_key_id = encrypted_data["master_key_id"]
        expected_checksum = encrypted_data.get("checksum", "")

        # Decrypt the data encryption key via KMS
        plaintext_dek = self._kms.decrypt_data_key(encrypted_dek, master_key_id)

        # Decrypt the data
        aesgcm = AESGCM(plaintext_dek)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)

        # Verify integrity if checksum is present
        if expected_checksum:
            actual_checksum = hashlib.sha256(plaintext).hexdigest()
            if actual_checksum != expected_checksum:
                raise ValueError(
                    f"Data integrity check failed: "
                    f"expected {expected_checksum}, got {actual_checksum}"
                )

        # Update key usage
        self._increment_key_usage(master_key_id)

        # Zero out the plaintext DEK (best-effort)
        plaintext_dek = b"\x00" * _AES_KEY_SIZE_BYTES  # noqa: F841

        logger.info(
            "data_decrypted",
            extra={
                "ciphertext_size": len(ciphertext),
                "plaintext_size": len(plaintext),
                "master_key_id": master_key_id,
            },
        )

        return plaintext

    # ------------------------------------------------------------------
    # Key rotation
    # ------------------------------------------------------------------

    def rotate_key(self) -> Dict[str, Any]:
        """
        Rotate the master encryption key.

        WHY: Regular key rotation limits the exposure window if a key is
        compromised. After rotation:
        - New encryptions use the new master key
        - Existing data can still be decrypted (old master keys are retained)
        - Lazy re-encryption happens on next decrypt+re-encrypt cycle

        Netflix policy requires master key rotation every 90 days. This method
        creates a new master key in KMS, marks the old key as inactive, and
        registers metadata for the new key.

        Returns:
            A dictionary containing the new key ID, the previous key ID,
            rotation timestamp, and key version.
        """
        previous_key_id = self._kms.active_key_id

        # Mark the old key as inactive for new encryptions
        if previous_key_id in self._key_metadata:
            self._key_metadata[previous_key_id].is_active = False

        # Rotate the master key in KMS
        new_key_id = self._kms.rotate_master_key()

        # Determine the new version
        previous_meta = self._key_metadata.get(previous_key_id)
        new_version = (previous_meta.version + 1) if previous_meta else 1

        # Register metadata for the new key
        self._register_key_metadata(new_key_id, version=new_version)

        logger.info(
            "master_key_rotated",
            extra={
                "previous_key_id": previous_key_id,
                "new_key_id": new_key_id,
                "new_version": new_version,
            },
        )

        return {
            "previous_key_id": previous_key_id,
            "new_key_id": new_key_id,
            "rotated_at": datetime.now(timezone.utc).isoformat(),
            "new_version": new_version,
            "total_managed_keys": len(self._key_metadata),
        }

    # ------------------------------------------------------------------
    # Key metadata
    # ------------------------------------------------------------------

    def get_key_metadata(self, key_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieve metadata for a specific key or all managed keys.

        WHY: Key metadata is required for compliance audits and operational
        monitoring. It shows key age, rotation history, and usage patterns.

        Args:
            key_id: If provided, return metadata for this specific key.
                If None, return metadata for all managed keys.

        Returns:
            A dictionary containing key metadata, including the active key ID,
            total keys, and per-key details (creation time, usage count, etc.).

        Raises:
            ValueError: If the specified key_id is not found.
        """
        if key_id is not None:
            meta = self._key_metadata.get(key_id)
            if meta is None:
                raise ValueError(f"Key '{key_id}' not found")
            return self._serialize_key_metadata(meta)

        return {
            "active_key_id": self._kms.active_key_id,
            "total_keys": len(self._key_metadata),
            "keys": {
                kid: self._serialize_key_metadata(meta)
                for kid, meta in self._key_metadata.items()
            },
            "total_encrypted_artifacts": len(self._artifact_registry),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _register_key_metadata(self, key_id: str, version: int = 1) -> None:
        """Register metadata for a newly created key."""
        self._key_metadata[key_id] = KeyMetadata(
            key_id=key_id,
            created_at=datetime.now(timezone.utc),
            version=version,
        )

    def _increment_key_usage(self, key_id: str) -> None:
        """Increment the usage counter for a key."""
        meta = self._key_metadata.get(key_id)
        if meta is not None:
            meta.usage_count += 1

    def _serialize_key_metadata(self, meta: KeyMetadata) -> Dict[str, Any]:
        """Convert KeyMetadata to a JSON-serializable dictionary."""
        return {
            "key_id": meta.key_id,
            "created_at": meta.created_at.isoformat(),
            "rotated_at": meta.rotated_at.isoformat() if meta.rotated_at else None,
            "is_active": meta.is_active,
            "usage_count": meta.usage_count,
            "algorithm": meta.algorithm,
            "key_size_bits": meta.key_size_bits,
            "version": meta.version,
        }

    def _write_artifact_metadata(
        self,
        metadata_path: str,
        artifact: EncryptedArtifact,
    ) -> None:
        """
        Write artifact encryption metadata to a JSON sidecar file.

        WHY: The metadata file stores everything needed to decrypt the artifact
        except the KMS master key itself. It lives alongside the encrypted
        artifact so the decryption process can find it.
        """
        metadata = {
            "artifact_id": artifact.artifact_id,
            "encrypted_data_key": base64.b64encode(
                artifact.encrypted_data_key
            ).decode("utf-8"),
            "nonce": base64.b64encode(artifact.nonce).decode("utf-8"),
            "master_key_id": artifact.master_key_id,
            "original_size": artifact.original_size,
            "encrypted_size": artifact.encrypted_size,
            "checksum": artifact.checksum,
            "algorithm": "AES-256-GCM",
            "created_at": artifact.created_at.isoformat(),
        }
        Path(metadata_path).write_text(json.dumps(metadata, indent=2))

    def _read_artifact_metadata(self, metadata_path: str) -> Dict[str, Any]:
        """
        Read artifact encryption metadata from a JSON sidecar file.

        Args:
            metadata_path: Path to the .enc.meta file.

        Returns:
            Parsed metadata dictionary.

        Raises:
            FileNotFoundError: If the metadata file does not exist.
        """
        path = Path(metadata_path)
        if not path.exists():
            raise FileNotFoundError(
                f"Artifact metadata not found: {metadata_path}. "
                f"Cannot decrypt without the encryption metadata sidecar."
            )
        return json.loads(path.read_text())
