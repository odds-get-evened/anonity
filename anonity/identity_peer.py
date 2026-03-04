"""
identity_peer.py

Anonymous Identity Authenticator — Peer Node

This is the main entry point for a node in the identity network.

Each node:
  1. Generates (or loads) its own EC keypair — this IS the identity.
  2. Registers that identity on the blockchain with a mandatory PoW proof.
  3. Participates in the P2P network, issuing and responding to reputation
     mining challenges.
  4. Can authenticate any public key against the chain.

Usage:
    python identity_peer.py [port]

    Default port: 6000

Architecture:
  - IdentityBlockchain  (identity/identity.py) — extended blockchain with
    IDENTITY_REGISTER, REPUTATION_MINE, REPUTATION_IGNORE tx types
  - PeerChallengeManager (identity/peer_challenge.py) — issues and tracks
    reputation mining challenges
  - P2PNetwork (blockchain/network.py) — reused P2P layer from request-chain

New P2P message types added:
  REP_CHALLENGE   — issued challenge broadcast
  REP_SOLUTION    — solution broadcast (triggers REPUTATION_MINE tx)
"""

import atexit
import json
import signal
import sys
import threading
import time
from enum import StrEnum
from pathlib import Path
from queue import Empty, Queue

from cryptography.hazmat.primitives.asymmetric import ec

from blockchain.blockchain import serialize_pubkey, TxTypes, Transaction
from blockchain.network import P2PNetwork, Message, MessageType
from identity.identity import (
    IdentityBlockchain, IdentityTxTypes,
    make_registration_tx, make_reputation_mine_tx, make_reputation_ignore_tx,
)
from identity.peer_challenge import PeerChallengeManager, ChallengeMessage
from identity.pow import (
    solve_registration, solve_reputation,
    REGISTRATION_DIFFICULTY_BITS, REPUTATION_DIFFICULTY_BITS,
)
from identity.reputation import DEFAULT_BALANCE, AUTH_THRESHOLD


# ---------------------------------------------------------------------------
# Extended message types for reputation challenges
# ---------------------------------------------------------------------------

class RepMessageType(StrEnum):
    REP_CHALLENGE = "rep_challenge"    # Node → network: challenge an identity
    REP_SOLUTION  = "rep_solution"     # Identity → network: solved challenge


# ---------------------------------------------------------------------------
# Paths & globals
# ---------------------------------------------------------------------------

SNAP_PATH = Path.home().joinpath('.databox', 'identity', 'identity_chain.pkl')
KEY_PATH  = Path.home().joinpath('.databox', 'identity', 'my_key.pkl')
SNAP_PATH.parent.mkdir(parents=True, exist_ok=True)

status_q: Queue = Queue()


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def save_keypair(priv_key: ec.EllipticCurvePrivateKey, p: Path):
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PrivateFormat, NoEncryption,
    )
    pem = priv_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    with open(p, 'wb') as fh:
        fh.write(pem)


def load_keypair(p: Path) -> ec.EllipticCurvePrivateKey | None:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    if p.exists():
        with open(p, 'rb') as fh:
            try:
                return load_pem_private_key(fh.read(), password=None)
            except Exception:
                pass
    return None


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

def log(msg: str):
    status_q.put(msg)


def drain_log():
    try:
        while True:
            print(status_q.get_nowait())
    except Empty:
        pass


def prompt(msg: str) -> str:
    drain_log()
    return input(msg)


def print_separator(title: str = ""):
    w = 60
    if title:
        pad = (w - len(title) - 2) // 2
        print("─" * pad + f" {title} " + "─" * pad)
    else:
        print("─" * w)


# ---------------------------------------------------------------------------
# Registration flow
# ---------------------------------------------------------------------------

def register_identity(
    chain: IdentityBlockchain,
    priv_key: ec.EllipticCurvePrivateKey,
) -> bool:
    """
    Register this node's identity on the chain.

    Steps:
      1. Solve registration PoW (expensive — this is the Sybil cost)
      2. Build & sign IDENTITY_REGISTER transaction
      3. Add to chain mempool
      4. Mine a block to confirm it

    Returns True if successful.
    """
    pub_key = priv_key.public_key()
    pubkey_hex = serialize_pubkey(pub_key)

    print()
    print_separator("IDENTITY REGISTRATION")
    print(f"  Public key : {pubkey_hex[:32]}…")
    print(f"  PoW target : {REGISTRATION_DIFFICULTY_BITS} leading zero bits")
    print(f"  Solving registration PoW — this will take a moment…")

    start = time.time()
    seed, nonce, solution_hash = solve_registration(pubkey_hex)
    elapsed = time.time() - start

    print(f"  ✅ PoW solved in {elapsed:.2f}s  (nonce={nonce})")
    print(f"  Solution   : {solution_hash.hex()[:32]}…")

    tx = make_registration_tx(priv_key, seed, nonce)

    if not chain.register_identity(tx):
        print("  ❌ Registration transaction rejected by chain")
        return False

    print("  ⛏️  Mining registration block…")
    block = chain.mine_identity_block(pub_key)

    if block is None:
        print("  ❌ Failed to mine registration block")
        return False

    print(f"  ✅ Identity registered in block #{block.index}")
    print_separator()
    return True


# ---------------------------------------------------------------------------
# Challenge response (our own identity being challenged)
# ---------------------------------------------------------------------------

def respond_to_challenge(
    priv_key: ec.EllipticCurvePrivateKey,
    chain: IdentityBlockchain,
    p2p: P2PNetwork,
    msg: ChallengeMessage,
):
    """Solve and respond to an incoming reputation challenge directed at us."""
    challenge = msg.to_challenge()

    if challenge.is_expired:
        log("⚠️  Received challenge already expired — ignoring")
        return

    pub_key = priv_key.public_key()
    log(f"🎯 Received reputation challenge from {msg.issuer_pubkey[:16]}… — solving…")

    result = solve_reputation(challenge)
    if result is None:
        log("❌ Failed to solve reputation challenge")
        return

    nonce, solution_hash = result
    log(f"✅ Challenge solved (nonce={nonce}) — broadcasting solution…")

    tx = make_reputation_mine_tx(priv_key, challenge, nonce)

    # Add to our own mempool
    chain.submit_reputation_solution(tx)

    # Broadcast solution
    solution_payload = {
        'tx': tx.to_full_dict(),
        'challenge_data_hex': msg.challenge_data_hex,
        'nonce': nonce,
        'solution_hash_hex': solution_hash.hex(),
    }
    p2p.broadcast(Message(RepMessageType.REP_SOLUTION, solution_payload))
    log("📤 Solution broadcast to network")


# ---------------------------------------------------------------------------
# P2P network setup
# ---------------------------------------------------------------------------

def setup_network(
    p2p: P2PNetwork,
    chain: IdentityBlockchain,
    priv_key: ec.EllipticCurvePrivateKey,
    challenge_manager: PeerChallengeManager,
):
    """Wire all P2P callbacks including reputation message handling."""

    pub_key = priv_key.public_key()
    pubkey_hex = serialize_pubkey(pub_key)

    # --- Existing base callbacks ---

    def handle_new_block(block_data: dict):
        try:
            from blockchain.blockchain import deserialize_pubkey
            txs = []
            for tx_dict in block_data.get('transactions', []):
                p = deserialize_pubkey(tx_dict['requester'])
                tx = Transaction(
                    p, tx_dict['uid'], tx_dict['type'],
                    tx_dict.get('timestamp'), tx_dict.get('signature'),
                    tx_dict.get('amount', 0.0),
                    tx_dict.get('recipient'),
                    tx_dict.get('accepted_offer'),
                )
                txs.append(tx)

            if len(chain.chain) == block_data['index']:
                chain.add_block(txs)
                chain._rebuild_item_tracking()
                # Rebuild identity state from new block
                for tx in txs:
                    if tx.tx_type in (
                        IdentityTxTypes.IDENTITY_REGISTER,
                        IdentityTxTypes.REPUTATION_MINE,
                        IdentityTxTypes.REPUTATION_IGNORE,
                    ):
                        chain._apply_identity_tx(tx)
                chain.snapshot_identity(SNAP_PATH)
                log(f"📦 Received block #{block_data['index']}")
        except Exception as e:
            log(f"❌ Failed to process block: {e}")

    def handle_chain_request() -> dict:
        return {
            'chain': [b.to_full_dict() for b in chain.chain],
            'length': len(chain.chain),
        }

    def handle_chain_response(response_data: dict):
        try:
            peer_chain = response_data.get('chain', [])
            peer_length = response_data.get('length', 0)
            log(f"📡 Chain sync: peer={peer_length} ours={len(chain.chain)}")
            if chain.replace_chain(peer_chain):
                log(f"✅ Adopted longer chain ({peer_length} blocks) — rebuilding identity state")
                chain._rebuild_item_tracking()
                # Rebuild identity state from full chain
                chain.identities.clear()
                for block in chain.chain:
                    for tx in block.transactions:
                        if tx.tx_type in (
                            IdentityTxTypes.IDENTITY_REGISTER,
                            IdentityTxTypes.REPUTATION_MINE,
                            IdentityTxTypes.REPUTATION_IGNORE,
                        ):
                            chain._apply_identity_tx(tx)
                chain.snapshot_identity(SNAP_PATH)
            else:
                log("ℹ️  Kept current chain (already longest or peer chain invalid)")
        except Exception as e:
            log(f"❌ Chain sync error: {e}")

    def handle_new_transaction(tx_data: dict):
        try:
            from blockchain.blockchain import deserialize_pubkey
            p = deserialize_pubkey(tx_data['requester'])
            tx = Transaction(
                p, tx_data['uid'], tx_data['type'],
                tx_data.get('timestamp'), tx_data.get('signature'),
                tx_data.get('amount', 0.0),
            )
            tx_type = tx_data['type']
            if tx_type == IdentityTxTypes.IDENTITY_REGISTER:
                chain.register_identity(tx)
            elif tx_type == IdentityTxTypes.REPUTATION_MINE:
                chain.submit_reputation_solution(tx)
            elif tx_type == IdentityTxTypes.REPUTATION_IGNORE:
                chain.record_ignore(tx)
            else:
                chain.add_to_mempool(tx)
            log(f"📨 Received transaction type={tx_type} uid={tx_data.get('uid','')[:24]}…")
        except Exception as e:
            log(f"❌ Failed to process transaction: {e}")

    p2p.on_new_block = handle_new_block
    p2p.on_chain_request = handle_chain_request
    p2p.on_chain_response = handle_chain_response
    p2p.on_new_transaction = handle_new_transaction

    # --- Reputation message extensions ---
    # Monkey-patch the router to handle REP_* messages

    original_route = p2p._route_message

    def extended_route(msg: Message, peer):
        if msg.type == RepMessageType.REP_CHALLENGE:
            # Incoming challenge — is it for us?
            try:
                cm = ChallengeMessage.from_dict(msg.payload)
                incoming = challenge_manager.receive_challenge(cm)
                if incoming is not None:
                    # It's for us — solve it in a background thread
                    threading.Thread(
                        target=respond_to_challenge,
                        args=(priv_key, chain, p2p, cm),
                        daemon=True,
                    ).start()
                # Store in chain's pending_challenges for other identities too
                challenge_obj = cm.to_challenge()
                chain.pending_challenges[cm.target_pubkey] = challenge_obj
            except Exception as e:
                log(f"❌ REP_CHALLENGE handling error: {e}")

        elif msg.type == RepMessageType.REP_SOLUTION:
            # Incoming solution — validate and mark resolved
            try:
                tx_data = msg.payload.get('tx', {})
                target_hex = tx_data.get('requester', '')
                challenge_manager.mark_resolved(target_hex)

                # Add the REPUTATION_MINE tx to our mempool if valid
                from blockchain.blockchain import deserialize_pubkey
                p_key = deserialize_pubkey(tx_data['requester'])
                tx = Transaction(
                    p_key, tx_data['uid'],
                    IdentityTxTypes.REPUTATION_MINE,
                    tx_data.get('timestamp'),
                    tx_data.get('signature'),
                )
                chain.submit_reputation_solution(tx)
                log(f"📨 Received reputation solution from {target_hex[:16]}…")
            except Exception as e:
                log(f"❌ REP_SOLUTION handling error: {e}")

        else:
            original_route(msg, peer)

    p2p._route_message = extended_route

    # --- Challenge manager callbacks ---

    def on_challenge_to_issue(cm: ChallengeMessage):
        """Broadcast a newly issued challenge."""
        log(f"🎯 Issuing reputation challenge → {cm.target_pubkey[:16]}…")
        p2p.broadcast(Message(RepMessageType.REP_CHALLENGE, cm.to_dict()))

    def on_ignore_to_report(target_pubkey: str, challenge):
        """Submit a REPUTATION_IGNORE tx for an expired unresponded challenge."""
        log(f"⚠️  Challenge expired for {target_pubkey[:16]}… — submitting ignore penalty")
        try:
            ignore_tx = make_reputation_ignore_tx(priv_key, target_pubkey, challenge)
            if chain.record_ignore(ignore_tx):
                p2p.announce_new_transaction(ignore_tx.to_full_dict())
                log(f"📤 REPUTATION_IGNORE broadcast for {target_pubkey[:16]}…")
        except Exception as e:
            log(f"❌ Failed to report ignore: {e}")

    challenge_manager.on_challenge_to_issue = on_challenge_to_issue
    challenge_manager.on_ignore_to_report = on_ignore_to_report


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------

def start_background_tasks(chain: IdentityBlockchain, p2p: P2PNetwork):
    """Start integrity monitor, inactivity decay, and auto-sync."""

    def integrity_monitor():
        last_ok = None
        while True:
            ok = chain.integrity_check()
            if last_ok is None or ok != last_ok:
                if ok:
                    log("😁 Chain integrity: OK")
                else:
                    log("⚠️  Chain integrity: CORRUPT — repairing…")
                    if chain.repair():
                        log("✅ Repair completed")
                    else:
                        log("❌ Repair failed")
                last_ok = ok
            time.sleep(30)

    def decay_loop():
        while True:
            time.sleep(300)  # Every 5 minutes
            chain.apply_inactivity_decay()

    def auto_sync():
        time.sleep(15)
        while True:
            if p2p.peers:
                p2p.request_chain_from_peers()
            time.sleep(60)

    threading.Thread(target=integrity_monitor, daemon=True).start()
    threading.Thread(target=decay_loop, daemon=True).start()
    threading.Thread(target=auto_sync, daemon=True).start()


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------

MENU = """
╔══════════════════════════════════════════╗
║   Anonymous Identity Authenticator       ║
╠══════════════════════════════════════════╣
║  1. Register this node's identity        ║
║  2. Authenticate a public key            ║
║  3. List all identities                  ║
║  4. Show my identity & reputation        ║
║  5. Connect to peer                      ║
║  6. List connected peers                 ║
║  7. Sync chain from network              ║
║  8. Mine pending transactions            ║
║  9. Blockchain status                    ║
║ 10. Issue reputation challenge (manual)  ║
║ 11. Exit                                 ║
╚══════════════════════════════════════════╝
"""


def show_identity_list(chain: IdentityBlockchain):
    records = chain.list_identities()
    if not records:
        print("  No identities registered yet.")
        return
    print_separator("REGISTERED IDENTITIES")
    for r in records:
        status = "✅ AUTH" if r.is_authenticated else "❌ REVOKED"
        bar_len = min(40, int((r.balance / DEFAULT_BALANCE) * 20))
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(
            f"  {r.pubkey_hex[:20]}…  "
            f"[{bar}] {r.balance:6.1f}  "
            f"solved={r.solved_count}  ignored={r.ignored_count}  "
            f"{status}"
        )
    print_separator()


def show_my_identity(
    chain: IdentityBlockchain,
    pubkey_hex: str,
):
    record = chain.get_identity(pubkey_hex)
    print_separator("MY IDENTITY")
    print(f"  Public key  : {pubkey_hex}")
    if record:
        print(f"  Balance     : {record.balance:.1f}  (default={DEFAULT_BALANCE:.0f}, threshold={AUTH_THRESHOLD:.0f})")
        print(f"  Solved      : {record.solved_count} challenges")
        print(f"  Ignored     : {record.ignored_count} challenges")
        print(f"  Registered  : {time.ctime(record.registered_at)}")
        print(f"  Status      : {'✅ AUTHENTICATED' if record.is_authenticated else '❌ REVOKED'}")
    else:
        print("  ⚠️  Not yet registered on this chain")
    print_separator()


def authenticate_pubkey(chain: IdentityBlockchain):
    key_input = prompt("  Enter public key hex to authenticate: ").strip()
    result = chain.authenticate(key_input)
    if result:
        record = chain.get_identity(key_input)
        print(f"  ✅ AUTHENTICATED  (balance={record.balance:.1f})")
    else:
        record = chain.get_identity(key_input)
        if record:
            print(f"  ❌ DENIED — balance={record.balance:.1f} (threshold={AUTH_THRESHOLD:.0f}), revoked={record.revoked}")
        else:
            print("  ❌ DENIED — identity not registered on this chain")


def manual_challenge(
    chain: IdentityBlockchain,
    challenge_manager: PeerChallengeManager,
):
    """Manually issue a reputation challenge to a specific identity."""
    identities = chain.list_identities()
    if not identities:
        print("  No identities to challenge.")
        return

    print("  Authenticated identities:")
    auth = [r for r in identities if r.is_authenticated and r.pubkey_hex != challenge_manager.my_pubkey_hex]
    for i, r in enumerate(auth):
        print(f"  [{i}] {r.pubkey_hex[:32]}…  balance={r.balance:.1f}")

    if not auth:
        print("  No other authenticated identities available to challenge.")
        return

    choice = prompt("  Select identity index: ").strip()
    try:
        idx = int(choice)
        target = auth[idx]
    except (ValueError, IndexError):
        print("  Invalid selection.")
        return

    msg = challenge_manager.issue_challenge(target)
    if msg is None:
        print("  ⚠️  Challenge already pending for this identity.")
        return

    if challenge_manager.on_challenge_to_issue:
        challenge_manager.on_challenge_to_issue(msg)
        print(f"  ✅ Challenge issued to {target.pubkey_hex[:32]}…")
    else:
        print("  ⚠️  Network not connected — challenge stored locally only")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 6000

    print("=" * 60)
    print("  Anonymous Identity Authenticator")
    print(f"  Node port: {port}")
    print("=" * 60)

    # Load or generate keypair
    priv_key = load_keypair(KEY_PATH)
    if priv_key:
        print("  🔑 Loaded existing keypair from disk")
    else:
        priv_key = ec.generate_private_key(ec.SECP256R1())
        save_keypair(priv_key, KEY_PATH)
        print("  🔑 Generated new keypair — saved to disk")

    pub_key = priv_key.public_key()
    pubkey_hex = serialize_pubkey(pub_key)
    print(f"  Identity : {pubkey_hex[:40]}…")

    # Load or create identity chain
    chain = IdentityBlockchain.init_identity(SNAP_PATH)
    print(f"  Chain    : {len(chain.chain)} blocks, {len(chain.identities)} identities")

    # P2P network
    p2p = P2PNetwork(host="0.0.0.0", port=port)

    # Challenge manager
    challenge_manager = PeerChallengeManager(
        my_pubkey_hex=pubkey_hex,
        challenge_interval=120.0,
        challenge_window=300.0,
    )

    # Wire everything up
    setup_network(p2p, chain, priv_key, challenge_manager)
    p2p.start()

    # Cleanup handlers
    def cleanup():
        chain.snapshot_identity(SNAP_PATH)
        save_keypair(priv_key, KEY_PATH)
        p2p.stop()
        print("\n  Goodbye — identity and chain saved.")

    atexit.register(cleanup)
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))

    # Background tasks
    start_background_tasks(chain, p2p)

    # Start challenge manager (issues challenges automatically)
    challenge_manager.start(get_identities=chain.list_identities)

    print()

    # Main loop
    while True:
        print(MENU)
        choice_raw = prompt("select > ").strip()

        try:
            choice = int(choice_raw)
        except ValueError:
            print("  Invalid input — enter a number")
            continue

        if choice == 1:
            # Register identity
            if chain.authenticate(pubkey_hex):
                print(f"  ℹ️  Already registered  (balance={chain.get_identity(pubkey_hex).balance:.1f})")
            else:
                ok = register_identity(chain, priv_key)
                if ok:
                    p2p.announce_new_block(chain.chain[-1].to_full_dict())
                    chain.snapshot_identity(SNAP_PATH)

        elif choice == 2:
            authenticate_pubkey(chain)

        elif choice == 3:
            show_identity_list(chain)

        elif choice == 4:
            show_my_identity(chain, pubkey_hex)

        elif choice == 5:
            host = prompt("  peer host: ").strip()
            peer_port_raw = prompt("  peer port: ").strip()
            try:
                peer_port = int(peer_port_raw)
                p2p.connect_to_peer(host, peer_port)
                print(f"  🔗 Connecting to {host}:{peer_port}…")
            except ValueError:
                print("  Invalid port number")

        elif choice == 6:
            if p2p.peers:
                print(f"  Connected peers ({len(p2p.peers)}):")
                for peer in p2p.peers:
                    print(f"    - {peer.address}")
            else:
                print("  No peers connected")

        elif choice == 7:
            if p2p.peers:
                print("  📡 Requesting chain sync from peers…")
                p2p.request_chain_from_peers()
            else:
                print("  ⚠️  No peers connected")

        elif choice == 8:
            if chain.mempool:
                print(f"  ⛏️  Mining {len(chain.mempool)} pending transaction(s)…")
                block = chain.mine_identity_block(pub_key)
                if block:
                    print(f"  ✅ Mined block #{block.index}")
                    p2p.announce_new_block(block.to_full_dict())
                    chain.snapshot_identity(SNAP_PATH)
                else:
                    print("  ❌ Mining failed")
            else:
                print("  ℹ️  Mempool is empty — nothing to mine")

        elif choice == 9:
            ok = chain.integrity_check()
            print_separator("BLOCKCHAIN STATUS")
            print(f"  Integrity  : {'✅ OK' if ok else '❌ CORRUPT'}")
            print(f"  Blocks     : {len(chain.chain)}")
            print(f"  Identities : {len(chain.identities)}")
            print(f"  Mempool    : {len(chain.mempool)} pending tx(s)")
            print(f"  Peers      : {len(p2p.peers)} connected")
            authenticated = sum(1 for r in chain.identities.values() if r.is_authenticated)
            revoked = sum(1 for r in chain.identities.values() if not r.is_authenticated)
            print(f"  Auth       : {authenticated} active, {revoked} revoked")
            print_separator()

        elif choice == 10:
            manual_challenge(chain, challenge_manager)

        elif choice == 11:
            break

        else:
            print("  Invalid option")

    print("  Shutting down…")


if __name__ == "__main__":
    main()