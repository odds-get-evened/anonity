# Anonity — API Documentation

This directory contains the complete reference documentation for the **Anonity** Flask REST API.

---

## Table of Contents

### Status & Monitoring

| Endpoint | Description |
|---|---|
| [`GET /api/status`](./endpoints/status.md) | Node health, chain stats, and identity counts |
| [`GET /api/logs`](./endpoints/logs.md) | Incremental log polling |

### Identity Management

| Endpoint | Description |
|---|---|
| [`GET /api/identities`](./endpoints/identities.md) | List all registered identities on-chain |
| [`GET /api/my-identity`](./endpoints/my-identity.md) | This node's own identity record |
| [`POST /api/register`](./endpoints/register.md) | Register this node's identity (triggers PoW) |
| [`POST /api/authenticate`](./endpoints/authenticate.md) | Check if a public key is authenticated |

### Peer Networking

| Endpoint | Description |
|---|---|
| [`POST /api/connect`](./endpoints/connect.md) | Connect to a P2P peer node |
| [`GET /api/peers`](./endpoints/peers.md) | List currently connected peers |

### Blockchain Operations

| Endpoint | Description |
|---|---|
| [`POST /api/sync`](./endpoints/sync.md) | Sync the chain from connected peers |
| [`POST /api/mine`](./endpoints/mine.md) | Mine pending mempool transactions |
| [`GET /api/mempool`](./endpoints/mempool.md) | List pending (unconfirmed) transactions |

### Reputation Challenges

| Endpoint | Description |
|---|---|
| [`POST /api/challenge`](./endpoints/challenge.md) | Issue a reputation challenge to an identity |
| [`GET /api/issued-challenges`](./endpoints/issued-challenges.md) | Challenges this node has issued |
| [`GET /api/incoming-challenges`](./endpoints/incoming-challenges.md) | Challenges directed at this node's identity |

---

## Base URL

The API server runs on **`http://localhost:5001`** by default.

```
http://<host>:<api_port>/api/<endpoint>
```

Override the API port when starting the node:

```bash
python anonity/identity_api.py <p2p_port> <api_port>
# e.g.:
python anonity/identity_api.py 6001 5002
```

---

## Common Conventions

### Request format

- `GET` requests use query parameters only.
- `POST` requests send a JSON body with `Content-Type: application/json`.
- Request bodies are optional unless a field is marked **Required**.

### Response format

All responses are JSON. Errors include an `"error"` field:

```json
{ "error": "description of what went wrong" }
```

### CORS

All endpoints include permissive CORS headers:

```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, POST, OPTIONS
Access-Control-Allow-Headers: Content-Type
```

### HTTP Status Codes

| Code | Meaning |
|---|---|
| `200 OK` | Success |
| `204 No Content` | CORS preflight (OPTIONS) |
| `400 Bad Request` | Missing or invalid request parameters |
| `404 Not Found` | Requested resource does not exist |
| `409 Conflict` | State conflict (e.g., challenge already pending) |
| `500 Internal Server Error` | Network or runtime error |
| `503 Service Unavailable` | Node not yet initialised — retry after a short delay |

### Authentication model

There are **no API keys or session tokens**. Authentication is derived entirely from on-chain reputation:

- An identity is **authenticated** when its balance `>= 1.0`.
- Check any public key's status with [`POST /api/authenticate`](./endpoints/authenticate.md).

---

## See Also

- [Main project README](../README.md) — architecture overview, CLI guide, and design decisions.
