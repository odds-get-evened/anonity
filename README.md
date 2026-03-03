# Anonymous Identity Authenticator

A pseudonymous, reputation-based identity management system built on a custom blockchain. Identities are anchored to EC public keys (equivalent to PGP public keys), reputation is earned by solving proof-of-work challenges, and authentication is gated by a wallet balance that can grow, decay, or be revoked.

---

## Architecture

The system is layered: `identity_peer.py` serves as the node entry point, wiring together an `IdentityBlockchain` (which extends the request-chain base with `IDENTITY_REGISTER`, `REPUTATION_MINE`, and `REPUTATION_IGNORE` transaction types), a `PeerChallengeManager` (which periodically selects peers to challenge and tracks pending challenges to expiry), a `ReputationRecord` store (per-identity balance and lifecycle state held in `identity/reputation.py`), and a SHA-256 PoW engine (`identity/pow.py`) used for both registration proofs and reputation challenges. The P2P layer from request-chain is reused unchanged; two new message types — `REP_CHALLENGE` and `REP_SOLUTION` — handle reputation flows on top of the existing block and transaction gossip.

---

## Core Concepts

### Identity = EC Public Key

Every identity is a compressed SECP256R1 public key (33 bytes, 66 hex chars). This is functionally equivalent to a PGP public key — it can sign, verify, and be stored pseudonymously on chain. The private key never leaves the node.

### Authentication = Wallet Balance Check

```
balance >= 1.0  →  ✅ AUTHENTICATED
balance <  1.0  →  ❌ DENIED / REVOKED
```

There is no username, password, or session token. Any peer can verify any identity by checking the chain.

### Registration = One-Time PoW

Every new identity must solve a **20-bit PoW** (≈1M SHA-256 iterations) before their key is accepted. This is the primary Sybil resistance mechanism — cheap for a legitimate user who registers once, expensive at scale for a Sybil factory.

```
Registration cost  ≈ 1–5 seconds on modern hardware
1,000 fake keys    ≈ 16–83 minutes of CPU time
1,000,000 fake keys ≈ 11–57 days of CPU time (single core)
```

---

## Reputation Lifecycle

### New Identity

```
register() → PoW solved → IDENTITY_REGISTER tx mined → balance = 100.0 (DEFAULT)
```

### Growth (active participation)

```
Peer issues challenge → Identity solves PoW (14-bit) → REPUTATION_MINE tx mined
→ balance += 10.0 (MINING_REWARD)
```

### Soft Decay (inactivity — safe floor)

Applies when an identity has no pending challenge and is simply absent:

```
Every DECAY_TICK_SECONDS (1 hour):
  if balance > DEFAULT_BALANCE:
    balance = max(DEFAULT_BALANCE, balance - SOFT_DECAY_RATE)
```

- Balance **never drops below 100.0** from soft decay alone
- Identity remains authenticated indefinitely while absent
- Long-established identities trend back toward baseline but keep auth status

### Hard Decay (ignoring challenges — can revoke)

Applies when a challenge was explicitly issued but the window expired with no response:

```
On challenge expiry (no response within 5 min):
  balance -= IGNORE_PENALTY (15.0)
  if balance < AUTH_THRESHOLD (1.0):
    revoked = True
```

- Balance **can go below DEFAULT_BALANCE** and all the way to 0
- Repeated ignoring causes progressive revocation
- At balance 0 → identity is permanently denied until manual review

### The Key Distinction

| Situation | Decay type | Floor | Auth status |
|-----------|-----------|-------|-------------|
| Just offline / inactive | Soft | DEFAULT (100) | Maintained |
| Challenge issued, ignored | Hard | 0 | Revoked if balance hits 0 |

---

## Transaction Types

| Type | Value | Description |
|------|-------|-------------|
| `IDENTITY_REGISTER` | 10 | Register new identity with PoW proof |
| `REPUTATION_MINE` | 11 | Solved reputation challenge — balance grows |
| `REPUTATION_IGNORE` | 12 | Expired unresponded challenge — balance penalized |

These extend the base `TxTypes` from request-chain (COINBASE=0, REQUEST=1, RELEASE=2, TRANSFER=3, BUYOUT_OFFER=4).

---

## PoW Parameters

```python
REGISTRATION_DIFFICULTY_BITS = 20   # ~1M iterations — one-time cost per identity
REPUTATION_DIFFICULTY_BITS   = 14   # ~16K iterations — lightweight, proves liveness
```

Both use SHA-256. The registration PoW is embedded in the `IDENTITY_REGISTER` transaction and verified by all nodes before the transaction is accepted into the mempool.

---

## Network Messages

In addition to the base request-chain messages (`NEW_BLOCK`, `NEW_TRANSACTION`, `REQUEST_CHAIN`, `CHAIN_RESPONSE`), two new message types are added:

| Message | Direction | Payload |
|---------|-----------|---------|
| `REP_CHALLENGE` | Node → network | `{target_pubkey, challenge_data_hex, difficulty_bits, issued_at, expires_in, issuer_pubkey}` |
| `REP_SOLUTION` | Identity → network | `{tx: Transaction, challenge_data_hex, nonce, solution_hash_hex}` |

Challenge flow:
```
Issuer node                    Target identity node
     │                                │
     │──── REP_CHALLENGE ────────────►│
     │     (broadcast to all peers)   │
     │                                │  solve PoW (14-bit)
     │◄─── REP_SOLUTION ─────────────│
     │     (broadcast)                │
     │                                │
  mark_resolved()           REPUTATION_MINE tx → mempool
  (no IGNORE tx needed)
```

If no solution arrives before `expires_in` (300s default):
```
Issuer node
     │
  check_expired()
     │
  make_reputation_ignore_tx() → mempool
     │
  broadcast REPUTATION_IGNORE tx
```

---

## Challenge Selection Strategy

The `PeerChallengeManager` uses **inverse-balance weighted random selection**:

- Lower balance identities have higher probability of being selected
- This means new identities build reputation faster
- High-balance trusted identities are challenged less often (they've proven themselves)
- One pending challenge per identity at a time (no flooding)
- A node never challenges itself

```python
weights = [max_balance - r.balance + 1.0 for r in candidates]
target = random.choices(candidates, weights=weights, k=1)[0]
```

---

## File Structure

```
requestchain-identity/
├── identity_peer.py          # Main node entry point (CLI)
├── identity/
│   ├── __init__.py
│   ├── identity.py           # IdentityBlockchain (extends base)
│   ├── pow.py                # PoW engine (registration + reputation)
│   ├── reputation.py         # ReputationRecord + ReputationEngine
│   └── peer_challenge.py     # PeerChallengeManager
├── blockchain/               # request-chain base (unchanged)
│   ├── __init__.py
│   ├── blockchain.py
│   ├── network.py
│   ├── security.py
│   └── db.py
├── requirements.txt
└── README.md
```

---

## Running the Peer

### Prerequisites

```bash
pip install -r requirements.txt
```

Node.js (v18 or newer) is required only for the Electron GUI.

### CLI peer

The terminal interface is the simplest way to get started.

```bash
# Start on the default port (6000)
python anonity/identity_peer.py

# Or pick a specific port
python anonity/identity_peer.py 6001
```

**First-time setup**

1. On first launch your EC keypair is generated and saved to `~/.databox/identity/my_key.pkl`. It persists across restarts — the same keypair is loaded automatically each time.
2. Choose **option 1 — Register Identity**. The node solves a 20-bit proof-of-work automatically (takes 1–5 seconds) and mines a block to confirm the registration. Your starting balance is 100.0.
3. Your node is now live. It will automatically issue and respond to reputation challenges every two minutes. Use **option 4** to watch your balance grow.

### Electron desktop GUI

The graphical interface controls the peer through a local REST API backend (`anonity/identity_api.py`). Electron spawns that server automatically — no manual startup needed.

```bash
cd electron
npm install
npm start
```

To connect two nodes, start a second instance on a different port:

```bash
npm start -- 6001
```

Then use the **Connect to Peer** button and enter `localhost:6000`.

### Two-node CLI network

```bash
# Terminal 1
python anonity/identity_peer.py 6000

# Terminal 2
python anonity/identity_peer.py 6001
# Select 5 → connect to localhost:6000
# Select 7 → sync chain
```

Both nodes will automatically issue and respond to reputation challenges every 2 minutes. Balance changes propagate via `REPUTATION_MINE` and `REPUTATION_IGNORE` transactions mined into blocks.

---

## Persistence

| Data | Path |
|------|------|
| Identity chain | `~/.databox/identity/identity_chain.pkl` |
| This node's keypair | `~/.databox/identity/my_key.pkl` |

The keypair is persistent across restarts — your identity survives node restarts. The chain is also persisted and reloaded on startup.

---

## Constants Reference

```python
# identity/reputation.py
DEFAULT_BALANCE     = 100.0    # Starting balance for new identity
AUTH_THRESHOLD      = 1.0      # Minimum balance to authenticate
MINING_REWARD       = 10.0     # Balance gained per solved challenge
SOFT_DECAY_RATE     = 2.0      # Balance lost per hour (inactivity, floor=default)
IGNORE_PENALTY      = 15.0     # Balance lost per ignored challenge (no floor)
DECAY_TICK_SECONDS  = 3600.0   # 1 hour decay tick

# identity/pow.py
REGISTRATION_DIFFICULTY_BITS = 20   # ~1M iterations
REPUTATION_DIFFICULTY_BITS   = 14   # ~16K iterations
```

---

## Design Decisions & Tradeoffs

**Why EC keys instead of PGP keys?**
The request-chain base already uses SECP256R1 EC keys for transaction signing. Using the same key type means zero additional dependencies and seamless integration with the existing signature verification infrastructure. Functionally, an EC public key serves the same role as a PGP public key for our purposes.

**Why is registration PoW the Sybil mitigation?**
The impossibility triangle (strong anonymity + zero cost + hard Sybil resistance) means we must sacrifice something. By requiring PoW at registration, we impose a real CPU cost without requiring identity or payment. Legitimate users pay once; Sybil farmers pay per fake key.

**Why does soft decay have a floor?**
Legitimate users have lives. A trusted long-standing identity should not lose authentication because they went on vacation. The hard decay path (ignored challenges) is reserved for identities that are actively online but unresponsive — a behavioral signal of bad acting.

**Why weighted random challenge selection?**
Flat random would be unfair to new identities — they'd be challenged at the same rate as established ones but with less buffer. Inverse-balance weighting means the network naturally helps new identities build reputation quickly while letting established ones coast.

---

## Next Steps

- **Blind issuance** for multi-key users who need unlinkable identities
- **Honeypot challenges** (node injects synthetic challenges to detect scripted responders)
- **Temporal maturation** (new identities have limited privileges for N blocks)
- **Key rotation** (allow an identity to transfer its balance to a new key with PoW proof)