# GET `/api/identities`

Returns the full list of registered identities visible in the local chain, including each identity's reputation balance, authentication state, and revocation flag.

---

## Request

| Property | Value |
|---|---|
| **Method** | `GET` |
| **URL** | `/api/identities` |
| **Auth required** | No |
| **Request body** | None |

### Headers

| Header | Value |
|---|---|
| `Accept` | `application/json` (optional) |

---

## Success Response — `200 OK`

Returns a JSON array. An empty array (`[]`) is returned when no identities are registered.

```json
[
  {
    "pubkey": "04a1b2c3d4e5f6aa...",
    "balance": 112.5,
    "solved": 3,
    "ignored": 0,
    "authenticated": true,
    "revoked": false,
    "registered_at": 1710000000.0
  },
  {
    "pubkey": "04deadbeef0102...",
    "balance": 0.0,
    "solved": 0,
    "ignored": 8,
    "authenticated": false,
    "revoked": true,
    "registered_at": 1709500000.0
  }
]
```

### Field Reference (per identity object)

| Field | Type | Description |
|---|---|---|
| `pubkey` | `string` (hex) | SECP256R1 public key in uncompressed hex format |
| `balance` | `number` | Current reputation balance. Starts at `100.0`, grows by `+10.0` per solved challenge, decays by `-2.0/hour` (soft), `-15.0` per ignored challenge (hard) |
| `solved` | `integer` | Cumulative count of reputation challenges solved |
| `ignored` | `integer` | Cumulative count of reputation challenges ignored (expired without a response) |
| `authenticated` | `boolean` | `true` when `balance >= 1.0` |
| `revoked` | `boolean` | `true` when `balance` has dropped below `1.0` |
| `registered_at` | `number` | Unix timestamp (seconds) of when the `IDENTITY_REGISTER` transaction was mined |

---

## Error Responses

### `503 Service Unavailable` — node not initialised

```json
{
  "error": "not ready"
}
```

---

## Example

```bash
curl http://localhost:5001/api/identities
```

```json
[
  {
    "pubkey": "04a1b2...",
    "balance": 100.0,
    "solved": 0,
    "ignored": 0,
    "authenticated": true,
    "revoked": false,
    "registered_at": 1710001234.567
  }
]
```

---

## Notes

- Results reflect the **committed chain state** only. Pending `IDENTITY_REGISTER` transactions in the mempool will not appear until mined.
- Balances shown here include all soft-decay ticks applied up to the current moment.
- To retrieve only the local node's own identity record, see [`GET /api/my-identity`](./my-identity.md).
- To check whether a specific public key is currently authenticated, see [`POST /api/authenticate`](./authenticate.md).
