'use strict'

const { contextBridge, ipcRenderer } = require('electron')

// Expose only what the renderer needs: the dynamically-assigned API port.
contextBridge.exposeInMainWorld('anonity', {
  getApiPort: () => ipcRenderer.invoke('get-api-port'),
})
