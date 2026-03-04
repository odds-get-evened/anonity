"""
identity/identity.py

Identity-aware blockchain layer built on top of request-chain's Blockchain.

New transaction types added to TxTypes:
  IDENTITY_REGISTER  — Register a new identity (PGP/EC public key) with PoW proof
  REPUTATION_MINE    — Record a solved reputation challenge on-chain
  REPUTATION_IGNORE  — Record an ignored/expired challenge on-chain (peer-submitted)

The identity is anchored to a compressed EC public key (same curve as the base
blockchain — SECP256R1). This is functionally equivalent to a PGP public key
for our purposes (sign + verify), and directly compatible with the existing
Transaction signing infrastructure.

Registration PoW:
  Every IDENTITY_REGISTER transaction must include a valid PoW solution.
  The PoW is verified by all nodes before the transaction is accepted.
  This is the primary Sybil resistance mechanism: it costs real CPU time
  to register a new identity, buying time and resources before farming scales.

Reputation state:
  Each node maintains an in-memory (and persisted) map of
  pubkey_hex -> ReputationRecord.  The chain is the authoritative log;
  the ReputationRecord is the derived state.
"""

import json
import time
from enum import IntEnum
from pathlib import Path
import pickle

from cryptography.hazmat.primitives.asymmetric import ec

from blockchain.blockchain import (
    Blockchain, Block, Transaction, TxTypes,
    serialize_pubkey, deserialize_pubkey,
    BIT_OP,
)
from identity.pow import (
    verify_registration, verify_reputation,
    PoWChallenge, REGISTRATION_DIFFICULTY_BITS, REPUTATION_DIFFICULTY_BITS,
)
from identity.reputation import ReputationRecord, ReputationEngine, DEFAULT_BALANCE


# ---------------------------------------------------------------------------
# Extended transaction type enum values
# (We extend by integer value since TxTypes is IntEnum)
# ---------------------------------------------------------------------------

class IdentityTxTypes(IntEnum):
    IDENTITY_REGISTER = 10   # New identity registration with PoW
    REPUTATION_MINE   = 11   # Solved reputation challenge recorded on chain
    REPUTATION_IGNORE = 12   # Ignored/expired challenge recorded on chain


# ---------------------------------------------------------------------------
# Identity transaction factory helpers
# ---------------------------------------------------------------------------

def make_registration_tx(
    priv_key: ec.EllipticCurvePrivateKey,
    seed: bytes,
    nonce: int,
) -> Transaction:
    """
    Build and sign an IDENTITY_REGISTER transaction.

    The UID encodes the PoW proof as a JSON string so it travels in the
    existing Transaction.uid field without schema changes.

    uid format:
      JSON: {"seed": "<hex>", "nonce": <int>, "bits": <int>}
    """
    pub_key = priv_key.public_key()
    pubkey_hex = serialize_pubkey(pub_key)

    proof_payload = json.dumps({
        "seed": seed.hex(),
        "nonce": nonce,
        "bits": REGISTRATION_DIFFICULTY_BITS,
    }, sort_keys=True)

    tx = Transaction(
        pub_key=pub_key,
        uid=proof_payload,
        tx_type=IdentityTxTypes.IDENTITY_REGISTER,
    )
    tx.sign(priv_key)
    return tx


def make_reputation_mine_tx(
    priv_key: ec.EllipticCurvePrivateKey,
    challenge: PoWChallenge,
    nonce: int,
) -> Transaction:
    """
    Build and sign a REPUTATION_MINE transaction after solving a peer challenge.

    uid format:
      JSON: {"challenge_data": "<hex>", "nonce": <int>, "bits": <int>}
    """
    pub_key = priv_key.public_key()

    proof_payload = json.dumps({
        "challenge_data": challenge.challenge_data.hex(),
        "nonce": nonce,
        "bits": REPUTATION_DIFFICULTY_BITS,
    }, sort_keys=True)

    tx = Transaction(
        pub_key=pub_key,
        uid=proof_payload,
        tx_type=IdentityTxTypes.REPUTATION_MINE,
    )
    tx.sign(priv_key)
    return tx


def make_reputation_ignore_tx(
    reporter_priv_key: ec.EllipticCurvePrivateKey,
    offender_pubkey_hex: str,
    challenge: PoWChallenge,
) -> Transaction:
    """
    Build and sign a REPUTATION_IGNORE transaction (submitted by the challenging peer).

    uid format:
      JSON: {"offender": "<pubkey_hex>", "challenge_data": "<hex>", "issued_at": <float>}
    """
    pub_key = reporter_priv_key.public_key()

    payload = json.dumps({
        "offender": offender_pubkey_hex,
        "challenge_data": challenge.challenge_data.hex(),
        "issued_at": challenge.issued_at,
    }, sort_keys=True)

    tx = Transaction(
        pub_key=pub_key,
        uid=payload,
        tx_type=IdentityTxTypes.REPUTATION_IGNORE,
    )
    tx.sign(reporter_priv_key)
    return tx


# ---------------------------------------------------------------------------
# Identity-aware Blockchain
# ---------------------------------------------------------------------------

class IdentityBlockchain(Blockchain):
    """
    Extends the base Blockchain with identity registration and reputation tracking.

    Additional state maintained:
      identities     — dict[pubkey_hex, ReputationRecord]
      pending_challenges — dict[pubkey_hex, PoWChallenge]  (in-memory, not on chain)
    """

    def __init__(self, difficulty: int = 2):
        super().__init__(difficulty)
        self.identities: dict[str, ReputationRecord] = {}
        self.pending_challenges: dict[str, PoWChallenge] = {}

    # ------------------------------------------------------------------
    # Identity registration
    # ------------------------------------------------------------------

    def register_identity(self, tx: Transaction) -> bool:
        """
        Validate and add an IDENTITY_REGISTER transaction to the mempool.

        Validation:
          1. tx_type must be IDENTITY_REGISTER
          2. Signature must verify
          3. PoW proof in tx.uid must be valid
          4. Identity must not already be registered (no re-registration without PoW)

        Returns True if added, False if invalid.
        """
        if tx.tx_type != IdentityTxTypes.IDENTITY_REGISTER:
            return False

        if not tx.verify():
            return False

        pubkey_hex = tx.requester

        # Parse PoW proof from uid
        try:
            proof = json.loads(tx.uid)
            seed = bytes.fromhex(proof["seed"])
            nonce = int(proof["nonce"])
        except (json.JSONDecodeError, KeyError, ValueError):
            return False

        # Verify PoW
        if not verify_registration(pubkey_hex, seed, nonce):
            return False

        # Block re-registration only when the identity has built up (or lost)
        # reputation away from the default — at exactly DEFAULT_BALANCE the PoW
        # cost already paid is sufficient to allow a fresh start.
        existing = self.identities.get(pubkey_hex)
        if existing and existing.is_authenticated and existing.balance != DEFAULT_BALANCE:
            return False

        # Add to mempool
        self.mempool.append(tx)
        return True

    # ------------------------------------------------------------------
    # Reputation mining
    # ------------------------------------------------------------------

    def submit_reputation_solution(self, tx: Transaction) -> bool:
        """
        Validate and add a REPUTATION_MINE transaction to the mempool.

        Validation:
          1. tx_type must be REPUTATION_MINE
          2. Signature must verify
          3. Identity must be registered and authenticated
          4. A pending challenge must exist for this identity
          5. PoW solution must satisfy the pending challenge
          6. Challenge must not be expired

        Returns True if added, False if invalid.
        """
        if tx.tx_type != IdentityTxTypes.REPUTATION_MINE:
            return False

        if not tx.verify():
            return False

        pubkey_hex = tx.requester

        # Identity must be registered and authenticated
        record = self.identities.get(pubkey_hex)
        if not record or not record.is_authenticated:
            return False

        # Must have a pending challenge
        challenge = self.pending_challenges.get(pubkey_hex)
        if not challenge:
            return False

        if challenge.is_expired:
            return False

        # Parse and verify solution
        try:
            proof = json.loads(tx.uid)
            nonce = int(proof["nonce"])
            challenge_data_hex = proof["challenge_data"]
        except (json.JSONDecodeError, KeyError, ValueError):
            return False

        # Challenge data must match what we issued
        if challenge_data_hex != challenge.challenge_data.hex():
            return False

        if not verify_reputation(challenge, nonce):
            return False

        self.mempool.append(tx)
        return True

    def record_ignore(self, tx: Transaction) -> bool:
        """
        Validate and add a REPUTATION_IGNORE transaction to the mempool.

        Called by a peer who issued a challenge that expired without response.

        Returns True if added.
        """
        if tx.tx_type != IdentityTxTypes.REPUTATION_IGNORE:
            return False

        if not tx.verify():
            return False

        try:
            payload = json.loads(tx.uid)
            offender_hex = payload["offender"]
        except (json.JSONDecodeError, KeyError):
            return False

        # Offender must be a known identity
        if offender_hex not in self.identities:
            return False

        self.mempool.append(tx)
        return True

    # ------------------------------------------------------------------
    # Authentication gate
    # ------------------------------------------------------------------

    def authenticate(self, pubkey_hex: str) -> bool:
        """
        The primary authentication method.

        Returns True if the identity associated with pubkey_hex:
          - Is registered on this chain
          - Has a balance >= AUTH_THRESHOLD
          - Has not been revoked
        """
        record = self.identities.get(pubkey_hex)
        if not record:
            return False
        return record.is_authenticated

    # ------------------------------------------------------------------
    # Override mine_block to handle identity transactions
    # ------------------------------------------------------------------

    def mine_identity_block(self, miner_pubkey: ec.EllipticCurvePublicKey) -> Block | None:
        """
        Mine a block that may include identity transactions from the mempool.
        Handles IDENTITY_REGISTER, REPUTATION_MINE, REPUTATION_IGNORE in
        addition to all base transaction types.
        """
        # Separate identity txs from base txs
        identity_txs = [
            tx for tx in self.mempool
            if tx.tx_type in (
                IdentityTxTypes.IDENTITY_REGISTER,
                IdentityTxTypes.REPUTATION_MINE,
                IdentityTxTypes.REPUTATION_IGNORE,
            )
        ]
        base_txs = [
            tx for tx in self.mempool
            if tx.tx_type not in (
                IdentityTxTypes.IDENTITY_REGISTER,
                IdentityTxTypes.REPUTATION_MINE,
                IdentityTxTypes.REPUTATION_IGNORE,
            )
        ]

        # Let the base class handle base txs (creates coinbase, PoW, etc.)
        # We temporarily swap the mempool to only base txs
        original_mempool = self.mempool
        self.mempool = base_txs

        block = self.mine_block(miner_pubkey)

        self.mempool = original_mempool

        if block is None:
            # No base txs; create a minimal block for identity txs if any
            if not identity_txs:
                return None

            from blockchain.blockchain import MINING_REWARD
            coinbase = Transaction(
                pub_key=miner_pubkey,
                uid=f"COINBASE_BLOCK_{len(self.chain)}",
                tx_type=TxTypes.COINBASE,
                amount=MINING_REWARD,
            )
            coinbase.signature = "COINBASE"
            all_txs = [coinbase] + identity_txs

            blk = Block(len(self.chain), self.last_hash, all_txs)
            blk.hash = self.proof_of_work(blk)
            self.chain.append(blk)
            block = blk

        else:
            # Append identity txs to the mined block's transaction list
            # (they're already validated — just add and recompute hash)
            if identity_txs:
                block.transactions.extend(identity_txs)
                block.hash = block.compute_hash()

        # Apply identity state changes from the mined identity txs
        for tx in identity_txs:
            self._apply_identity_tx(tx)
            # Remove from mempool
            if tx in self.mempool:
                self.mempool.remove(tx)

        return block

    def _apply_identity_tx(self, tx: Transaction):
        """Update in-memory identity state after a tx is confirmed on chain."""
        pubkey_hex = tx.requester

        if tx.tx_type == IdentityTxTypes.IDENTITY_REGISTER:
            # Create or reset reputation record
            self.identities[pubkey_hex] = ReputationRecord(
                pubkey_hex=pubkey_hex,
                balance=DEFAULT_BALANCE,
                registered_at=time.time(),
            )

        elif tx.tx_type == IdentityTxTypes.REPUTATION_MINE:
            record = self.identities.get(pubkey_hex)
            if record:
                ReputationEngine.on_challenge_solved(record)
                # Clear pending challenge
                self.pending_challenges.pop(pubkey_hex, None)

        elif tx.tx_type == IdentityTxTypes.REPUTATION_IGNORE:
            try:
                payload = json.loads(tx.uid)
                offender_hex = payload["offender"]
            except (json.JSONDecodeError, KeyError):
                return

            record = self.identities.get(offender_hex)
            if record:
                ReputationEngine.on_challenge_ignored(record)
                self.pending_challenges.pop(offender_hex, None)

    # ------------------------------------------------------------------
    # Inactivity decay (called periodically by background thread)
    # ------------------------------------------------------------------

    def apply_inactivity_decay(self):
        """
        Apply soft decay to all identities that have no pending challenge
        and have not responded recently.  Safe to call from a background thread.
        """
        now = time.time()
        for pubkey_hex, record in self.identities.items():
            if pubkey_hex not in self.pending_challenges:
                ReputationEngine.on_inactivity_tick(record, now)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def snapshot_identity(self, p: Path):
        """Save full IdentityBlockchain state to disk."""
        with open(p, 'wb') as fh:
            pickle.dump(self, fh, pickle.HIGHEST_PROTOCOL)

    @staticmethod
    def init_identity(p: Path, difficulty: int = 2) -> 'IdentityBlockchain':
        """Load from disk or create a new IdentityBlockchain."""
        if p.exists():
            with open(p, 'rb') as fh:
                try:
                    chain = pickle.load(fh)
                    if not isinstance(chain, IdentityBlockchain):
                        # Migrate old chain
                        new_chain = IdentityBlockchain(difficulty=chain.difficulty)
                        new_chain.chain = chain.chain
                        return new_chain
                    return chain
                except Exception:
                    pass
        return IdentityBlockchain(difficulty=difficulty)

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def list_identities(self) -> list[ReputationRecord]:
        return list(self.identities.values())

    def get_identity(self, pubkey_hex: str) -> ReputationRecord | None:
        return self.identities.get(pubkey_hex)