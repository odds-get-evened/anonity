# GET `/api/my-identity`

Returns the on-chain identity record for **this node's own public key**. All fields are `null` while the node has not yet completed registration.

---

## Request

| Property | Value |
|---|---|
| **Method** | `GET` |
| **URL** | `/api/my-identity` |
| **Auth required** | No |
| **Request body** | None |

### Headers

| Header | Value |
|---|---|
| `Accept` | `application/json` (optional) |

---

## Success Responses

### `200 OK` — registered identity

```json
{
  "pubkey": "04a1b2c3d4e5f6aa...",
  "registered": true,
  "balance": 112.5,
  "solved": 3,
  "ignored": 0,
  "registered_at": 1710000000.0,
  "authenticated": true,
  "revoked": false
}
```

### `200 OK` — node running but not yet registered

```json
{
  "pubkey": "04a1b2c3d4e5f6aa...",
  "registered": false,
  "balance": null,
  "solved": null,
  "ignored": null,
  "registered_at": null,
  "authenticated": null,
  "revoked": null
}
```

`pubkey` is always present once the node has generated its key. All reputation fields are `null` until the `IDENTITY_REGISTER` transaction is mined.

### Field Reference

| Field | Type | Description |
|---|---|---|
| `pubkey` | `string` (hex) | This node's SECP256R1 public key |
| `registered` | `boolean` | `true` once the `IDENTITY_REGISTER` transaction has been mined |
| `balance` | `number \| null` | Current reputation balance |
| `solved` | `integer \| null` | Challenges solved so far |
| `ignored` | `integer \| null` | Challenges ignored so far |
| `registered_at` | `number \| null` | Unix timestamp of registration mining |
| `authenticated` | `boolean \| null` | `true` when `balance >= 1.0` |
| `revoked` | `boolean \| null` | `true` when balance dropped below `1.0` |

---

## Error Responses

### `503 Service Unavailable` — key not yet generated

```json
{
  "error": "not ready"
}
```

Returned during the brief startup window before the node keypair is loaded or generated. Retry after a short delay.

---

## Example

```bash
curl http://localhost:5001/api/my-identity
```

---

## Notes

- This endpoint is the fastest way to check your own node's registration and authentication status.
- It does **not** require you to know your own public key in advance — the node returns it directly.
- To trigger registration, call [`POST /api/register`](./register.md).
- To look up other identities by public key, see [`GET /api/identities`](./identities.md).
