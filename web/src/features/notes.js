// Floating sticky-note scratchpad (Pointer Events: works with mouse, pen and touch).

import { api } from '../lib/api.js'
import { $, clear, debounce, h, icon } from '../lib/dom.js'
import { canComment, on, state } from '../lib/store.js'
import { errorToast } from '../lib/ui.js'

const COLORS = ['yellow', 'cyan', 'purple', 'green', 'pink']

const save = debounce(async () => {
  try {
    await api.put('/api/notes', state.notes)
  } catch (e) {
    errorToast(e)
  }
}, 600)

export function initNotes() {
  $('#btnNotesToggle').onclick = toggleNotes
  on('project:loaded', loadNotes)
}

export async function loadNotes() {
  try {
    state.notes = await api.get('/api/notes')
  } catch {
    state.notes = []
  }
  renderNotes()
}

export function toggleNotes() {
  const layer = $('#notesLayer')
  layer.hidden = !layer.hidden
  $('#btnNotesToggle').classList.toggle('active', !layer.hidden)
  if (!layer.hidden) renderNotes()
}

function newId() {
  return 'n' + Date.now().toString(16) + Math.random().toString(16).slice(2, 8)
}

export function renderNotes() {
  const layer = $('#notesLayer')
  clear(layer)
  const editable = canComment()
  if (editable) {
    layer.append(h('button.floating-add-note', { onclick: addNote }, icon('plus'), 'Add note'))
  }
  for (const note of state.notes) layer.append(noteEl(note, editable))
}

function addNote() {
  state.notes.push({ id: newId(), text: '', color: 'yellow', x: Math.round(window.innerWidth / 2 - 125), y: Math.round(window.innerHeight / 2 - 100), w: 250, h: 200 })
  save()
  renderNotes()
  const last = $('#notesLayer').lastElementChild?.querySelector('textarea')
  last?.focus()
}

function noteEl(note, editable) {
  const body = h('textarea.note-body', { value: note.text, placeholder: 'Idea, plot hole, character flaw…', readOnly: !editable, maxlength: 20000, 'aria-label': 'Note text' })
  body.addEventListener('input', () => {
    note.text = body.value
    save()
  })
  const header = h('div.note-header',
    h('div.note-tools', COLORS.map((c) => h(`button.note-color.c-${c}`, { title: c, 'aria-label': `Colour ${c}`, disabled: !editable, onclick: () => { note.color = c; el.className = `sticky-note ${c}`; save() } }))),
    editable ? h('button.icon-btn.small', { title: 'Delete note', 'aria-label': 'Delete note', onclick: () => { state.notes = state.notes.filter((n) => n.id !== note.id); el.remove(); save() } }, icon('x')) : null)
  const el = h(`div.sticky-note.${note.color}`, { style: { left: note.x + 'px', top: note.y + 'px', width: note.w + 'px', height: note.h + 'px' } }, header, body)

  if (editable) {
    header.addEventListener('pointerdown', (e) => {
      if (e.target.closest('button')) return
      header.setPointerCapture(e.pointerId)
      const sx = e.clientX - note.x
      const sy = e.clientY - note.y
      const move = (ev) => {
        note.x = Math.round(Math.max(-100, Math.min(window.innerWidth - 60, ev.clientX - sx)))
        note.y = Math.round(Math.max(0, Math.min(window.innerHeight - 40, ev.clientY - sy)))
        el.style.left = note.x + 'px'
        el.style.top = note.y + 'px'
      }
      const up = () => {
        header.removeEventListener('pointermove', move)
        header.removeEventListener('pointerup', up)
        save()
      }
      header.addEventListener('pointermove', move)
      header.addEventListener('pointerup', up)
    })
    new ResizeObserver(() => {
      const w = Math.round(el.offsetWidth)
      const hh = Math.round(el.offsetHeight)
      if (w && hh && (w !== note.w || hh !== note.h)) {
        note.w = Math.max(120, w)
        note.h = Math.max(80, hh)
        save()
      }
    }).observe(el)
  }
  return el
}
