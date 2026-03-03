"""
identity/peer_challenge.py

Peer-to-peer reputation challenge manager.

This module runs on each node and manages the lifecycle of reputation mining
challenges:

  1. ISSUE    — Node randomly selects an active identity and issues it a
                PoW challenge via the P2P network.

  2. RESPOND  — The challenged identity solves the PoW and broadcasts a
                REPUTATION_MINE transaction back to the network.

  3. REPORT   — If the challenge window expires without a response, the
                issuing node submits a REPUTATION_IGNORE transaction, which
                (when mined) penalizes the unresponsive identity.

Challenge selection strategy:
  - Weighted random: identities with higher balance are less likely to be
    challenged (they're already trusted). New/low-balance identities are
    challenged more frequently, building their reputation faster.
  - One pending challenge per identity at a time (no flooding).

Network message types added for reputation challenges:
  REP_CHALLENGE   — peer → identity: "solve this PoW"
  REP_SOLUTION    — identity → network: "here is my solution"
"""

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from identity.pow import issue_reputation_challenge, PoWChallenge
from identity.reputation import ReputationRecord


# ---------------------------------------------------------------------------
# Network message payloads (serialized as dicts over the P2P layer)
# ---------------------------------------------------------------------------

@dataclass
class ChallengeMessage:
    """Sent by a node to an identity to initiate a reputation mining cycle."""
    target_pubkey: str            # Who must solve this
    challenge_data_hex: str       # hex of PoWChallenge.challenge_data
    difficulty_bits: int
    issued_at: float
    expires_in: float
    issuer_pubkey: str            # Who issued it (for accountability)

    def to_dict(self) -> dict:
        return {
            'target_pubkey': self.target_pubkey,
            'challenge_data_hex': self.challenge_data_hex,
            'difficulty_bits': self.difficulty_bits,
            'issued_at': self.issued_at,
            'expires_in': self.expires_in,
            'issuer_pubkey': self.issuer_pubkey,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'ChallengeMessage':
        return cls(
            target_pubkey=d['target_pubkey'],
            challenge_data_hex=d['challenge_data_hex'],
            difficulty_bits=d['difficulty_bits'],
            issued_at=d['issued_at'],
            expires_in=d['expires_in'],
            issuer_pubkey=d['issuer_pubkey'],
        )

    def to_challenge(self) -> PoWChallenge:
        from identity.pow import PoWChallenge, REPUTATION_DIFFICULTY_BITS
        return PoWChallenge(
            challenge_data=bytes.fromhex(self.challenge_data_hex),
            difficulty_bits=self.difficulty_bits,
            issued_at=self.issued_at,
            expires_in=self.expires_in,
        )


# ---------------------------------------------------------------------------
# Challenge tracker (per-node state)
# ---------------------------------------------------------------------------

@dataclass
class IssuedChallenge:
    """Tracks a challenge this node issued to another identity."""
    target_pubkey: str
    challenge: PoWChallenge
    issued_at: float = field(default_factory=time.time)
    resolved: bool = False   # True once solved or ignored tx submitted


class PeerChallengeManager:
    """
    Manages the full lifecycle of reputation challenges on a single node.

    Responsibilities:
      - Periodically select identities to challenge
      - Issue challenges over the P2P network
      - Track pending challenges and their expiry
      - Submit REPUTATION_IGNORE transactions for expired challenges
      - Handle incoming challenges directed at this node's identity
    """

    def __init__(
        self,
        my_pubkey_hex: str,
        challenge_interval: float = 120.0,    # How often to issue a challenge (seconds)
        challenge_window: float = 300.0,       # How long identities have to respond
    ):
        self.my_pubkey_hex = my_pubkey_hex
        self.challenge_interval = challenge_interval
        self.challenge_window = challenge_window

        # Challenges this node has issued
        self.issued: dict[str, IssuedChallenge] = {}
        self._lock = threading.Lock()

        # Callbacks — set by the node after creating this manager
        self.on_challenge_to_issue: Callable[[ChallengeMessage], None] | None = None
        # Called when we want to broadcast a challenge to the network

        self.on_ignore_to_report: Callable[[str, PoWChallenge], None] | None = None
        # Called when a challenge expired — node should submit REPUTATION_IGNORE tx

        self.on_incoming_challenge: Callable[[ChallengeMessage], None] | None = None
        # Called when this node's identity receives a challenge from the network

    # ------------------------------------------------------------------
    # Issuing challenges
    # ------------------------------------------------------------------

    def select_target(self, identities: list[ReputationRecord]) -> ReputationRecord | None:
        """
        Choose an identity to challenge.

        Selection strategy:
          - Only authenticated, non-revoked identities
          - Exclude identities already pending a challenge
          - Weighted random: lower balance → higher probability of selection
            (they need to build reputation faster)
          - Exclude ourselves (don't challenge ourselves)
        """
        with self._lock:
            pending = set(self.issued.keys())

        candidates = [
            r for r in identities
            if r.is_authenticated
            and r.pubkey_hex not in pending
            and r.pubkey_hex != self.my_pubkey_hex
        ]

        if not candidates:
            return None

        # Inverse-balance weighting: lower balance → higher weight
        max_bal = max(r.balance for r in candidates) or 1.0
        weights = [max_bal - r.balance + 1.0 for r in candidates]

        return random.choices(candidates, weights=weights, k=1)[0]

    def issue_challenge(self, target: ReputationRecord) -> ChallengeMessage | None:
        """
        Issue a fresh PoW challenge to target.
        Returns the ChallengeMessage to broadcast, or None if target already pending.
        """
        with self._lock:
            if target.pubkey_hex in self.issued:
                return None  # Already pending

        challenge = issue_reputation_challenge(
            target.pubkey_hex,
            expires_in=self.challenge_window,
        )

        msg = ChallengeMessage(
            target_pubkey=target.pubkey_hex,
            challenge_data_hex=challenge.challenge_data.hex(),
            difficulty_bits=challenge.difficulty_bits,
            issued_at=challenge.issued_at,
            expires_in=challenge.expires_in,
            issuer_pubkey=self.my_pubkey_hex,
        )

        with self._lock:
            self.issued[target.pubkey_hex] = IssuedChallenge(
                target_pubkey=target.pubkey_hex,
                challenge=challenge,
            )

        target.record_challenge_issued(challenge.issued_at)
        return msg

    # ------------------------------------------------------------------
    # Monitoring expiry
    # ------------------------------------------------------------------

    def check_expired(self) -> list[tuple[str, PoWChallenge]]:
        """
        Find all issued challenges that have expired without being resolved.
        Returns list of (target_pubkey, challenge) pairs to report as ignored.
        Removes them from the issued dict.
        """
        now = time.time()
        expired = []

        with self._lock:
            to_remove = []
            for pubkey, issued in self.issued.items():
                if not issued.resolved and issued.challenge.is_expired:
                    expired.append((pubkey, issued.challenge))
                    to_remove.append(pubkey)
            for k in to_remove:
                del self.issued[k]

        return expired

    def mark_resolved(self, target_pubkey: str):
        """Mark a challenge as resolved (identity responded successfully)."""
        with self._lock:
            entry = self.issued.get(target_pubkey)
            if entry:
                entry.resolved = True
                del self.issued[target_pubkey]

    # ------------------------------------------------------------------
    # Handling incoming challenges (for our own identity)
    # ------------------------------------------------------------------

    def receive_challenge(self, msg: ChallengeMessage) -> PoWChallenge | None:
        """
        Handle a challenge directed at this node's identity.
        Returns the PoWChallenge to solve, or None if not for us / already expired.
        """
        if msg.target_pubkey != self.my_pubkey_hex:
            return None  # Not for us

        challenge = msg.to_challenge()
        if challenge.is_expired:
            return None

        return challenge

    # ------------------------------------------------------------------
    # Background scheduler
    # ------------------------------------------------------------------

    def start(self, get_identities: Callable[[], list[ReputationRecord]]):
        """
        Start background threads for:
          - Periodically issuing challenges
          - Monitoring expired challenges and reporting ignores
        """
        threading.Thread(
            target=self._issue_loop,
            args=(get_identities,),
            daemon=True,
            name="challenge-issuer"
        ).start()

        threading.Thread(
            target=self._expiry_loop,
            daemon=True,
            name="challenge-expiry-monitor"
        ).start()

    def _issue_loop(self, get_identities: Callable[[], list[ReputationRecord]]):
        """Periodically select and challenge an identity."""
        while True:
            time.sleep(self.challenge_interval)
            try:
                identities = get_identities()
                target = self.select_target(identities)
                if target:
                    msg = self.issue_challenge(target)
                    if msg and self.on_challenge_to_issue:
                        self.on_challenge_to_issue(msg)
            except Exception as e:
                print(f"[challenge-issuer] error: {e}")

    def _expiry_loop(self):
        """Check for expired challenges and report them."""
        while True:
            time.sleep(30)  # Check every 30 seconds
            try:
                expired = self.check_expired()
                for target_pubkey, challenge in expired:
                    if self.on_ignore_to_report:
                        self.on_ignore_to_report(target_pubkey, challenge)
            except Exception as e:
                print(f"[challenge-expiry-monitor] error: {e}")