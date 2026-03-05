# GET `/api/status`

Returns a real-time health snapshot of the local node: chain statistics, identity counts, and readiness state.

---

## Request

| Property | Value |
|---|---|
| **Method** | `GET` |
| **URL** | `/api/status` |
| **Auth required** | No |
| **Request body** | None |

### Headers

| Header | Value |
|---|---|
| `Accept` | `application/json` (optional) |

---

## Success Response — `200 OK`

```json
{
  "ready": true,
  "pubkey": "04a1b2c3d4e5f6...",
  "balance": 112.5,
  "authenticated": true,
  "blocks": 42,
  "peers": 3,
  "mempool": 1,
  "identities": 7,
  "auth_count": 5,
  "rev_count": 1,
  "integrity": true
}
```

### Field Reference

| Field | Type | Description |
|---|---|---|
| `ready` | `boolean` | `true` once the node has fully initialised (key loaded, chain ready) |
| `pubkey` | `string` (hex) | This node's SECP256R1 public key in uncompressed hex |
| `balance` | `number \| null` | This node's current reputation balance; `null` if not yet registered |
| `authenticated` | `boolean` | `true` when `balance >= 1.0` |
| `blocks` | `integer` | Number of mined blocks in the local chain |
| `peers` | `integer` | Number of currently connected P2P peers |
| `mempool` | `integer` | Number of pending (unmined) transactions |
| `identities` | `integer` | Total registered identities visible on-chain |
| `auth_count` | `integer` | Identities currently authenticated (`balance >= 1.0`) |
| `rev_count` | `integer` | Identities currently revoked (`balance < 1.0`) |
| `integrity` | `boolean` | `true` when the local chain passes full integrity verification |

---

## Error Responses

### `503 Service Unavailable` — node not yet initialised

```json
{
  "error": "not ready"
}
```

Returned during the brief startup window before the key and chain are loaded. Retry after a short delay.

---

## Example

```bash
curl http://localhost:5001/api/status
```

```json
{
  "ready": true,
  "pubkey": "04a1b2c3...",
  "balance": 100.0,
  "authenticated": true,
  "blocks": 10,
  "peers": 2,
  "mempool": 0,
  "identities": 3,
  "auth_count": 3,
  "rev_count": 0,
  "integrity": true
}
```

---

## Notes

- Poll this endpoint to implement a dashboard status bar.
- The `integrity` field is re-verified every 30 seconds by a background monitor. If it flips to `false`, the node will attempt auto-repair.
- `balance` and `authenticated` reflect the **on-chain** state at the moment of the call; they do not include pending mempool transactions.
