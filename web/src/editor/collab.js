// Minimal Yjs provider for ScribeOS' /ws/collab/{chapterId} protocol (see scribeos/collab/rooms.py).

import * as Y from 'yjs'
import { Awareness, applyAwarenessUpdate, encodeAwarenessUpdate, removeAwarenessStates } from 'y-protocols/awareness'

const MSG_UPDATE = 0
const MSG_AWARENESS = 1

export class ScribeProvider {
  constructor(doc, chapterId, handlers = {}) {
    this.doc = doc
    this.chapterId = chapterId
    this.handlers = handlers
    this.awareness = new Awareness(doc)
    this.ws = null
    this.roomId = null
    this.synced = false
    this.destroyed = false
    this.retry = 500
    this._onUpdate = (update, origin) => {
      if (origin !== this) this._send(MSG_UPDATE, update)
    }
    this._onAwareness = ({ added, updated, removed }, origin) => {
      if (origin === this) return
      const changed = added.concat(updated, removed)
      this._send(MSG_AWARENESS, encodeAwarenessUpdate(this.awareness, changed))
    }
    doc.on('update', this._onUpdate)
    this.awareness.on('update', this._onAwareness)
    this.connect()
  }

  connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/ws/collab/${encodeURIComponent(this.chapterId)}`)
    ws.binaryType = 'arraybuffer'
    this.ws = ws
    let firstState = true
    ws.onopen = () => {
      this.retry = 500
      ws.send(JSON.stringify({ type: 'hello', clientId: this.doc.clientID }))
      this.handlers.onStatus?.('connected')
    }
    ws.onmessage = (e) => {
      if (typeof e.data === 'string') {
        let msg
        try {
          msg = JSON.parse(e.data)
        } catch {
          return
        }
        if (msg.type === 'init') {
          if (this.roomId && msg.room !== this.roomId) {
            // The server room was recreated while we were offline: our document is stale.
            this.handlers.onReset?.()
            return
          }
          const reconnect = !!this.roomId
          this.roomId = msg.room
          this.handlers.onInit?.(msg)
          if (reconnect) this._send(MSG_UPDATE, Y.encodeStateAsUpdate(this.doc))
        } else if (msg.type === 'peer-joined') {
          this._sendLocalAwareness()
        } else if (msg.type === 'left' && Number.isInteger(msg.clientId)) {
          removeAwarenessStates(this.awareness, [msg.clientId], this)
        } else if (msg.type === 'reset') {
          this.handlers.onReset?.(msg.reason)
        } else if (msg.type === 'readonly') {
          this.handlers.onReadonly?.()
        }
        return
      }
      const data = new Uint8Array(e.data)
      if (!data.length) return
      const payload = data.subarray(1)
      if (data[0] === MSG_UPDATE) {
        Y.applyUpdate(this.doc, payload, this)
        if (firstState) {
          firstState = false
          if (!this.synced) {
            this.synced = true
            this.handlers.onSynced?.()
          }
          this._sendLocalAwareness()
        }
      } else if (data[0] === MSG_AWARENESS) {
        applyAwarenessUpdate(this.awareness, payload, this)
      }
    }
    ws.onclose = (e) => {
      if (this.destroyed) return
      this.handlers.onStatus?.('disconnected')
      if (e.code === 4004 || e.code === 4001 || e.code === 1001) {
        this.handlers.onReset?.('closed')
        return
      }
      setTimeout(() => !this.destroyed && this.connect(), this.retry)
      this.retry = Math.min(this.retry * 2, 8000)
    }
  }

  _sendLocalAwareness() {
    if (this.awareness.getLocalState() !== null) this._send(MSG_AWARENESS, encodeAwarenessUpdate(this.awareness, [this.doc.clientID]))
  }

  _send(kind, payload) {
    if (this.ws?.readyState !== WebSocket.OPEN) return
    const msg = new Uint8Array(payload.length + 1)
    msg[0] = kind
    msg.set(payload, 1)
    this.ws.send(msg)
  }

  destroy() {
    this.destroyed = true
    removeAwarenessStates(this.awareness, [this.doc.clientID], 'local')
    this.doc.off('update', this._onUpdate)
    this.awareness.off('update', this._onAwareness)
    this.awareness.destroy()
    try {
      this.ws?.close()
    } catch {}
  }
}
