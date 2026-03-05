# POST `/api/authenticate`

Checks whether a given public key is currently **authenticated** — i.e., whether its on-chain reputation balance meets or exceeds the authentication threshold (`1.0`).

This is a **read-only query**. It does not create transactions or modify any state.

---

## Request

| Property | Value |
|---|---|
| **Method** | `POST` |
| **URL** | `/api/authenticate` |
| **Auth required** | No |
| **Content-Type** | `application/json` |

### Headers

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |

### Request Body

```json
{
  "pubkey": "04a1b2c3d4e5f6aa..."
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `pubkey` | `string` (hex) | Yes | SECP256R1 public key to check, in uncompressed hex |

---

## Success Response — `200 OK`

```json
{
  "authenticated": true,
  "balance": 112.5,
  "revoked": false,
  "registered": true
}
```

### Field Reference

| Field | Type | Description |
|---|---|---|
| `authenticated` | `boolean` | `true` when `balance >= 1.0` and identity is not revoked |
| `balance` | `number \| null` | Current reputation balance; `null` if the key is not registered |
| `revoked` | `boolean \| null` | `true` if the identity has been revoked; `null` if not registered |
| `registered` | `boolean` | `true` if the key appears in the identity registry |

### Unregistered key

```json
{
  "authenticated": false,
  "balance": null,
  "revoked": null,
  "registered": false
}
```

### Revoked identity

```json
{
  "authenticated": false,
  "balance": 0.0,
  "revoked": true,
  "registered": true
}
```

---

## Error Responses

### `400 Bad Request` — missing pubkey

```json
{
  "error": "pubkey required"
}
```

### `503 Service Unavailable` — node not initialised

```json
{
  "error": "not ready"
}
```

---

## Authentication Rules

| Condition | `authenticated` |
|---|---|
| `balance >= 1.0` and not revoked | `true` |
| `balance < 1.0` | `false` |
| `revoked = true` | `false` |
| Key not registered | `false` |

The **authentication threshold** is `1.0`. New identities start at `100.0`, giving substantial margin before revocation.

---

## Example

```bash
curl -X POST http://localhost:5001/api/authenticate \
  -H "Content-Type: application/json" \
  -d '{"pubkey": "04a1b2c3..."}'
```

```json
{
  "authenticated": true,
  "balance": 100.0,
  "revoked": false,
  "registered": true
}
```

---

## Notes

- Authentication is **stateless**: it re-checks the chain on every call and requires no session tokens.
- This endpoint is intended for use by external services that want to gate access based on Anonity identity reputation.
- To retrieve your own node's authentication status without knowing its public key, use [`GET /api/my-identity`](./my-identity.md).
