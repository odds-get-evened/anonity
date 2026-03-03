"""
identity/reputation.py

Reputation wallet lifecycle engine.

Rules (as designed):

GROWTH
  - Peer issues a PoW mining challenge to an active identity.
  - Identity solves and returns the challenge within the window.
  - On-chain confirmation increments the wallet balance by MINING_REWARD.

SOFT DECAY (inactivity, floor = DEFAULT_BALANCE)
  - Identity is not responding to challenges but has not explicitly ignored them.
  - Defined as: no challenge issued to this identity recently (identity is simply
    absent / offline).
  - Balance decays toward DEFAULT_BALANCE at SOFT_DECAY_RATE per decay tick.
  - Balance NEVER goes below DEFAULT_BALANCE from soft decay alone.
  - Identity remains authenticated as long as balance >= AUTH_THRESHOLD.

HARD DECAY (ignored challenges, floor = 0 → revocation)
  - A challenge was issued, the window expired, and no solution was returned.
  - Balance is penalized by IGNORE_PENALTY per ignored challenge.
  - Balance CAN go below DEFAULT_BALANCE and all the way to 0.
  - At balance == 0 (or below), identity authentication is REVOKED.

AUTHENTICATION GATE
  - balance >= AUTH_THRESHOLD  →  authenticated
  - balance < AUTH_THRESHOLD   →  revoked / denied
"""

import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BALANCE: float = 100.0       # Starting balance for every new identity
AUTH_THRESHOLD: float = 1.0          # Minimum balance to remain authenticated
MINING_REWARD: float = 10.0          # Balance gain per solved reputation challenge
SOFT_DECAY_RATE: float = 2.0         # Balance lost per decay tick (inactivity)
IGNORE_PENALTY: float = 15.0         # Balance lost per ignored/expired challenge
DECAY_TICK_SECONDS: float = 3600.0   # How often soft decay is applied (1 hour)


# ---------------------------------------------------------------------------
# Reputation record
# ---------------------------------------------------------------------------

@dataclass
class ReputationRecord:
    """
    Persistent reputation state stored per identity on (or alongside) the chain.

    pubkey_hex      — compressed EC public key as hex string (identity anchor)
    balance         — current reputation balance
    registered_at   — unix timestamp of registration
    last_challenge  — unix timestamp of last issued challenge (None = never)
    last_response   — unix timestamp of last solved challenge (None = never)
    ignored_count   — cumulative number of ignored/expired challenges
    solved_count    — cumulative number of solved challenges
    last_decay_tick — unix timestamp of last soft-decay application
    revoked         — True if balance dropped to/below AUTH_THRESHOLD permanently
    """
    pubkey_hex: str
    balance: float = DEFAULT_BALANCE
    registered_at: float = field(default_factory=time.time)
    last_challenge: float | None = None
    last_response: float | None = None
    ignored_count: int = 0
    solved_count: int = 0
    last_decay_tick: float = field(default_factory=time.time)
    revoked: bool = False

    # -----------------------------------------------------------------------
    # Authentication gate
    # -----------------------------------------------------------------------

    @property
    def is_authenticated(self) -> bool:
        """True if this identity is currently allowed to authenticate."""
        return not self.revoked and self.balance >= AUTH_THRESHOLD

    # -----------------------------------------------------------------------
    # Mutation methods (called by the chain / reputation engine)
    # -----------------------------------------------------------------------

    def apply_mining_reward(self) -> float:
        """
        Called when the identity successfully solves a reputation challenge.
        Increments balance by MINING_REWARD.
        Returns the new balance.
        """
        self.balance += MINING_REWARD
        self.solved_count += 1
        self.last_response = time.time()
        return self.balance

    def apply_soft_decay(self, now: float | None = None) -> float:
        """
        Apply inactivity soft decay.  Called periodically when no challenge has
        been issued recently (identity is simply absent).

        Balance decays toward DEFAULT_BALANCE but never below it.
        Returns the new balance.
        """
        now = now or time.time()
        ticks_elapsed = (now - self.last_decay_tick) / DECAY_TICK_SECONDS
        if ticks_elapsed < 1.0:
            return self.balance  # Not enough time has passed

        decay_amount = SOFT_DECAY_RATE * ticks_elapsed

        if self.balance > DEFAULT_BALANCE:
            # Decay toward default but stop at it
            self.balance = max(DEFAULT_BALANCE, self.balance - decay_amount)
        # If balance is already at or below default, soft decay does nothing
        self.last_decay_tick = now
        return self.balance

    def apply_ignore_penalty(self) -> float:
        """
        Called when a challenge issued to this identity expired without a response.
        Penalizes balance — can go below DEFAULT_BALANCE, down to 0.
        Triggers revocation if balance hits AUTH_THRESHOLD.
        Returns the new balance.
        """
        self.balance = max(0.0, self.balance - IGNORE_PENALTY)
        self.ignored_count += 1

        if self.balance < AUTH_THRESHOLD:
            self.revoked = True

        return self.balance

    def record_challenge_issued(self, ts: float | None = None):
        """Record that a challenge was sent to this identity."""
        self.last_challenge = ts or time.time()

    # -----------------------------------------------------------------------
    # Serialization helpers
    # -----------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            'pubkey_hex': self.pubkey_hex,
            'balance': self.balance,
            'registered_at': self.registered_at,
            'last_challenge': self.last_challenge,
            'last_response': self.last_response,
            'ignored_count': self.ignored_count,
            'solved_count': self.solved_count,
            'last_decay_tick': self.last_decay_tick,
            'revoked': self.revoked,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'ReputationRecord':
        return cls(
            pubkey_hex=d['pubkey_hex'],
            balance=d.get('balance', DEFAULT_BALANCE),
            registered_at=d.get('registered_at', time.time()),
            last_challenge=d.get('last_challenge'),
            last_response=d.get('last_response'),
            ignored_count=d.get('ignored_count', 0),
            solved_count=d.get('solved_count', 0),
            last_decay_tick=d.get('last_decay_tick', time.time()),
            revoked=d.get('revoked', False),
        )

    def summary(self) -> str:
        status = "✅ AUTHENTICATED" if self.is_authenticated else "❌ REVOKED"
        return (
            f"[{self.pubkey_hex[:16]}…] "
            f"balance={self.balance:.1f} "
            f"solved={self.solved_count} ignored={self.ignored_count} "
            f"→ {status}"
        )


# ---------------------------------------------------------------------------
# Reputation engine (stateless helpers — state lives in ReputationRecord)
# ---------------------------------------------------------------------------

class ReputationEngine:
    """
    Stateless helper that applies reputation rules to a ReputationRecord.
    The engine is separate from the record so the record can be serialized
    to the chain while the engine lives in the node process.
    """

    @staticmethod
    def on_challenge_solved(record: ReputationRecord) -> ReputationRecord:
        """Call when identity returns a valid PoW solution."""
        record.apply_mining_reward()
        return record

    @staticmethod
    def on_challenge_ignored(record: ReputationRecord) -> ReputationRecord:
        """Call when a challenge window expires with no response."""
        record.apply_ignore_penalty()
        return record

    @staticmethod
    def on_inactivity_tick(record: ReputationRecord, now: float | None = None) -> ReputationRecord:
        """
        Call periodically when no active challenge cycle is in progress.
        Applies soft decay toward DEFAULT_BALANCE.
        """
        record.apply_soft_decay(now)
        return record

    @staticmethod
    def check_auth(record: ReputationRecord) -> bool:
        """Return True if the identity may authenticate right now."""
        return record.is_authenticated