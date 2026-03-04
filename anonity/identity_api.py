"""
identity_api.py

Anonymous Identity Authenticator — HTTP API Server

Wraps the identity peer as a Flask REST API so the Electron desktop
GUI (or any HTTP client) can drive the node without a terminal.

Endpoints
---------
GET  /api/status            Node health & summary stats
GET  /api/identities        All registered identities
GET  /api/my-identity       This node's own identity record
POST /api/register          Start registration PoW (async)
POST /api/authenticate      Check whether a public key passes auth
POST /api/connect           Connect to a peer { host, port }
GET  /api/peers             List connected peers
POST /api/sync              Request chain sync from peers
POST /api/mine              Mine pending mempool transactions (async)
POST /api/challenge         Issue a reputation challenge { target_pubkey }
GET  /api/logs?since=N      Return log entries from index N onwards

Usage:
    python identity_api.py [p2p_port [api_port]]

    p2p_port default: 6000
    api_port default: 5001
"""

import atexit
import json
import sys
import threading
import time
from enum import StrEnum
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec
from flask import Flask, jsonify, request

from blockchain.blockchain import serialize_pubkey, Transaction
from blockchain.network import P2PNetwork, Message
from identity.identity import (
    IdentityBlockchain, IdentityTxTypes,
    make_registration_tx, make_reputation_mine_tx, make_reputation_ignore_tx,
)
from identity.peer_challenge import PeerChallengeManager, ChallengeMessage
from identity.pow import solve_registration, solve_reputation, REGISTRATION_DIFFICULTY_BITS
from identity.reputation import DEFAULT_BALANCE, AUTH_THRESHOLD


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SNAP_PATH = Path.home() / '.databox' / 'identity' / 'identity_chain.pkl'
KEY_PATH  = Path.home() / '.databox' / 'identity' / 'my_key.pkl'
SNAP_PATH.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Reputation message types (mirrors identity_peer.py)
# ---------------------------------------------------------------------------

class RepMessageType(StrEnum):
    REP_CHALLENGE = "rep_challenge"
    REP_SOLUTION  = "rep_solution"


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
# Log buffer  (polled by /api/logs)
# ---------------------------------------------------------------------------

_log_buffer: list[dict] = []
_log_lock = threading.Lock()


def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    with _log_lock:
        _log_buffer.append({"ts": ts, "msg": msg})


# ---------------------------------------------------------------------------
# Incoming challenge event buffer  (polled by /api/incoming-challenges)
# ---------------------------------------------------------------------------

_challenge_events: list[dict] = []
_challenge_events_lock = threading.Lock()


def _add_challenge_event(event: dict):
    with _challenge_events_lock:
        _challenge_events.append(event)


# ---------------------------------------------------------------------------
# Shared peer state
# ---------------------------------------------------------------------------

state: dict = {
    'priv_key':          None,
    'pub_key':           None,
    'pubkey_hex':        '',
    'chain':             None,
    'p2p':               None,
    'challenge_manager': None,
}


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)


@app.after_request
def _cors(response):
    response.headers['Access-Control-Allow-Origin']  = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response


# ── Status ──────────────────────────────────────────────────────────────────

@app.route('/api/status')
def api_status():
    chain      = state['chain']
    p2p        = state['p2p']
    pubkey_hex = state['pubkey_hex']

    if chain is None:
        return jsonify({'ready': False})

    record     = chain.get_identity(pubkey_hex) if pubkey_hex else None
    auth_count = sum(1 for r in chain.identities.values() if r.is_authenticated)
    rev_count  = sum(1 for r in chain.identities.values() if not r.is_authenticated)

    return jsonify({
        'ready':         True,
        'pubkey':        pubkey_hex,
        'balance':       record.balance if record else None,
        'authenticated': record.is_authenticated if record else False,
        'blocks':        len(chain.chain),
        'peers':         len(p2p.peers) if p2p else 0,
        'mempool':       len(chain.mempool),
        'identities':    len(chain.identities),
        'auth_count':    auth_count,
        'rev_count':     rev_count,
        'integrity':     chain.integrity_check(),
    })


# ── Identities ───────────────────────────────────────────────────────────────

@app.route('/api/identities')
def api_identities():
    chain = state['chain']
    if chain is None:
        return jsonify([])
    return jsonify([
        {
            'pubkey':        r.pubkey_hex,
            'balance':       r.balance,
            'solved':        r.solved_count,
            'ignored':       r.ignored_count,
            'authenticated': r.is_authenticated,
            'revoked':       r.revoked,
            'registered_at': r.registered_at,
        }
        for r in chain.list_identities()
    ])


@app.route('/api/my-identity')
def api_my_identity():
    chain      = state['chain']
    pubkey_hex = state['pubkey_hex']
    if chain is None or not pubkey_hex:
        return jsonify({'error': 'Not initialised'}), 503

    record = chain.get_identity(pubkey_hex)
    if not record:
        return jsonify({'pubkey': pubkey_hex, 'registered': False})

    return jsonify({
        'pubkey':        pubkey_hex,
        'registered':    True,
        'balance':       record.balance,
        'solved':        record.solved_count,
        'ignored':       record.ignored_count,
        'registered_at': record.registered_at,
        'authenticated': record.is_authenticated,
        'revoked':       record.revoked,
    })


# ── Register ─────────────────────────────────────────────────────────────────

@app.route('/api/register', methods=['POST', 'OPTIONS'])
def api_register():
    if request.method == 'OPTIONS':
        return '', 204

    chain      = state['chain']
    priv_key   = state['priv_key']
    pub_key    = state['pub_key']
    pubkey_hex = state['pubkey_hex']
    p2p        = state['p2p']

    if chain is None:
        return jsonify({'error': 'Not initialised'}), 503

    if chain.authenticate(pubkey_hex):
        record = chain.get_identity(pubkey_hex)
        # Only block if the identity has non-default reputation; at exactly
        # DEFAULT_BALANCE (no reputation earned/lost) allow a fresh registration.
        if record and record.balance != DEFAULT_BALANCE:
            return jsonify({'already_registered': True, 'balance': record.balance})

    def _do_register():
        try:
            log(f"Solving registration PoW ({REGISTRATION_DIFFICULTY_BITS}-bit)…")
            start = time.time()
            seed, nonce, solution_hash = solve_registration(pubkey_hex)
            elapsed = time.time() - start
            log(f"PoW solved in {elapsed:.2f}s  (nonce={nonce})")

            tx = make_registration_tx(priv_key, seed, nonce)
            if not chain.register_identity(tx):
                log("Registration transaction rejected by chain")
                return

            log("Mining registration block…")
            block = chain.mine_identity_block(pub_key)
            if block is None:
                log("Failed to mine registration block")
                return

            log(f"Identity registered in block #{block.index}")
            p2p.announce_new_block(chain.chain[-1].to_full_dict())
            chain.snapshot_identity(SNAP_PATH)
        except Exception as e:
            log(f"Registration error: {e}")

    threading.Thread(target=_do_register, daemon=True).start()
    return jsonify({'started': True})


# ── Authenticate ─────────────────────────────────────────────────────────────

@app.route('/api/authenticate', methods=['POST', 'OPTIONS'])
def api_authenticate():
    if request.method == 'OPTIONS':
        return '', 204

    chain = state['chain']
    if chain is None:
        return jsonify({'error': 'Not initialised'}), 503

    data    = request.get_json(silent=True) or {}
    key_hex = data.get('pubkey', '').strip()
    if not key_hex:
        return jsonify({'error': 'pubkey required'}), 400

    result = chain.authenticate(key_hex)
    record = chain.get_identity(key_hex)
    return jsonify({
        'authenticated': result,
        'balance':       record.balance  if record else None,
        'revoked':       record.revoked  if record else None,
        'registered':    record is not None,
    })


# ── Peers ─────────────────────────────────────────────────────────────────────

@app.route('/api/connect', methods=['POST', 'OPTIONS'])
def api_connect():
    if request.method == 'OPTIONS':
        return '', 204

    p2p = state['p2p']
    if p2p is None:
        return jsonify({'error': 'Not initialised'}), 503

    data = request.get_json(silent=True) or {}
    host = data.get('host', '').strip()
    port = data.get('port')
    if not host or port is None:
        return jsonify({'error': 'host and port required'}), 400

    try:
        p2p.connect_to_peer(host, int(port))
        log(f"Connecting to {host}:{port}…")
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/peers')
def api_peers():
    p2p = state['p2p']
    if p2p is None:
        return jsonify([])
    return jsonify([str(p.address) for p in p2p.peers])


# ── Chain operations ──────────────────────────────────────────────────────────

@app.route('/api/sync', methods=['POST', 'OPTIONS'])
def api_sync():
    if request.method == 'OPTIONS':
        return '', 204

    p2p = state['p2p']
    if p2p is None:
        return jsonify({'error': 'Not initialised'}), 503
    if not p2p.peers:
        return jsonify({'error': 'No peers connected'}), 400

    log("Requesting chain sync from peers…")
    p2p.request_chain_from_peers()
    return jsonify({'ok': True})


@app.route('/api/mine', methods=['POST', 'OPTIONS'])
def api_mine():
    if request.method == 'OPTIONS':
        return '', 204

    chain   = state['chain']
    pub_key = state['pub_key']
    p2p     = state['p2p']

    if chain is None:
        return jsonify({'error': 'Not initialised'}), 503
    if not chain.mempool:
        return jsonify({'error': 'Mempool is empty'}), 400

    def _do_mine():
        try:
            log(f"Mining {len(chain.mempool)} pending transaction(s)…")
            block = chain.mine_identity_block(pub_key)
            if block:
                log(f"Mined block #{block.index}")
                p2p.announce_new_block(block.to_full_dict())
                chain.snapshot_identity(SNAP_PATH)
            else:
                log("Mining failed")
        except Exception as e:
            log(f"Mining error: {e}")

    threading.Thread(target=_do_mine, daemon=True).start()
    return jsonify({'started': True})


# ── Challenges ────────────────────────────────────────────────────────────────

@app.route('/api/challenge', methods=['POST', 'OPTIONS'])
def api_challenge():
    if request.method == 'OPTIONS':
        return '', 204

    chain             = state['chain']
    challenge_manager = state['challenge_manager']

    if chain is None or challenge_manager is None:
        return jsonify({'error': 'Not initialised'}), 503

    data          = request.get_json(silent=True) or {}
    target_pubkey = data.get('target_pubkey', '').strip()
    if not target_pubkey:
        return jsonify({'error': 'target_pubkey required'}), 400

    record = chain.get_identity(target_pubkey)
    if not record:
        return jsonify({'error': 'Identity not found'}), 404

    msg = challenge_manager.issue_challenge(record)
    if msg is None:
        return jsonify({'error': 'Challenge already pending for this identity'}), 409

    if challenge_manager.on_challenge_to_issue:
        challenge_manager.on_challenge_to_issue(msg)

    log(f"Challenge issued to {target_pubkey[:16]}…")
    return jsonify({'ok': True})


# ── Log polling ───────────────────────────────────────────────────────────────

@app.route('/api/logs')
def api_logs():
    """Return log entries from index `since` onwards (for polling)."""
    try:
        since = int(request.args.get('since', 0))
    except ValueError:
        since = 0

    with _log_lock:
        entries = _log_buffer[since:]
        total   = len(_log_buffer)

    return jsonify({'entries': entries, 'next': total})


# ── Incoming challenge events ─────────────────────────────────────────────────

@app.route('/api/incoming-challenges')
def api_incoming_challenges():
    """Return incoming-challenge events from index `since` onwards."""
    try:
        since = int(request.args.get('since', 0))
    except ValueError:
        since = 0

    with _challenge_events_lock:
        entries = _challenge_events[since:]
        total   = len(_challenge_events)

    return jsonify({'events': entries, 'next': total})


# ── Mempool contents ──────────────────────────────────────────────────────────

@app.route('/api/mempool')
def api_mempool():
    chain = state['chain']
    if chain is None:
        return jsonify([])
    return jsonify([
        {
            'uid':       tx.uid,
            'type':      int(tx.tx_type),
            'requester': serialize_pubkey(tx.requester),
            'timestamp': tx.timestamp,
        }
        for tx in chain.mempool
    ])


# ── Issued (pending) challenges ───────────────────────────────────────────────

@app.route('/api/issued-challenges')
def api_issued_challenges():
    """Return challenges this node has issued that are still pending."""
    challenge_manager = state['challenge_manager']
    if challenge_manager is None:
        return jsonify([])

    with challenge_manager._lock:
        result = [
            {
                'target_pubkey': ic.target_pubkey,
                'issued_at':     ic.issued_at,
                'expires_at':    ic.issued_at + ic.challenge.expires_in,
                'resolved':      ic.resolved,
            }
            for ic in challenge_manager.issued.values()
        ]
    return jsonify(result)


# ---------------------------------------------------------------------------
# Challenge response (our identity being challenged)
# ---------------------------------------------------------------------------

def _respond_to_challenge(priv_key, chain, p2p, msg: ChallengeMessage):
    challenge = msg.to_challenge()
    if challenge.is_expired:
        log("Received challenge already expired — ignoring")
        return

    log(f"Solving challenge from {msg.issuer_pubkey[:16]}…")
    result = solve_reputation(challenge)
    if result is None:
        log("Failed to solve challenge")
        return

    nonce, solution_hash = result
    log(f"Challenge solved (nonce={nonce})")

    tx = make_reputation_mine_tx(priv_key, challenge, nonce)
    chain.submit_reputation_solution(tx)

    p2p.broadcast(Message(RepMessageType.REP_SOLUTION, {
        'tx':                tx.to_full_dict(),
        'challenge_data_hex': msg.challenge_data_hex,
        'nonce':             nonce,
        'solution_hash_hex': solution_hash.hex(),
    }))
    log("Solution broadcast to network")


# ---------------------------------------------------------------------------
# Network wiring
# ---------------------------------------------------------------------------

def _wire_network(p2p, chain, priv_key, pubkey_hex, challenge_manager):
    """Wire P2P callbacks — mirrors identity_peer.setup_network."""

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
                for tx in txs:
                    if tx.tx_type in (
                        IdentityTxTypes.IDENTITY_REGISTER,
                        IdentityTxTypes.REPUTATION_MINE,
                        IdentityTxTypes.REPUTATION_IGNORE,
                    ):
                        chain._apply_identity_tx(tx)
                chain.snapshot_identity(SNAP_PATH)
                log(f"Received block #{block_data['index']}")
        except Exception as e:
            log(f"Failed to process block: {e}")

    def handle_chain_request() -> dict:
        return {
            'chain':  [b.to_full_dict() for b in chain.chain],
            'length': len(chain.chain),
        }

    def handle_chain_response(response_data: dict):
        try:
            peer_chain  = response_data.get('chain', [])
            peer_length = response_data.get('length', 0)
            log(f"Chain sync: peer={peer_length} ours={len(chain.chain)}")
            if chain.replace_chain(peer_chain):
                log(f"Adopted longer chain ({peer_length} blocks)")
                chain._rebuild_item_tracking()
                chain.identities.clear()
                for block in chain.chain:
                    for tx in block.transactions:
                        if tx.tx_type in (
                            IdentityTxTypes.IDENTITY_REGISTER,
                            IdentityTxTypes.REPUTATION_MINE,
                            IdentityTxTypes.REPUTATION_IGNORE,
                        ):
                            chain._apply_identity_tx(tx)
                # Remove mempool entries that are now confirmed in the adopted chain
                confirmed_uids = {
                    tx.uid
                    for block in chain.chain
                    for tx in block.transactions
                }
                chain.mempool = [tx for tx in chain.mempool if tx.uid not in confirmed_uids]
                chain.snapshot_identity(SNAP_PATH)
            else:
                log("Kept current chain (already longest or peer chain invalid)")
        except Exception as e:
            log(f"Chain sync error: {e}")

    def handle_new_transaction(tx_data: dict):
        try:
            from blockchain.blockchain import deserialize_pubkey
            p  = deserialize_pubkey(tx_data['requester'])
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
            log(f"Received transaction type={tx_type}")
        except Exception as e:
            log(f"Failed to process transaction: {e}")

    p2p.on_new_block          = handle_new_block
    p2p.on_chain_request      = handle_chain_request
    p2p.on_chain_response     = handle_chain_response
    p2p.on_new_transaction    = handle_new_transaction

    original_route = p2p._route_message

    def extended_route(msg: Message, peer):
        if msg.type == RepMessageType.REP_CHALLENGE:
            try:
                cm       = ChallengeMessage.from_dict(msg.payload)
                incoming = challenge_manager.receive_challenge(cm)
                # Set pending_challenges BEFORE starting the solver thread to
                # avoid a race where submit_reputation_solution runs before the
                # challenge is registered and rejects the tx as invalid.
                chain.pending_challenges[cm.target_pubkey] = cm.to_challenge()
                if incoming is not None:
                    _add_challenge_event({
                        'kind':           'received',
                        'issuer_pubkey':  cm.issuer_pubkey,
                        'target_pubkey':  cm.target_pubkey,
                        'issued_at':      cm.issued_at,
                        'expires_at':     cm.issued_at + cm.expires_in,
                        'status':         'solving',
                    })

                    def _respond_and_track(_cm=cm):
                        _respond_to_challenge(priv_key, chain, p2p, _cm)
                        _add_challenge_event({
                            'kind':          'solved',
                            'issuer_pubkey': _cm.issuer_pubkey,
                            'target_pubkey': _cm.target_pubkey,
                            'issued_at':     _cm.issued_at,
                            'status':        'solved',
                        })

                    threading.Thread(
                        target=_respond_and_track,
                        daemon=True,
                    ).start()
            except Exception as e:
                log(f"REP_CHALLENGE error: {e}")

        elif msg.type == RepMessageType.REP_SOLUTION:
            try:
                tx_data    = msg.payload.get('tx', {})
                target_hex = tx_data.get('requester', '')
                challenge_manager.mark_resolved(target_hex)
                from blockchain.blockchain import deserialize_pubkey
                p_key = deserialize_pubkey(tx_data['requester'])
                tx = Transaction(
                    p_key, tx_data['uid'],
                    IdentityTxTypes.REPUTATION_MINE,
                    tx_data.get('timestamp'),
                    tx_data.get('signature'),
                )
                chain.submit_reputation_solution(tx)
                log(f"Received reputation solution from {target_hex[:16]}…")
            except Exception as e:
                log(f"REP_SOLUTION error: {e}")

        else:
            original_route(msg, peer)

    p2p._route_message = extended_route

    def on_challenge_to_issue(cm: ChallengeMessage):
        log(f"Issuing challenge to {cm.target_pubkey[:16]}…")
        # Store locally so we can validate the solution when it arrives
        chain.pending_challenges[cm.target_pubkey] = cm.to_challenge()
        p2p.broadcast(Message(RepMessageType.REP_CHALLENGE, cm.to_dict()))

        # If the target is this node, handle the challenge locally as well.
        # This is essential in single-node setups where P2P broadcast would
        # go nowhere: the node challenges itself, auto-solves the PoW, and
        # the resulting REPUTATION_MINE tx lands in the mempool ready to mine.
        if cm.target_pubkey == pubkey_hex:
            incoming = challenge_manager.receive_challenge(cm)
            if incoming is not None:
                # Set the pending challenge BEFORE starting the solver thread
                chain.pending_challenges[cm.target_pubkey] = cm.to_challenge()
                _add_challenge_event({
                    'kind':          'received',
                    'issuer_pubkey': cm.issuer_pubkey,
                    'target_pubkey': cm.target_pubkey,
                    'issued_at':     cm.issued_at,
                    'expires_at':    cm.issued_at + cm.expires_in,
                    'status':        'solving',
                })

                def _self_respond_and_track(_cm=cm):
                    _respond_to_challenge(priv_key, chain, p2p, _cm)
                    _add_challenge_event({
                        'kind':          'solved',
                        'issuer_pubkey': _cm.issuer_pubkey,
                        'target_pubkey': _cm.target_pubkey,
                        'issued_at':     _cm.issued_at,
                        'status':        'solved',
                    })
                    log("Self-challenge solved — REPUTATION_MINE tx in mempool. Click 'Mine Pending' to confirm.")

                threading.Thread(
                    target=_self_respond_and_track,
                    daemon=True,
                ).start()

    def on_ignore_to_report(target_pubkey: str, challenge):
        log(f"Challenge expired for {target_pubkey[:16]}… — submitting ignore penalty")
        _add_challenge_event({
            'kind':          'expired',
            'target_pubkey': target_pubkey,
            'status':        'expired',
        })
        try:
            ignore_tx = make_reputation_ignore_tx(priv_key, target_pubkey, challenge)
            if chain.record_ignore(ignore_tx):
                p2p.announce_new_transaction(ignore_tx.to_full_dict())
                log(f"REPUTATION_IGNORE broadcast for {target_pubkey[:16]}…")
        except Exception as e:
            log(f"Failed to report ignore: {e}")

    challenge_manager.on_challenge_to_issue = on_challenge_to_issue
    challenge_manager.on_ignore_to_report   = on_ignore_to_report


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------

def _start_background_tasks(chain, p2p):
    def integrity_monitor():
        last_ok = None
        while True:
            ok = chain.integrity_check()
            if last_ok is None or ok != last_ok:
                log(f"Chain integrity: {'OK' if ok else 'CORRUPT'}")
                if not ok and chain.repair():
                    log("Repair completed")
                last_ok = ok
            time.sleep(30)

    def decay_loop():
        while True:
            time.sleep(300)
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
# Peer initialisation (runs in a background thread before Flask starts)
# ---------------------------------------------------------------------------

def _init_peer(p2p_port: int):
    priv_key = load_keypair(KEY_PATH)
    if priv_key:
        log("Loaded existing keypair from disk")
    else:
        priv_key = ec.generate_private_key(ec.SECP256R1())
        save_keypair(priv_key, KEY_PATH)
        log("Generated new keypair — saved to disk")

    pub_key    = priv_key.public_key()
    pubkey_hex = serialize_pubkey(pub_key)

    chain = IdentityBlockchain.init_identity(SNAP_PATH)
    log(f"Chain: {len(chain.chain)} blocks, {len(chain.identities)} identities")

    p2p               = P2PNetwork(host="0.0.0.0", port=p2p_port)
    challenge_manager = PeerChallengeManager(
        my_pubkey_hex=pubkey_hex,
        challenge_interval=120.0,
        challenge_window=300.0,
    )

    _wire_network(p2p, chain, priv_key, pubkey_hex, challenge_manager)
    p2p.start()
    _start_background_tasks(chain, p2p)
    challenge_manager.start(get_identities=chain.list_identities)

    state['priv_key']          = priv_key
    state['pub_key']           = pub_key
    state['pubkey_hex']        = pubkey_hex
    state['chain']             = chain
    state['p2p']               = p2p
    state['challenge_manager'] = challenge_manager

    def _cleanup():
        chain.snapshot_identity(SNAP_PATH)
        save_keypair(priv_key, KEY_PATH)
        p2p.stop()

    atexit.register(_cleanup)
    log(f"Peer ready on P2P port {p2p_port}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    p2p_port = int(sys.argv[1]) if len(sys.argv) > 1 else 6000
    api_port = int(sys.argv[2]) if len(sys.argv) > 2 else 5001

    threading.Thread(target=_init_peer, args=(p2p_port,), daemon=True).start()

    app.run(
        host='127.0.0.1',
        port=api_port,
        threaded=True,
        use_reloader=False,
    )
