"""
identity/pow.py

Proof-of-Work engine used in two distinct contexts:

1. REGISTRATION PoW  — A minimum mandatory PoW that every new identity must solve
   before their key is accepted into the network. This is a one-time cost that raises
   the bar for Sybil farming: spinning up 1000 throwaway keys requires 1000 PoW solutions.

2. REPUTATION MINING PoW  — Ongoing lightweight challenges issued by peers to active
   identities. Solving these grows reputation balance. Ignoring them starts decay.

The registration difficulty is intentionally higher than reputation mining difficulty.
"""

import hashlib
import os
import time
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Registration: number of leading zero BITS the hash must satisfy.
# 20 bits ≈ ~1M iterations on average — meaningful cost per key, trivial for
# a legitimate user who registers once, painful for a Sybil factory.
REGISTRATION_DIFFICULTY_BITS: int = 20

# Reputation mining: much lighter — just enough to prove liveness.
REPUTATION_DIFFICULTY_BITS: int = 14

# Maximum nonce before giving up (prevents infinite loops in tests)
MAX_NONCE: int = 2 ** 32


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _leading_zero_bits(digest: bytes) -> int:
    """Count the number of leading zero bits in a bytes object."""
    count = 0
    for byte in digest:
        if byte == 0:
            count += 8
        else:
            # Count leading zeros in this byte
            count += 8 - byte.bit_length()
            break
    return count


def _hash(data: bytes) -> bytes:
    """SHA-256 hash returning raw bytes."""
    return hashlib.sha256(data).digest()


# ---------------------------------------------------------------------------
# Challenge dataclass
# ---------------------------------------------------------------------------

@dataclass
class PoWChallenge:
    """
    A PoW challenge issued to an identity.

    challenge_data  — bytes the solver must include (typically: pubkey + nonce_seed)
    difficulty_bits — minimum leading zero bits required in the solution hash
    issued_at       — unix timestamp when the challenge was issued
    expires_in      — seconds before the challenge is considered ignored
    """
    challenge_data: bytes
    difficulty_bits: int
    issued_at: float
    expires_in: float = 300.0  # 5 minutes default

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.issued_at) > self.expires_in

    def make_input(self, nonce: int) -> bytes:
        """Build the input bytes for hashing: challenge_data || nonce (big-endian 8 bytes)."""
        return self.challenge_data + nonce.to_bytes(8, 'big')


# ---------------------------------------------------------------------------
# PoW solver (runs locally — called by the identity owner)
# ---------------------------------------------------------------------------

def solve(challenge: PoWChallenge, start_nonce: int = 0) -> tuple[int, bytes] | None:
    """
    Find a nonce such that SHA-256(challenge_data || nonce) has at least
    difficulty_bits leading zero bits.

    Returns (nonce, hash_bytes) on success, None if MAX_NONCE is reached.
    """
    for nonce in range(start_nonce, start_nonce + MAX_NONCE):
        candidate = _hash(challenge.make_input(nonce))
        if _leading_zero_bits(candidate) >= challenge.difficulty_bits:
            return nonce, candidate
    return None


def solve_registration(pubkey_hex: str, seed: bytes | None = None) -> tuple[bytes, int, bytes]:
    """
    Solve a registration-level PoW for the given public key hex string.

    The challenge data is:  SHA-256(pubkey_bytes || seed)
    seed defaults to 16 random bytes if not provided.

    Returns (seed, nonce, solution_hash).
    Raises RuntimeError if no solution found within MAX_NONCE.
    """
    seed = seed or os.urandom(16)
    pubkey_bytes = bytes.fromhex(pubkey_hex)
    challenge_data = _hash(pubkey_bytes + seed)

    challenge = PoWChallenge(
        challenge_data=challenge_data,
        difficulty_bits=REGISTRATION_DIFFICULTY_BITS,
        issued_at=time.time(),
        expires_in=float('inf'),  # registration has no expiry
    )

    result = solve(challenge)
    if result is None:
        raise RuntimeError("Registration PoW: no solution found — increase MAX_NONCE")

    nonce, solution_hash = result
    return seed, nonce, solution_hash


def solve_reputation(challenge: PoWChallenge) -> tuple[int, bytes] | None:
    """
    Solve a peer-issued reputation mining challenge.
    Returns (nonce, solution_hash) or None if expired/unsolvable.
    """
    if challenge.is_expired:
        return None
    return solve(challenge)


# ---------------------------------------------------------------------------
# PoW verifier (runs on peers / chain validators)
# ---------------------------------------------------------------------------

def verify_registration(pubkey_hex: str, seed: bytes, nonce: int) -> bool:
    """
    Verify that (seed, nonce) is a valid registration PoW for pubkey_hex.
    """
    pubkey_bytes = bytes.fromhex(pubkey_hex)
    challenge_data = _hash(pubkey_bytes + seed)
    candidate = _hash(challenge_data + nonce.to_bytes(8, 'big'))
    return _leading_zero_bits(candidate) >= REGISTRATION_DIFFICULTY_BITS


def verify_reputation(challenge: PoWChallenge, nonce: int) -> bool:
    """
    Verify that nonce solves the given reputation challenge.
    Does NOT check expiry — expiry enforcement is the caller's responsibility.
    """
    candidate = _hash(challenge.make_input(nonce))
    return _leading_zero_bits(candidate) >= challenge.difficulty_bits


# ---------------------------------------------------------------------------
# Challenge factory (runs on peers — used to issue challenges to identities)
# ---------------------------------------------------------------------------

def issue_reputation_challenge(pubkey_hex: str, expires_in: float = 300.0) -> PoWChallenge:
    """
    Create a fresh reputation mining challenge for a given identity.

    The challenge data is: SHA-256(pubkey_bytes || random_16_bytes || timestamp)
    This makes each challenge unique and unpredictable.
    """
    pubkey_bytes = bytes.fromhex(pubkey_hex)
    nonce_seed = os.urandom(16)
    ts_bytes = int(time.time()).to_bytes(8, 'big')
    challenge_data = _hash(pubkey_bytes + nonce_seed + ts_bytes)

    return PoWChallenge(
        challenge_data=challenge_data,
        difficulty_bits=REPUTATION_DIFFICULTY_BITS,
        issued_at=time.time(),
        expires_in=expires_in,
    )