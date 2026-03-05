'use strict'

// ── Bootstrap ──────────────────────────────────────────────────────────────
// window.anonity is exposed by preload.js via contextBridge.

let API = ''                  // set after we get the port from main process
let ownPubkey = ''            // filled once /api/status returns our pubkey
let logCursor = 0             // position in the server's log buffer
let incomingChallengeCursor = 0  // position in the server's challenge-event buffer

async function boot () {
  const port = await window.anonity.getApiPort()
  API = `http://127.0.0.1:${port}`
  await waitForBackend()
  hideLoading()
  startRefreshLoop()
  startLogPolling()
}

// ── Loading screen ─────────────────────────────────────────────────────────

async function waitForBackend () {
  const msg = document.getElementById('loading-msg')
  while (true) {
    try {
      const s = await fetchJSON('/api/status')
      if (s && s.ready) return
    } catch (_) { /* still starting */ }
    msg.textContent = 'Waiting for peer…'
    await sleep(600)
  }
}

function hideLoading () {
  const el = document.getElementById('loading')
  el.classList.add('hidden')
  setTimeout(() => el.remove(), 500)
}

// ── Utilities ──────────────────────────────────────────────────────────────

const sleep = ms => new Promise(r => setTimeout(r, ms))

async function fetchJSON (path, opts = {}) {
  const res = await fetch(API + path, opts)
  return res.json()
}

async function postJSON (path, body = {}) {
  return fetchJSON(path, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  })
}

function abbrev (hex, n = 20) {
  return hex.length > n ? hex.slice(0, n) + '…' : hex
}

// ── Toast ──────────────────────────────────────────────────────────────────

let _toastTimer = null

function toast (msg, duration = 3000) {
  const el = document.getElementById('toast')
  el.textContent = msg
  el.classList.add('show')
  clearTimeout(_toastTimer)
  _toastTimer = setTimeout(() => el.classList.remove('show'), duration)
}

// ── Refresh loops ──────────────────────────────────────────────────────────

function startRefreshLoop () {
  refreshStatus()
  refreshIdentities()
  refreshIssuedChallenges()
  setInterval(refreshStatus,           2000)
  setInterval(refreshIdentities,       5000)
  setInterval(refreshIssuedChallenges, 3000)
}

async function refreshStatus () {
  try {
    const s = await fetchJSON('/api/status')
    if (!s.ready) return

    ownPubkey = s.pubkey || ''

    set('stat-pubkey',  abbrev(s.pubkey, 16))
    set('stat-blocks',  s.blocks)
    set('stat-mempool', s.mempool)

    const mineBtn = document.getElementById('btn-mine')
    if (s.mempool > 0) mineBtn.classList.add('has-pending')
    else               mineBtn.classList.remove('has-pending')

    const authEl = document.getElementById('stat-auth')
    if (authEl) {
      const rev = s.rev_count ?? 0
      authEl.textContent = rev > 0 ? `${s.auth_count ?? '—'}/${rev}r` : (s.auth_count ?? '—')
      authEl.className   = 'stat-value ' + (rev > 0 ? 'warn' : 'ok')
    }

    const balEl = document.getElementById('stat-balance')
    balEl.textContent = s.balance !== null ? s.balance.toFixed(1) : '—'
    balEl.className   = 'stat-value ' + (s.authenticated ? 'ok' : 'warn')

    const peersEl = document.getElementById('stat-peers')
    peersEl.textContent = s.peers
    peersEl.className   = 'stat-value ' + (s.peers > 0 ? 'ok' : 'warn')

    const intEl = document.getElementById('stat-integrity')
    intEl.textContent = s.integrity ? 'OK' : 'CORRUPT'
    intEl.className   = 'stat-value ' + (s.integrity ? 'ok' : 'bad')
  } catch (_) { /* backend may still be initialising */ }
}

async function refreshIdentities () {
  try {
    const records = await fetchJSON('/api/identities')
    const tbody   = document.getElementById('identity-tbody')

    if (!records.length) {
      tbody.innerHTML = '<tr><td class="empty-cell" colspan="5">No identities registered yet</td></tr>'
      return
    }

    tbody.innerHTML = records.map(r => {
      const isOwn    = r.pubkey === ownPubkey
      const rowClass = isOwn ? ' class="own-row"' : ''
      const sClass   = r.authenticated ? 'td-status-auth' : 'td-status-revoked'
      const sText    = r.authenticated ? 'AUTH' : 'REVOKED'
      return `<tr${rowClass}>
        <td class="td-pubkey">${abbrev(r.pubkey, 36)}${isOwn ? ' <span style="color:var(--accent);font-size:10px">(me)</span>' : ''}</td>
        <td class="td-balance">${r.balance.toFixed(1)}</td>
        <td>${r.solved}</td>
        <td>${r.ignored}</td>
        <td class="${sClass}">${sText}</td>
      </tr>`
    }).join('')
  } catch (_) {}
}

async function refreshIssuedChallenges () {
  try {
    const challenges = await fetchJSON('/api/issued-challenges')
    const tbody = document.getElementById('challenges-tbody')
    if (!challenges.length) {
      tbody.innerHTML = '<tr><td class="empty-cell" colspan="3">No pending challenges</td></tr>'
      return
    }
    tbody.innerHTML = challenges.map(c => {
      const issued  = new Date(c.issued_at  * 1000).toLocaleTimeString()
      const expires = new Date(c.expires_at * 1000).toLocaleTimeString()
      return `<tr>
        <td class="td-pubkey">${abbrev(c.target_pubkey, 28)}</td>
        <td style="text-align:right;font-family:var(--mono);font-size:11px;color:var(--muted)">${issued}</td>
        <td style="text-align:right;font-family:var(--mono);font-size:11px;color:var(--muted)">${expires}</td>
      </tr>`
    }).join('')
  } catch (_) {}
}

// ── Log polling ────────────────────────────────────────────────────────────

function startLogPolling () {
  pollLogs()
  pollIncomingChallenges()
}

async function pollLogs () {
  try {
    const data = await fetchJSON(`/api/logs?since=${logCursor}`)
    if (data.entries && data.entries.length > 0) {
      const scroll = document.getElementById('log-scroll')
      const wasAtBottom = scroll.scrollHeight - scroll.scrollTop <= scroll.clientHeight + 4

      for (const entry of data.entries) {
        const line = document.createElement('div')
        line.className = 'log-line'
        line.innerHTML = `<span class="log-ts">[${entry.ts}]</span><span class="log-msg">${escHtml(entry.msg)}</span>`
        scroll.appendChild(line)
      }

      // Keep at most 300 lines in the DOM
      while (scroll.children.length > 300) scroll.removeChild(scroll.firstChild)

      logCursor = data.next
      if (wasAtBottom) scroll.scrollTop = scroll.scrollHeight
    }
  } catch (_) {}
  setTimeout(pollLogs, 1000)
}

function escHtml (s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
}

// ── Incoming-challenge banner ───────────────────────────────────────────────

let _challengeBarTimer = null

function showChallengeBar (msg, state = 'solving') {
  const bar  = document.getElementById('challenge-bar')
  const icon = document.getElementById('challenge-bar-icon')
  bar.className = state          // 'solving' | 'solved' | 'expired'
  icon.textContent = state === 'solved' ? '✓' : state === 'expired' ? '✗' : '⚡'
  document.getElementById('challenge-bar-msg').textContent = msg
  clearTimeout(_challengeBarTimer)
  if (state === 'solved' || state === 'expired') {
    _challengeBarTimer = setTimeout(hideChallengeBar, 6000)
  }
}

function hideChallengeBar () {
  document.getElementById('challenge-bar').classList.add('hidden')
}

async function pollIncomingChallenges () {
  try {
    const data = await fetchJSON(`/api/incoming-challenges?since=${incomingChallengeCursor}`)
    if (data.events && data.events.length > 0) {
      for (const ev of data.events) {
        if (ev.kind === 'received') {
          showChallengeBar(
            `Incoming challenge from ${abbrev(ev.issuer_pubkey, 20)} — solving proof-of-work automatically…`,
            'solving',
          )
        } else if (ev.kind === 'solved') {
          showChallengeBar('Challenge solved! Reputation transaction broadcast to network.', 'solved')
        } else if (ev.kind === 'expired') {
          showChallengeBar(
            `Challenge expired — ${abbrev(ev.target_pubkey, 20)} did not respond. Ignore penalty submitted.`,
            'expired',
          )
        }
      }
      incomingChallengeCursor = data.next
    }
  } catch (_) {}
  setTimeout(pollIncomingChallenges, 1000)
}

document.getElementById('challenge-bar-dismiss').addEventListener('click', hideChallengeBar)

// ── Modal helpers ──────────────────────────────────────────────────────────

function showModal ({ title, body, confirmLabel = 'OK', cancelLabel = 'Cancel', onConfirm, hideCancelBtn = false }) {
  document.getElementById('modal-title').textContent   = title
  document.getElementById('modal-body').innerHTML      = body
  document.getElementById('modal-confirm').textContent = confirmLabel

  const cancelBtn = document.getElementById('modal-cancel')
  cancelBtn.textContent = cancelLabel
  cancelBtn.style.display = hideCancelBtn ? 'none' : ''

  document.getElementById('overlay').classList.add('visible')

  const confirm = document.getElementById('modal-confirm')
  const cancel  = document.getElementById('modal-cancel')
  const close   = () => document.getElementById('overlay').classList.remove('visible')

  // Remove old listeners
  const newConfirm = confirm.cloneNode(true)
  const newCancel  = cancel.cloneNode(true)
  confirm.replaceWith(newConfirm)
  cancel.replaceWith(newCancel)

  document.getElementById('modal-confirm').addEventListener('click', () => {
    close()
    if (onConfirm) onConfirm()
  })
  document.getElementById('modal-cancel').addEventListener('click', close)
}

function infoModal (title, text) {
  showModal({
    title,
    body:          `<pre style="white-space:pre-wrap;font-family:var(--mono);font-size:11px;color:var(--muted)">${escHtml(text)}</pre>`,
    hideCancelBtn: true,
    onConfirm:     () => {},
  })
}

function inputModal (title, fields, confirmLabel, onConfirm) {
  const inputs = fields.map(f =>
    `<label style="display:block;margin-top:10px;color:var(--muted);font-size:11px">${escHtml(f.label)}
      <input class="modal-input" id="modal-input-${f.id}" placeholder="${escHtml(f.placeholder || '')}" value="${escHtml(f.value || '')}">
    </label>`
  ).join('')

  showModal({
    title,
    body:         inputs,
    confirmLabel,
    onConfirm: () => {
      const values = Object.fromEntries(fields.map(f => [f.id, document.getElementById(`modal-input-${f.id}`)?.value.trim() || '']))
      onConfirm(values)
    },
  })

  // Focus first input
  setTimeout(() => {
    const first = document.querySelector('.modal-input')
    if (first) first.focus()
  }, 50)
}

// ── Helpers ────────────────────────────────────────────────────────────────

function set (id, val) {
  const el = document.getElementById(id)
  if (el) el.textContent = val
}

// ── Action handlers ────────────────────────────────────────────────────────

document.getElementById('btn-register').addEventListener('click', async () => {
  try {
    const res = await postJSON('/api/register')
    if (res.already_registered) {
      toast(`Already registered — balance ${res.balance.toFixed(1)}`)
    } else if (res.started) {
      toast('Registration started — watch the activity log')
    } else {
      toast(res.error || 'Registration failed')
    }
  } catch (e) { toast('Network error: ' + e.message) }
})

document.getElementById('btn-authenticate').addEventListener('click', () => {
  inputModal(
    'Authenticate Public Key',
    [{ id: 'pubkey', label: 'Public key (hex)', placeholder: '03…' }],
    'Authenticate',
    async ({ pubkey }) => {
      if (!pubkey) return
      try {
        const res = await postJSON('/api/authenticate', { pubkey })
        if (res.authenticated) {
          infoModal('Authenticated ✓', `AUTHENTICATED\n\nBalance: ${res.balance.toFixed(1)}`)
        } else {
          const detail = res.registered
            ? `Balance: ${res.balance.toFixed(1)}\nRevoked: ${res.revoked}`
            : 'Identity not registered on this chain'
          infoModal('Denied ✗', `DENIED\n\n${detail}`)
        }
      } catch (e) { toast('Error: ' + e.message) }
    }
  )
})

document.getElementById('btn-my-identity').addEventListener('click', async () => {
  try {
    const r = await fetchJSON('/api/my-identity')
    if (r.error) { toast(r.error); return }
    if (!r.registered) {
      infoModal('My Identity', `Public key:\n${r.pubkey}\n\n⚠  Not yet registered on this chain`)
      return
    }
    const registered = new Date(r.registered_at * 1000).toLocaleString()
    infoModal('My Identity',
      `Public key:  ${r.pubkey}\n\n` +
      `Balance:     ${r.balance.toFixed(1)}\n` +
      `Solved:      ${r.solved} challenges\n` +
      `Ignored:     ${r.ignored} challenges\n` +
      `Registered:  ${registered}\n` +
      `Status:      ${r.authenticated ? 'AUTHENTICATED' : 'REVOKED'}`
    )
  } catch (e) { toast('Error: ' + e.message) }
})

document.getElementById('btn-connect').addEventListener('click', () => {
  inputModal(
    'Connect to Peer',
    [
      { id: 'host', label: 'Host',  placeholder: 'localhost' },
      { id: 'port', label: 'Port',  placeholder: '6000' },
    ],
    'Connect',
    async ({ host, port }) => {
      if (!host || !port) return
      try {
        const res = await postJSON('/api/connect', { host, port: parseInt(port, 10) })
        if (res.ok) toast(`Connecting to ${host}:${port}…`)
        else toast(res.error || 'Connect failed')
      } catch (e) { toast('Error: ' + e.message) }
    }
  )
})

document.getElementById('btn-peers').addEventListener('click', async () => {
  try {
    const peers = await fetchJSON('/api/peers')
    if (!peers.length) { infoModal('Connected Peers', 'No peers connected'); return }
    infoModal(`Connected Peers (${peers.length})`, peers.join('\n'))
  } catch (e) { toast('Error: ' + e.message) }
})

document.getElementById('btn-sync').addEventListener('click', async () => {
  try {
    const res = await postJSON('/api/sync')
    if (res.ok) toast('Chain sync requested')
    else toast(res.error || 'Sync failed')
  } catch (e) { toast('Error: ' + e.message) }
})

document.getElementById('btn-mine').addEventListener('click', async () => {
  try {
    const res = await postJSON('/api/mine')
    if (res.started)      toast('Mining started — watch the activity log')
    else if (res.error)   toast(res.error)
  } catch (e) { toast('Error: ' + e.message) }
})

document.getElementById('btn-status').addEventListener('click', async () => {
  try {
    const s = await fetchJSON('/api/status')
    if (!s.ready) { toast('Backend not ready yet'); return }
    infoModal('Chain Status',
      `Integrity:    ${s.integrity ? 'OK' : 'CORRUPT'}\n` +
      `Blocks:       ${s.blocks}\n` +
      `Identities:   ${s.identities}  (${s.auth_count} auth, ${s.rev_count} revoked)\n` +
      `Mempool:      ${s.mempool} pending tx(s)\n` +
      `Peers:        ${s.peers} connected`
    )
  } catch (e) { toast('Error: ' + e.message) }
})

document.getElementById('btn-challenge').addEventListener('click', async () => {
  let records
  try {
    records = await fetchJSON('/api/identities')
  } catch (e) { toast('Error: ' + e.message); return }

  const candidates = records.filter(r => r.authenticated)
  if (!candidates.length) {
    infoModal('Issue Challenge', 'No other authenticated identities available to challenge')
    return
  }

  // Build a list-picker modal
  const listHtml = `
    <div class="modal-list" id="challenge-list">
      ${candidates.map((r, i) => {
        const isSelf = r.pubkey === ownPubkey
        return `<div class="modal-list-item" data-i="${i}" data-pubkey="${escHtml(r.pubkey)}">
          ${abbrev(r.pubkey, 38)} &nbsp;<span style="color:var(--muted)">bal=${r.balance.toFixed(1)}</span>${isSelf ? ' <span style="color:var(--accent);font-size:10px">(self)</span>' : ''}
        </div>`
      }).join('')}
    </div>`

  let selectedPubkey = null

  showModal({
    title:        'Issue Reputation Challenge',
    body:         '<p style="color:var(--muted);margin-bottom:4px;font-size:11px">Select an identity to challenge:</p>' + listHtml,
    confirmLabel: 'Issue Challenge',
    onConfirm: async () => {
      if (!selectedPubkey) { toast('No identity selected'); return }
      try {
        const res = await postJSON('/api/challenge', { target_pubkey: selectedPubkey })
        if (res.ok) {
          infoModal(
            'Challenge Issued',
            `Challenge sent to:\n${abbrev(selectedPubkey, 48)}\n\n` +
            `The target must solve a proof-of-work puzzle within 5 minutes.\n\n` +
            `• If they respond in time → their balance increases by 10\n` +
            `• If they do not respond → their balance is penalised by 15\n\n` +
            `Watch the Activity Log for updates.`,
          )
        } else {
          toast(res.error || 'Challenge failed')
        }
      } catch (e) { toast('Error: ' + e.message) }
    },
  })

  // Wire list-item selection after modal is rendered
  document.getElementById('challenge-list').addEventListener('click', e => {
    const item = e.target.closest('.modal-list-item')
    if (!item) return
    document.querySelectorAll('.modal-list-item').forEach(el => el.classList.remove('selected'))
    item.classList.add('selected')
    selectedPubkey = item.dataset.pubkey
  })
})

// ── Mempool inspector ──────────────────────────────────────────────────────

const TX_TYPE_NAMES = { 10: 'IDENTITY_REGISTER', 11: 'REPUTATION_MINE', 12: 'REPUTATION_IGNORE' }

document.getElementById('mempool-stat').addEventListener('click', async () => {
  try {
    const txs = await fetchJSON('/api/mempool')
    if (!txs.length) { infoModal('Mempool', 'No pending transactions'); return }
    const lines = txs.map(tx => {
      const typeName = TX_TYPE_NAMES[tx.type] || `type=${tx.type}`
      const ts = tx.timestamp ? new Date(tx.timestamp * 1000).toLocaleTimeString() : '?'
      return `[${ts}] ${typeName}\n  from: ${abbrev(tx.requester, 40)}\n  uid:  ${abbrev(tx.uid, 40)}`
    })
    infoModal(`Mempool — ${txs.length} pending tx(s)`, lines.join('\n\n'))
  } catch (e) { toast('Error: ' + e.message) }
})

// ── Start ──────────────────────────────────────────────────────────────────

boot().catch(console.error)
