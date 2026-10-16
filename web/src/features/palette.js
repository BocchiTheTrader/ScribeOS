// Command palette (Ctrl+K): every action, searchable by name.

import { clear, h, icon } from '../lib/dom.js'
import { canEdit, chapters, isOwner } from '../lib/store.js'
import { modal } from '../lib/ui.js'
import { mountChapter } from '../editor/editor.js'

let commands = []
export const registerCommands = (list) => (commands = list)

function score(text, q) {
  text = text.toLowerCase()
  if (text.includes(q)) return 100 - text.indexOf(q)
  let i = 0
  for (const ch of text) if (ch === q[i]) i++
  return i === q.length ? 10 : -1
}

export function openPalette() {
  const m = modal('Command palette', { width: 560, className: 'palette' })
  const input = h('input.input.palette-input', { placeholder: 'Type a command or chapter name…', autofocus: true, 'aria-label': 'Command' })
  const list = h('div.palette-list', { role: 'listbox' })
  m.body.append(input, list)
  let items = []
  let sel = 0
  const all = () => [
    ...commands.filter((c) => (!c.owner || isOwner()) && (!c.edit || canEdit()) && (!c.when || c.when())),
    ...chapters().map((c) => ({ label: `Go to: ${c.title}`, icon: 'file-text', run: () => mountChapter(c.id) })),
  ]
  const render = () => {
    const q = input.value.trim().toLowerCase()
    items = all().map((c) => ({ c, s: q ? score(c.label, q) : 0 })).filter((x) => x.s >= 0).sort((a, b) => b.s - a.s).slice(0, 60).map((x) => x.c)
    sel = Math.min(sel, Math.max(0, items.length - 1))
    clear(list)
    items.forEach((c, i) => list.append(h(`button.palette-item${i === sel ? '.selected' : ''}`, { role: 'option', 'aria-selected': String(i === sel), onmouseenter: () => { sel = i; highlight() }, onclick: () => exec(c) }, icon(c.icon || 'lightning'), h('span', c.label), c.hint ? h('kbd', c.hint) : null)))
  }
  const highlight = () => [...list.children].forEach((el, i) => el.classList.toggle('selected', i === sel))
  const exec = (c) => {
    m.close()
    setTimeout(() => c.run(), 0)
  }
  input.addEventListener('input', () => { sel = 0; render() })
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(items.length - 1, sel + 1); highlight(); list.children[sel]?.scrollIntoView({ block: 'nearest' }) }
    if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); highlight(); list.children[sel]?.scrollIntoView({ block: 'nearest' }) }
    if (e.key === 'Enter' && items[sel]) { e.preventDefault(); exec(items[sel]) }
  })
  render()
}
