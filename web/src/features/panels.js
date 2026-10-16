// Side panels: snapshots, project search & replace, codex, media library, trash.

import { api } from '../lib/api.js'
import { $, clear, debounce, formatNumber, h, icon, timeAgo } from '../lib/dom.js'
import { canEdit, chapters, isOwner, on, state } from '../lib/store.js'
import { confirmDialog, errorToast, modal, openPanel, panelOpen, promptDialog, toast } from '../lib/ui.js'
import { ed, mountChapter, refreshDecorations } from '../editor/editor.js'
import { reloadProject } from './sidebar.js'

// ------------------------------------------------------------------ snapshots
export function openSnapshots() {
  openPanel('snapshots', 'Snapshots', (body) => {
    const list = h('div.snap-list')
    const load = async () => {
      clear(list)
      const cid = ed.chapterId
      if (!cid) return
      const snaps = await api.get(`/api/chapters/${cid}/snapshots`).catch(() => [])
      if (!snaps.length) list.append(h('p.muted', 'No snapshots yet. Take one before a big rewrite — you can always go back.'))
      for (const s of snaps) {
        list.append(h('button.snap-item', { onclick: () => previewSnapshot(cid, s, load) },
          h('span.snap-label', s.label || (s.auto ? 'Automatic' : 'Snapshot')), h('small.muted', `${timeAgo(s.createdAt)} · ${formatNumber(s.wordCount)} words`)))
      }
    }
    body.append(
      h('p.muted.small', 'A snapshot is a saved version of the current chapter. ScribeOS also takes them automatically while you write.'),
      canEdit() ? h('button.btn.wide', { onclick: async () => {
        const label = await promptDialog('Name this snapshot (optional)', { value: '', placeholder: 'e.g. Before rewriting the ending', maxlength: 120 })
        if (label === null) return
        await api.post(`/api/chapters/${ed.chapterId}/snapshots`, { label }).catch(errorToast)
        toast('Snapshot saved')
        load()
      } }, icon('camera'), 'Take snapshot') : null,
      list)
    load()
    const off = on('chapter:changed', () => (panelOpen('snapshots') ? load() : off()))
  })
}

function wordDiff(a, b) {
  // Paragraph-level LCS, then highlight changed paragraphs.
  const pa = a.split(/\n\n/)
  const pb = b.split(/\n\n/)
  const n = pa.length
  const m = pb.length
  if (n * m > 4_000_000) return [{ t: 'same', s: 'Document too large to compare.' }]
  const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1))
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = pa[i] === pb[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const out = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (pa[i] === pb[j]) { out.push({ t: 'same', s: pa[i] }); i++; j++ }
    else if (dp[i + 1][j] >= dp[i][j + 1]) out.push({ t: 'del', s: pa[i++] })
    else out.push({ t: 'add', s: pb[j++] })
  }
  while (i < n) out.push({ t: 'del', s: pa[i++] })
  while (j < m) out.push({ t: 'add', s: pb[j++] })
  return out
}

async function previewSnapshot(cid, s, reload) {
  let snap
  try {
    snap = await api.get(`/api/chapters/${cid}/snapshots/${s.id}`)
  } catch (e) {
    return errorToast(e)
  }
  const m = modal(`Snapshot — ${s.label || timeAgo(s.createdAt)}`, { width: 860 })
  const diff = wordDiff(snap.text, snap.currentText)
  const changed = diff.filter((d) => d.t !== 'same').length
  m.body.append(
    h('p.muted.small', changed ? h('span', h('span.diff-legend.del', 'Red'), ' = only in this snapshot, ', h('span.diff-legend.add', 'green'), ' = only in the current text.') : 'This snapshot is identical to the current chapter.'),
    h('div.diff-view', diff.map((d) => h(`p.diff-${d.t}`, d.s || ' '))),
    h('div.dialog-actions',
      canEdit() ? h('button.btn.ghost.danger', { onclick: async () => { await api.del(`/api/chapters/${cid}/snapshots/${s.id}`).catch(errorToast); m.close(); reload() } }, icon('trash'), 'Delete') : null,
      h('button.btn', { onclick: () => m.close() }, 'Close'),
      canEdit() ? h('button.btn.primary', { onclick: async () => {
        if (!(await confirmDialog('The current text is snapshotted first, so this can be undone.', { title: 'Restore this version?', ok: 'Restore' }))) return
        try {
          await api.post(`/api/chapters/${cid}/snapshots/${s.id}/restore`)
          m.close()
          await mountChapter(cid, { force: true })
          toast('Snapshot restored')
          reload()
        } catch (e) {
          errorToast(e)
        }
      } }, icon('clock-counter-clockwise'), 'Restore') : null))
}

// ------------------------------------------------------------------ search & replace
export function openSearch() {
  openPanel('search', 'Find in project', (body) => {
    const q = h('input.input', { placeholder: 'Search all chapters…', maxlength: 1000, autofocus: true, 'aria-label': 'Search' })
    const rep = h('input.input', { placeholder: 'Replace with…', maxlength: 1000, 'aria-label': 'Replace with' })
    const opts = { case: false, whole: false, regex: false }
    const flag = (k, label, title, disabled) => h('label.check.small', { title }, h('input', { type: 'checkbox', disabled, onchange: (e) => { opts[k] = e.target.checked; run() } }), label)
    const results = h('div.search-results')
    const run = debounce(async () => {
      clear(results)
      if (!q.value.trim()) return
      let hits
      try {
        hits = await api.post('/api/search', { q: q.value, ...opts })
      } catch (e) {
        results.append(h('p.error', e.message))
        return
      }
      results.append(h('p.muted.small', `${hits.length}${hits.length >= 500 ? '+' : ''} matches`))
      let last = null
      for (const r of hits) {
        if (r.chapterId !== last) {
          results.append(h('div.result-chapter', r.chapterTitle))
          last = r.chapterId
        }
        results.append(h('button.result', { onclick: () => jumpTo(r) }, h('span.muted', r.before), h('mark', r.match), h('span.muted', r.after)))
      }
    }, 250)
    q.addEventListener('input', run)
    body.append(q,
      h('div.flags', flag('case', 'Aa', 'Match case'), flag('whole', 'Whole word', 'Whole words only'), flag('regex', '.*', isOwner() ? 'Regular expression' : 'Regular expressions are host-only', !isOwner())),
      canEdit() ? h('div.replace-row', rep, h('button.btn.small', { onclick: async () => {
        if (!q.value) return
        if (!(await confirmDialog(`Replace every match of “${q.value}” with “${rep.value}” in the whole project? A snapshot of each changed chapter is taken first.`, { title: 'Replace all?', ok: 'Replace all' }))) return
        try {
          const r = await api.post('/api/replace', { q: q.value, replacement: rep.value, ...opts })
          toast(`Replaced ${r.replaced} matches in ${r.chapters.length} chapters`)
          await reloadProject()
          if (r.chapters.includes(ed.chapterId)) await mountChapter(ed.chapterId, { force: true })
          run()
        } catch (e) {
          errorToast(e)
        }
      } }, 'Replace all')) : null,
      results)
  })
}

async function jumpTo(r) {
  await mountChapter(r.chapterId)
  const editor = ed.editor
  if (!editor) return
  let found = null
  let block = -1
  editor.state.doc.descendants((node, pos) => {
    if (found) return false
    if (node.isTextblock) {
      block++
      if (block === r.block) {
        const text = node.textContent
        const idx = text.indexOf(r.match, Math.max(0, r.before.length - 2))
        const at = idx >= 0 ? idx : text.indexOf(r.match)
        if (at >= 0) found = { from: pos + 1 + at, to: pos + 1 + at + r.match.length }
        return false
      }
    }
  })
  if (found) {
    editor.chain().focus().setTextSelection(found).scrollIntoView().run()
  }
}

// ------------------------------------------------------------------ codex
const KINDS = { character: 'Characters', place: 'Places', item: 'Items', faction: 'Factions', lore: 'Lore', other: 'Other' }
const KIND_ICON = { character: 'user', place: 'map-pin', item: 'sword', faction: 'flag', lore: 'scroll', other: 'dots-three' }

export async function loadCodex() {
  state.codex = await api.get('/api/codex').catch(() => [])
  refreshDecorations()
}

const saveCodex = debounce(async () => {
  try {
    state.codex = await api.put('/api/codex', state.codex)
    refreshDecorations()
  } catch (e) {
    errorToast(e)
  }
}, 500)

export function openCodex(focusId = null) {
  openPanel('codex', 'Codex', (body) => {
    const filter = h('input.input', { placeholder: 'Filter…', 'aria-label': 'Filter codex' })
    const list = h('div.codex-list')
    const render = () => {
      clear(list)
      const f = filter.value.toLowerCase()
      for (const [kind, label] of Object.entries(KINDS)) {
        const items = state.codex.filter((e) => e.kind === kind && (!f || e.name.toLowerCase().includes(f) || e.aliases.some((a) => a.toLowerCase().includes(f))))
        if (!items.length) continue
        list.append(h('div.codex-group', icon(KIND_ICON[kind]), label))
        for (const e of items) list.append(h('button.codex-item', { onclick: () => editEntry(e, render) }, h('span.codex-dot', { style: { background: e.color } }), h('span', e.name), e.aliases.length ? h('small.muted', e.aliases.join(', ')) : null))
      }
      if (!list.childElementCount) list.append(h('p.muted', state.codex.length ? 'Nothing matches.' : 'Add your characters, places and items. Their names are underlined in the text and help dictation spell them correctly.'))
    }
    filter.addEventListener('input', render)
    body.append(h('div.codex-top', filter, canEdit() ? h('button.btn.small', { onclick: () => editEntry(null, render) }, icon('plus'), 'New') : null), list)
    render()
    if (focusId) {
      const e = state.codex.find((x) => x.id === focusId)
      if (e) editEntry(e, render)
    }
  })
}

function editEntry(entry, rerender) {
  const isNew = !entry
  const e = entry ? { ...entry, aliases: [...entry.aliases] } : { id: 'x' + Date.now().toString(16) + Math.random().toString(16).slice(2, 8), name: '', kind: 'character', aliases: [], description: '', color: '#f59e0b', image: null }
  const ro = !canEdit()
  const m = modal(isNew ? 'New codex entry' : e.name, { width: 560 })
  const name = h('input.input', { value: e.name, maxlength: 120, readOnly: ro, autofocus: true })
  const kind = h('select.input', { disabled: ro }, Object.entries(KINDS).map(([k, l]) => h('option', { value: k, selected: e.kind === k }, l.replace(/s$/, ''))))
  const aliases = h('input.input', { value: e.aliases.join(', '), maxlength: 1000, readOnly: ro, placeholder: 'Nicknames, titles… (comma separated)' })
  const desc = h('textarea.input', { rows: 8, maxlength: 20000, readOnly: ro }, e.description)
  const color = h('input', { type: 'color', value: e.color, disabled: ro })
  m.body.append(
    h('div.grid-2', h('label.field', 'Name', name), h('label.field', 'Type', kind)),
    h('label.field', 'Also known as', aliases),
    h('label.field', 'Notes', desc),
    h('label.field.inline', 'Colour', color),
    h('div.dialog-actions',
      !isNew && !ro ? h('button.btn.ghost.danger', { onclick: async () => {
        if (!(await confirmDialog(`Delete “${e.name}” from the codex?`, { title: 'Delete entry?', ok: 'Delete', danger: true }))) return
        state.codex = state.codex.filter((x) => x.id !== e.id)
        saveCodex()
        m.close()
        rerender()
      } }, icon('trash'), 'Delete') : null,
      h('button.btn', { onclick: () => m.close() }, ro ? 'Close' : 'Cancel'),
      ro ? null : h('button.btn.primary', { onclick: () => {
        if (!name.value.trim()) return toast('Name is required', { type: 'error' })
        Object.assign(e, { name: name.value.trim(), kind: kind.value, aliases: aliases.value.split(',').map((a) => a.trim()).filter(Boolean).slice(0, 20), description: desc.value, color: color.value })
        if (isNew) state.codex.push(e)
        else state.codex = state.codex.map((x) => (x.id === e.id ? e : x))
        saveCodex()
        m.close()
        rerender()
      } }, 'Save')))
}

/** Hover cards for codex mentions inside the editor. */
export function initCodexHover() {
  let card = null
  $('#editor').addEventListener('mouseover', (ev) => {
    const el = ev.target.closest?.('.codex-mention')
    card?.remove()
    card = null
    if (!el) return
    const entry = state.codex.find((e) => e.id === el.dataset.codexId)
    if (!entry) return
    const r = el.getBoundingClientRect()
    card = h('div.codex-card', { style: { left: Math.min(r.left, window.innerWidth - 300) + 'px', top: r.bottom + 6 + 'px' } },
      h('strong', entry.name), h('small.muted', ` · ${KINDS[entry.kind].replace(/s$/, '')}`), entry.description ? h('p', entry.description.slice(0, 280) + (entry.description.length > 280 ? '…' : '')) : null)
    document.body.append(card)
  })
  $('#editor').addEventListener('mouseout', (ev) => {
    if (ev.target.closest?.('.codex-mention')) {
      card?.remove()
      card = null
    }
  })
  $('#editor').addEventListener('click', (ev) => {
    const el = ev.target.closest?.('.codex-mention')
    if (el && ev.altKey) openCodex(el.dataset.codexId)
  })
}

// ------------------------------------------------------------------ media library
export function openAssets() {
  openPanel('assets', 'Media library', async (body) => {
    const grid = h('div.asset-grid')
    body.append(h('p.muted.small', 'Every image, GIF, video and audio file in this project. Click to insert at the cursor.'), grid)
    const items = await api.get('/api/assets').catch(() => [])
    if (!items.length) grid.append(h('p.muted', 'No media yet. Paste or drop images into the editor.'))
    for (const a of items) {
      const thumb = a.kind === 'image' ? h('img', { src: a.name.endsWith('.svg') ? a.src : `${a.src}/thumb`, alt: '', loading: 'lazy' }) : h('div.asset-icon', icon(a.kind === 'video' ? 'film-strip' : 'music-note'))
      grid.append(h('div.asset', { title: a.usedIn.length ? `Used in: ${a.usedIn.join(', ')}` : 'Not used anywhere' },
        h('button.asset-thumb', { disabled: !canEdit(), onclick: () => ed.editor?.chain().focus().insertContent(a.kind === 'image' ? { type: 'image', attrs: { src: a.src } } : { type: a.kind, attrs: { src: a.src } }).run() }, thumb),
        h('small', `${(a.size / 1048576).toFixed(a.size > 1048576 ? 1 : 2)} MB`, a.usedIn.length ? '' : ' · unused'),
        isOwner() && !a.usedIn.length ? h('button.icon-btn.small', { title: 'Delete unused file', onclick: async () => {
          if (!(await confirmDialog('This permanently deletes the file from the project folder.', { title: 'Delete file?', ok: 'Delete', danger: true }))) return
          await api.del(`/api/assets/${a.name}`).catch(errorToast)
          openAssets()
          openAssets()
        } }, icon('trash')) : null))
    }
  })
}

// ------------------------------------------------------------------ trash
export async function openTrash() {
  const m = modal('Trash', { width: 520 })
  const items = await api.get('/api/project/trash').catch(() => [])
  if (!items.length) m.body.append(h('p.muted', 'Deleted chapters appear here and can be restored.'))
  for (const t of items) {
    m.body.append(h('div.trash-item', h('span', t.title), h('small.muted', `${formatNumber(t.wordCount)} words · ${timeAgo(t.deletedAt)}`),
      canEdit() ? h('button.btn.small', { onclick: async () => {
        try {
          const node = await api.post(`/api/project/trash/${t.id}/restore`)
          await reloadProject()
          m.close()
          mountChapter(node.id)
          toast('Chapter restored')
        } catch (e) {
          errorToast(e)
        }
      } }, 'Restore') : null))
  }
  void chapters
}
