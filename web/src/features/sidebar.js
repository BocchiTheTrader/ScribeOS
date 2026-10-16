// Manuscript tree: parts, chapters, drag-and-drop reordering, inline rename, status.

import { api } from '../lib/api.js'
import { $, clear, debounce, formatNumber, h, icon } from '../lib/dom.js'
import { canEdit, emit, findChapter, isOwner, on, state } from '../lib/store.js'
import { confirmDialog, errorToast, popover, promptDialog, toast } from '../lib/ui.js'
import { ed, mountChapter } from '../editor/editor.js'

export const STATUS = { todo: 'To do', draft: 'Draft', revised: 'Revised', final: 'Final' }
const LABELS = ['#ef4444', '#f59e0b', '#22c55e', '#3b82f6', '#a855f7', '#ec4899']

let dragId = null

export function initSidebar() {
  $('#btnNewChapter').onclick = () => newChapter()
  $('#btnNewPart').onclick = newPart
  on('project:loaded', renderTree)
  on('chapter:changed', renderTree)
  on('chapter:saved', ({ id, wordCount }) => {
    const c = findChapter(id)
    if (c) c.wordCount = wordCount
    const badge = document.querySelector(`.tree-chapter[data-id="${CSS.escape(id)}"] .wc`)
    if (badge) badge.textContent = formatNumber(wordCount)
    emit('words:changed')
  })
  const title = $('#chapterTitle')
  const saveTitle = debounce(async (id, value) => {
    try {
      await api.patch(`/api/project/nodes/${id}`, { title: value })
    } catch (e) {
      errorToast(e)
    }
  }, 600)
  title.addEventListener('input', () => {
    const c = findChapter(ed.chapterId)
    if (!c || !canEdit()) return
    c.title = title.value.slice(0, 300) || 'Untitled'
    const el = document.querySelector(`.tree-chapter[data-id="${CSS.escape(c.id)}"] .tree-title`)
    if (el) el.textContent = c.title
    saveTitle(c.id, c.title)
  })
  title.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      ed.editor?.commands.focus('start')
    }
  })
}

export function renderTree() {
  const ul = $('#chapterTree')
  clear(ul)
  if (!state.project) return
  $('#projectName').textContent = state.project.name
  $('#projectName').title = state.project.path || ''
  for (const node of state.project.tree) {
    if (node.type === 'part') ul.append(partItem(node))
    else ul.append(chapterItem(node, null))
  }
  // Drop zone at the end of the top level.
  const end = h('li.tree-dropzone', { 'aria-hidden': 'true' })
  wireDrop(end, () => ({ parent: null, before: null }))
  ul.append(end)
}

function partItem(part) {
  const kids = h('ul.tree-children', part.children.map((c) => chapterItem(c, part.id)))
  const total = part.children.reduce((s, c) => s + (c.wordCount || 0), 0)
  const head = h('div.tree-part-head', { draggable: canEdit(), title: 'Part — drag chapters here' },
    h('button.icon-btn.small.twisty', { 'aria-label': part.collapsed ? 'Expand' : 'Collapse', onclick: () => toggleCollapse(part) }, icon(part.collapsed ? 'caret-right' : 'caret-down')),
    icon('folder-simple'),
    h('span.tree-title', part.title),
    h('span.wc', formatNumber(total)),
    canEdit() ? h('button.icon-btn.small.more', { 'aria-label': 'Part actions', onclick: (e) => { e.stopPropagation(); partMenu(e.currentTarget, part) } }, icon('dots-three')) : null,
  )
  head.ondblclick = () => renamePart(part)
  wireDrop(head, () => ({ parent: part.id, before: null }))
  if (part.collapsed) kids.hidden = true
  return h('li.tree-part', { role: 'treeitem', 'aria-expanded': String(!part.collapsed) }, head, kids)
}

function chapterItem(c, parentId) {
  const active = c.id === ed.chapterId
  const li = h(`li.tree-chapter${active ? '.active' : ''}`, { role: 'treeitem', 'aria-selected': String(active), dataset: { id: c.id }, draggable: canEdit(), tabindex: 0 },
    h('span.status-dot', { class: `status-${c.status}`, title: STATUS[c.status] }),
    c.label ? h('span.label-dot', { style: { background: c.label } }) : null,
    h('span.tree-title', c.title),
    h('span.wc', formatNumber(c.wordCount)),
    canEdit() ? h('button.icon-btn.small.more', { 'aria-label': 'Chapter actions', onclick: (e) => { e.stopPropagation(); chapterMenu(e.currentTarget, c) } }, icon('dots-three')) : null,
  )
  li.onclick = () => mountChapter(c.id)
  li.onkeydown = (e) => {
    if (e.key === 'Enter') mountChapter(c.id)
    if (e.key === 'Delete' && canEdit()) deleteChapter(c)
    if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown') && canEdit()) {
      e.preventDefault()
      moveChapter(c.id, e.key === 'ArrowUp' ? -1 : 1)
    }
  }
  li.addEventListener('dragstart', (e) => {
    dragId = c.id
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', c.title)
    li.classList.add('dragging')
  })
  li.addEventListener('dragend', () => {
    dragId = null
    li.classList.remove('dragging')
  })
  wireDrop(li, () => ({ parent: parentId, before: c.id }))
  return li
}

function wireDrop(el, target) {
  el.addEventListener('dragover', (e) => {
    if (!dragId) return
    e.preventDefault()
    el.classList.add('drop-target')
  })
  el.addEventListener('dragleave', () => el.classList.remove('drop-target'))
  el.addEventListener('drop', (e) => {
    e.preventDefault()
    el.classList.remove('drop-target')
    if (!dragId) return
    const { parent, before } = target()
    if (before === dragId) return
    moveTo(dragId, parent, before)
  })
}

function cloneTree() {
  return state.project.tree.map((n) => (n.type === 'part' ? { ...n, children: [...n.children] } : n))
}

function detach(tree, id) {
  for (let i = 0; i < tree.length; i++) {
    const n = tree[i]
    if (n.id === id) return tree.splice(i, 1)[0]
    if (n.type === 'part') {
      const j = n.children.findIndex((c) => c.id === id)
      if (j >= 0) return n.children.splice(j, 1)[0]
    }
  }
  return null
}

async function moveTo(id, parentId, beforeId) {
  const tree = cloneTree()
  const node = detach(tree, id)
  if (!node) return
  const container = parentId ? tree.find((n) => n.id === parentId)?.children : tree
  if (!container) return
  const idx = beforeId ? container.findIndex((n) => n.id === beforeId) : -1
  container.splice(idx < 0 ? container.length : idx, 0, node)
  await saveTree(tree)
}

async function moveChapter(id, dir) {
  const flat = []
  for (const n of state.project.tree) {
    if (n.type === 'part') n.children.forEach((c, i) => flat.push({ id: c.id, parent: n.id, i }))
    else flat.push({ id: n.id, parent: null })
  }
  const idx = flat.findIndex((f) => f.id === id)
  const other = flat[idx + dir]
  if (!other) return
  const tree = cloneTree()
  const node = detach(tree, id)
  const container = other.parent ? tree.find((n) => n.id === other.parent).children : tree
  let at = container.findIndex((n) => n.id === other.id)
  if (dir > 0) at += 1
  container.splice(at, 0, node)
  await saveTree(tree)
  document.querySelector(`.tree-chapter[data-id="${CSS.escape(id)}"]`)?.focus()
}

export async function saveTree(tree) {
  const prev = state.project.tree
  state.project.tree = tree
  renderTree()
  try {
    const meta = await api.put('/api/project/tree', { tree })
    state.project = { ...state.project, ...meta }
  } catch (e) {
    state.project.tree = prev
    renderTree()
    errorToast(e)
  }
}

async function toggleCollapse(part) {
  part.collapsed = !part.collapsed
  renderTree()
  if (canEdit()) api.patch(`/api/project/nodes/${part.id}`, { collapsed: part.collapsed }).catch(() => {})
}

export async function newChapter(title = 'New Chapter', template = null) {
  if (!canEdit()) return
  try {
    const node = await api.post('/api/project/chapters', { title, after: ed.chapterId && findChapterTopLevel(ed.chapterId) ? ed.chapterId : null, template })
    await reloadProject()
    await mountChapter(node.id)
    $('#chapterTitle').select()
  } catch (e) {
    errorToast(e)
  }
}

function findChapterTopLevel(id) {
  return state.project.tree.some((n) => n.id === id)
}

async function newPart() {
  if (!canEdit()) return
  const title = await promptDialog('Name of the new part', { value: `Part ${state.project.tree.filter((n) => n.type === 'part').length + 1}` })
  if (!title) return
  try {
    await api.post('/api/project/parts', { title })
    await reloadProject()
    toast('Part created — drag chapters onto it')
  } catch (e) {
    errorToast(e)
  }
}

export async function reloadProject() {
  state.project = await api.get('/api/project')
  emit('project:loaded', state.project)
}

async function renamePart(part) {
  if (!canEdit()) return
  const title = await promptDialog('Rename part', { value: part.title })
  if (!title) return
  await api.patch(`/api/project/nodes/${part.id}`, { title }).catch(errorToast)
  await reloadProject()
}

function partMenu(btn, part) {
  popover(btn, [
    { label: 'Rename', icon: 'pencil-simple', run: () => renamePart(part) },
    { label: 'New chapter in this part', icon: 'plus', run: async () => {
      const node = await api.post('/api/project/chapters', { title: 'New Chapter', partId: part.id }).catch(errorToast)
      if (node) { await reloadProject(); mountChapter(node.id) }
    } },
    { separator: true },
    { label: 'Dissolve part (keep chapters)', icon: 'folder-minus', danger: true, run: async () => {
      await api.del(`/api/project/nodes/${part.id}`).catch(errorToast)
      await reloadProject()
    } },
  ])
}

function chapterMenu(btn, c) {
  popover(btn, [
    { label: 'Rename', icon: 'pencil-simple', run: async () => {
      const title = await promptDialog('Rename chapter', { value: c.title })
      if (title) { await api.patch(`/api/project/nodes/${c.id}`, { title }).catch(errorToast); await reloadProject(); if (c.id === ed.chapterId) $('#chapterTitle').value = title }
    } },
    { label: 'Synopsis…', icon: 'note-pencil', run: () => editSynopsis(c) },
    { header: 'Status' },
    ...Object.entries(STATUS).map(([k, v]) => ({ label: v, icon: k === c.status ? 'check' : 'circle', active: k === c.status, run: async () => { await api.patch(`/api/project/nodes/${c.id}`, { status: k }).catch(errorToast); await reloadProject() } })),
    { header: 'Label' },
    { element: h('div.label-row', [null, ...LABELS].map((col) => h('button.label-swatch', { title: col ? 'Label' : 'No label', style: { background: col || 'transparent' }, onclick: async () => { document.querySelector('.popover')?.remove(); await api.patch(`/api/project/nodes/${c.id}`, { label: col }).catch(errorToast); await reloadProject() } }, col ? '' : '∅'))) },
    { separator: true },
    { label: 'Move up', icon: 'arrow-up', hint: 'Alt+↑', run: () => moveChapter(c.id, -1) },
    { label: 'Move down', icon: 'arrow-down', hint: 'Alt+↓', run: () => moveChapter(c.id, 1) },
    { separator: true },
    { label: 'Delete chapter', icon: 'trash', danger: true, run: () => deleteChapter(c) },
  ])
}

export async function editSynopsis(c) {
  const v = await promptDialog(`Synopsis — ${c.title}`, { value: c.synopsis || '', maxlength: 5000, title: 'Synopsis' })
  if (v === null) return
  await api.patch(`/api/project/nodes/${c.id}`, { synopsis: v }).catch(errorToast)
  await reloadProject()
}

async function deleteChapter(c) {
  if (!(await confirmDialog(`"${c.title}" will be moved to the project's trash. You can restore it later from Project menu → Trash.`, { title: 'Delete chapter?', ok: 'Delete', danger: true }))) return
  try {
    await api.del(`/api/project/nodes/${c.id}`)
    await reloadProject()
    if (ed.chapterId === c.id) await mountChapter(state.project.activeChapterId, { force: true })
    toast('Chapter moved to trash', { action: { label: 'Undo', run: async () => { await api.post(`/api/project/trash/${c.id}/restore`); await reloadProject() } } })
  } catch (e) {
    errorToast(e)
  }
}

export { isOwner }
