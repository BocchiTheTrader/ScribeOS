// Server-pushed events over /ws/events with automatic reconnect.

import { emit } from './store.js'

let ws = null
let retry = 500
let pingTimer = null
let stopped = false

export function connectEvents() {
  stopped = false
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/ws/events`)
  ws.onopen = () => {
    retry = 500
    emit('connection', true)
    clearInterval(pingTimer)
    pingTimer = setInterval(() => ws?.readyState === 1 && ws.send('ping'), 20000)
  }
  ws.onmessage = (e) => {
    if (e.data === 'pong') return
    try {
      const ev = JSON.parse(e.data)
      emit('server:' + ev.type, ev)
    } catch {}
  }
  ws.onclose = (e) => {
    clearInterval(pingTimer)
    emit('connection', false)
    if (stopped) return
    if (e.code === 4001) {
      emit('server:signed-out', {})
      return
    }
    setTimeout(connectEvents, retry)
    retry = Math.min(retry * 2, 10000)
  }
}

export function disconnectEvents() {
  stopped = true
  ws?.close()
}
