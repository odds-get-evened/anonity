'use strict'

const { app, BrowserWindow, ipcMain } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const http = require('http')

// ── Ports ──────────────────────────────────────────────────────────────────
// argv[2] is the P2P port passed through npm start, e.g. `npm start -- 6001`
const P2P_PORT = parseInt(process.argv[2], 10) || 6000
const API_PORT = P2P_PORT + 1000           // 6000 → 7000, 6001 → 7001

let pythonProcess = null
let mainWindow    = null

// ── Spawn Python API server ────────────────────────────────────────────────

function startPythonServer () {
  // Support both `python3` (Unix) and `python` (Windows / conda)
  const py         = process.platform === 'win32' ? 'python' : 'python3'
  const scriptPath = path.join(__dirname, '..', 'anonity', 'identity_api.py')
  const cwd        = path.join(__dirname, '..', 'anonity')

  pythonProcess = spawn(py, [scriptPath, String(P2P_PORT), String(API_PORT)], { cwd })

  pythonProcess.stdout.on('data', d => process.stdout.write('[py] ' + d))
  pythonProcess.stderr.on('data', d => process.stderr.write('[py] ' + d))
  pythonProcess.on('close', code => {
    console.log(`[py] exited (code ${code})`)
  })
}

// ── Wait until /api/status responds ───────────────────────────────────────
// Polls up to `attempts` times with `intervalMs` between tries.

function waitForServer (attempts = 40, intervalMs = 500) {
  return new Promise((resolve, reject) => {
    let tries = 0
    const check = () => {
      const req = http.get(`http://127.0.0.1:${API_PORT}/api/status`, res => {
        res.resume()
        resolve()
      })
      req.on('error', () => {
        if (++tries >= attempts) {
          reject(new Error('Python API server did not become ready in time'))
        } else {
          setTimeout(check, intervalMs)
        }
      })
      req.end()
    }
    check()
  })
}

// ── Browser window ────────────────────────────────────────────────────────

function createWindow () {
  mainWindow = new BrowserWindow({
    width:           1100,
    height:          720,
    minWidth:        860,
    minHeight:       540,
    backgroundColor: '#1a1a2e',
    webPreferences: {
      preload:          path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration:  false,
    },
  })

  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'))
  mainWindow.on('closed', () => { mainWindow = null })
}

// ── IPC: expose API port to renderer via preload ──────────────────────────

ipcMain.handle('get-api-port', () => API_PORT)

// ── App lifecycle ─────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  startPythonServer()

  // Show window straight away; renderer renders a loading overlay while
  // it waits for the backend to become ready.
  createWindow()

  // Keep trying in the background so the renderer can poll /api/status.
  waitForServer().catch(err => {
    console.error(err.message)
  })
})

app.on('window-all-closed', () => {
  if (pythonProcess) {
    pythonProcess.kill()
    pythonProcess = null
  }
  if (process.platform !== 'darwin') app.quit()
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow()
})
