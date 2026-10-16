// Dictation: offline Whisper on the desktop host, the browser's speech API as a fallback.

import { api, desktop } from '../lib/api.js'
import { $ } from '../lib/dom.js'
import { canEdit, isOwner, state } from '../lib/store.js'
import { errorToast, toast } from '../lib/ui.js'
import { ed } from '../editor/editor.js'

let session = null

export function initVoice() {
  $('#btnVoice').onclick = toggleDictation
}

export function toggleDictation() {
  if (session) return stop()
  if (!canEdit() || !ed.editor) return
  start()
}

async function start() {
  let engine = null
  if (isOwner()) {
    const st = await api.get('/api/voice/status').catch(() => null)
    if (st?.available) engine = 'whisper'
  }
  if (!engine && (window.SpeechRecognition || window.webkitSpeechRecognition)) engine = 'browser'
  if (!engine) {
    toast(isOwner() ? 'Offline dictation is not installed. Run: pip install "scribeos[voice]" — see Settings → Voice.' : 'Dictation is not supported in this browser.', { type: 'error', timeout: 8000 })
    return
  }
  session = engine === 'whisper' ? startWhisper() : startBrowser()
  $('#btnVoice').classList.add('recording')
  $('#dictationOverlay').hidden = false
  $('#dictationText').textContent = 'Listening…'
}

function stop() {
  session?.stop()
  session = null
  $('#btnVoice').classList.remove('recording')
  $('#dictationOverlay').hidden = true
}

function insertSegments(segments) {
  const e = ed.editor
  if (!e) return
  for (const seg of segments) {
    if (seg.cmd === 'paragraph') e.chain().focus().splitBlock().run()
    else if (seg.cmd === 'linebreak') e.chain().focus().setHardBreak().run()
    else if (seg.cmd === 'scenebreak') e.chain().focus().setHorizontalRule().run()
    else if (seg.cmd === 'undo') e.chain().focus().undo().run()
    else if (seg.cmd === 'stop') stop()
    else if (seg.text) {
      const { $from } = e.state.selection
      const before = $from.parent.textBetween(Math.max(0, $from.parentOffset - 1), $from.parentOffset)
      const lead = before && !/\s/.test(before) && !/^[,.;:!?…”)]/.test(seg.text) ? ' ' : ''
      e.chain().focus().insertContent(lead + seg.text).run()
    }
  }
}

function startWhisper() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/ws/voice`)
  ws.onopen = () => ws.send(JSON.stringify({ cmd: 'start' }))
  ws.onmessage = (e) => {
    const ev = JSON.parse(e.data)
    if (ev.type === 'level') $('#dictationLevel').style.width = `${Math.round(ev.rms * 100)}%`
    else if (ev.type === 'partial') $('#dictationText').textContent = ev.text
    else if (ev.type === 'final') {
      $('#dictationText').textContent = 'Listening…'
      insertSegments(ev.segments)
    } else if (ev.type === 'status') {
      if (ev.state === 'loading') $('#dictationText').textContent = ev.message
      if (ev.state === 'listening') $('#dictationText').textContent = 'Listening…'
      if (ev.state === 'error') {
        errorToast(new Error(ev.message))
        stop()
      }
    }
  }
  ws.onclose = () => session && stop()
  return { stop: () => { try { ws.send(JSON.stringify({ cmd: 'stop' })) } catch {} setTimeout(() => ws.close(), 300) } }
}

const LOCAL_COMMANDS = { 'new paragraph': { cmd: 'paragraph' }, 'new line': { cmd: 'linebreak' }, 'scene break': { cmd: 'scenebreak' }, 'stop dictation': { cmd: 'stop' }, comma: { text: ',' }, period: { text: '.' }, 'full stop': { text: '.' }, 'question mark': { text: '?' }, 'exclamation mark': { text: '!' }, colon: { text: ':' }, semicolon: { text: ';' } }

function parseLocal(text) {
  if (!state.settings.voice?.commands && isOwner()) return [{ text }]
  const keys = Object.keys(LOCAL_COMMANDS).sort((a, b) => b.length - a.length)
  const re = new RegExp(`\\b(${keys.join('|')})\\b`, 'gi')
  const out = []
  let pos = 0
  for (const m of text.matchAll(re)) {
    const before = text.slice(pos, m.index).trim()
    if (before) out.push({ text: before })
    out.push(LOCAL_COMMANDS[m[1].toLowerCase()])
    pos = m.index + m[0].length
  }
  const rest = text.slice(pos).trim()
  if (rest) out.push({ text: rest })
  return out
}

function startBrowser() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition
  const rec = new SR()
  rec.continuous = true
  rec.interimResults = true
  const lang = state.settings.voice?.language
  if (lang && lang !== 'auto') rec.lang = lang
  let active = true
  rec.onresult = (e) => {
    let interim = ''
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i]
      if (r.isFinal) insertSegments(parseLocal(r[0].transcript))
      else interim += r[0].transcript
    }
    $('#dictationText').textContent = interim || 'Listening…'
  }
  rec.onerror = (e) => {
    if (e.error !== 'no-speech') errorToast(new Error(`Speech recognition: ${e.error}`))
  }
  rec.onend = () => active && rec.start()
  rec.start()
  void desktop
  return { stop: () => { active = false; rec.stop() } }
}

/** Read the selection (or the chapter) aloud with the system voices. */
export function readAloud() {
  const synth = window.speechSynthesis
  if (!synth) return toast('Read-aloud is not available here', { type: 'error' })
  if (synth.speaking) {
    synth.cancel()
    return
  }
  const e = ed.editor
  if (!e) return
  const { from, to } = e.state.selection
  const text = from !== to ? e.state.doc.textBetween(from, to, '\n') : e.state.doc.textBetween(0, e.state.doc.content.size, '\n')
  if (!text.trim()) return
  const u = new SpeechSynthesisUtterance(text.slice(0, 32000))
  const lang = state.settings.voice?.language
  if (lang && lang !== 'auto') u.lang = lang
  synth.speak(u)
  toast('Reading aloud — run the command again to stop', { timeout: 2500 })
}
