// Creates and swaps the TipTap editor for the current chapter, in solo or live-collaboration mode.

import { Editor } from '@tiptap/core'
import Collaboration from '@tiptap/extension-collaboration'
import CollaborationCaret from '@tiptap/extension-collaboration-caret'
import Highlight from '@tiptap/extension-highlight'
import TextAlign from '@tiptap/extension-text-align'
import { Color, TextStyle } from '@tiptap/extension-text-style'
import Typography from '@tiptap/extension-typography'
import { Placeholder } from '@tiptap/extensions'
import StarterKit from '@tiptap/starter-kit'
import { NodeSelection } from '@tiptap/pm/state'
import * as Y from 'yjs'

import { api, desktop, upload } from '../lib/api.js'
import { $, clear, countWords, debounce, h, icon, store } from '../lib/dom.js'
import { canEdit, emit, findChapter, state, teamActive } from '../lib/store.js'
import { errorToast, toast } from '../lib/ui.js'
import { ScribeProvider } from './collab.js'
import { Audio, CodexHighlight, FileDrop, FigureImage, FocusMode, Video, codexKey } from './extensions.js'

export const ed = { editor: null, provider: null, ydoc: null, chapterId: null, collab: false, dirty: false, mounting: null }

const journalKey = (cid) => `scribeos:pending:${state.project?.id}:${cid}`

function setSaveState(text, cls = '') {
  const el = $('#saveState')
  el.textContent = text
  el.className = 'save-state ' + cls
}

async function saveNow() {
  const { editor, chapterId, collab } = ed
  if (!editor || collab || !ed.dirty || !canEdit()) return
  const doc = editor.getJSON()
  ed.dirty = false
  setSaveState('Saving…')
  try {
    const res = await api.put(`/api/chapters/${chapterId}`, { doc })
    store(journalKey(chapterId), null)
    const node = findChapter(chapterId)
    if (node) node.wordCount = res.wordCount
    emit('chapter:saved', { id: chapterId, ...res })
    setSaveState('Saved', 'ok')
  } catch (e) {
    ed.dirty = true
    setSaveState(e.status === 409 ? 'Live session — reloading' : 'Not saved — kept locally', 'warn')
    if (e.status === 409) emit('team:changed')
    else saveSoon()
  }
}

const saveSoon = debounce(saveNow, 900)
export const flushSave = () => saveSoon.flush()

/** Best-effort save while the page is unloading. */
export function saveOnUnload() {
  const { editor, chapterId, collab } = ed
  if (!editor || collab || !ed.dirty) return
  const doc = editor.getJSON()
  store(journalKey(chapterId), { doc, at: Date.now() })
  try {
    fetch(`/api/chapters/${chapterId}`, { method: 'PUT', keepalive: true, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': state.me?.csrf || '' }, body: JSON.stringify({ doc }) })
  } catch {}
}

function extensions({ collab, ydoc, provider }) {
  const s = state.settings.editor
  const list = [
    StarterKit.configure({
      heading: { levels: [1, 2, 3] },
      undoRedo: collab ? false : { depth: 200 },
      dropcursor: { color: 'var(--accent)', width: 2 },
      trailingNode: false,
      link: { openOnClick: false, autolink: true, protocols: ['http', 'https', 'mailto'], HTMLAttributes: { rel: 'noopener noreferrer nofollow', target: null } },
    }),
    TextStyle,
    Color,
    Highlight.configure({ multicolor: true }),
    TextAlign.configure({ types: ['heading', 'paragraph'] }),
    Placeholder.configure({ placeholder: () => (canEdit() ? 'Begin typing your story…  (Ctrl+K for commands)' : '') }),
    FigureImage,
    Video,
    Audio,
    FocusMode.configure({ enabled: () => state.settings.editor.focusMode }),
    CodexHighlight.configure({ entries: () => state.codex, enabled: () => state.settings.editor.codexHighlight }),
    FileDrop.configure({ onFiles: (files, pos) => uploadFiles(files, pos) }),
  ]
  if (s.smartTypography) list.push(Typography)
  if (collab) {
    list.push(Collaboration.configure({ document: ydoc }))
    list.push(CollaborationCaret.configure({ provider, user: { name: state.me.session.name, color: state.me.session.color } }))
  }
  return list
}

function destroyEditor() {
  if (ed.editor) {
    ed.editor.destroy()
    ed.editor = null
  }
  ed.provider?.destroy()
  ed.provider = null
  ed.ydoc?.destroy()
  ed.ydoc = null
  clear($('#editor'))
  $('#imageToolbar').hidden = true
}

export async function mountChapter(cid, { force = false } = {}) {
  if (!cid || !findChapter(cid)) return
  if (!force && ed.chapterId === cid && ed.editor && ed.collab === teamActive()) return
  if (ed.editor && !ed.collab) await flushSave()
  const token = Symbol('mount')
  ed.mounting = token
  destroyEditor()
  ed.chapterId = cid
  state.currentChapterId = cid
  ed.collab = teamActive()
  ed.dirty = false
  const title = $('#chapterTitle')
  title.value = findChapter(cid)?.title || ''
  title.readOnly = !canEdit()
  setSaveState('')
  emit('chapter:changed', cid)

  if (ed.collab) {
    const ydoc = new Y.Doc()
    let readonly = !canEdit()
    const provider = new ScribeProvider(ydoc, cid, {
      onInit: (msg) => { readonly = msg.readonly },
      onReset: () => ed.chapterId === cid && setTimeout(() => mountChapter(cid, { force: true }), 50),
      onReadonly: () => toast('Your role is read-only in this session'),
      onStatus: (st) => setSaveState(st === 'connected' ? 'Live' : 'Reconnecting…', st === 'connected' ? 'live' : 'warn'),
    })
    ed.ydoc = ydoc
    ed.provider = provider
    const synced = await new Promise((resolve) => {
      if (provider.synced) return resolve(true)
      provider.handlers.onSynced = () => resolve(true)
      setTimeout(() => resolve(false), 10000)
    })
    if (ed.mounting !== token) return
    if (!synced) {
      errorToast('Could not connect to the live session')
      return
    }
    createEditor({ collab: true, ydoc, provider, editable: !readonly })
  } else {
    let data
    try {
      data = await api.get(`/api/chapters/${cid}`)
    } catch (e) {
      errorToast(e)
      return
    }
    if (ed.mounting !== token) return
    let content = data.doc
    const pending = store(journalKey(cid))
    if (pending?.doc && canEdit() && (!data.updatedAt || pending.at > Date.parse(data.updatedAt))) {
      content = pending.doc
      ed.dirty = true
      toast('Recovered unsaved changes from your last session')
    }
    createEditor({ collab: false, content, editable: canEdit() })
    if (ed.dirty) saveSoon()
  }
  if (canEdit()) api.patch('/api/project', { activeChapterId: cid }).catch(() => {})
}

function createEditor({ collab, ydoc, provider, content, editable }) {
  const el = $('#editor')
  const editor = new Editor({
    element: el,
    extensions: extensions({ collab, ydoc, provider }),
    content: collab ? undefined : content,
    editable,
    editorProps: {
      attributes: { class: 'ProseMirror prose', spellcheck: String(state.settings.editor.spellcheck), 'aria-label': 'Chapter text', role: 'textbox', 'aria-multiline': 'true' },
      handleClick(view, pos, event) {
        const a = event.target.closest?.('a[href]')
        if (a && (event.ctrlKey || event.metaKey)) {
          openLink(a.getAttribute('href'))
          return true
        }
        return false
      },
    },
    onUpdate: ({ editor, transaction }) => {
      if (!collab && transaction.docChanged) {
        ed.dirty = true
        setSaveState('Unsaved', 'muted')
        store(journalKey(ed.chapterId), { doc: editor.getJSON(), at: Date.now() })
        saveSoon()
      }
      emit('editor:update', editor)
      typewriterScroll()
    },
    onSelectionUpdate: ({ editor }) => {
      emit('editor:selection', editor)
      updateImageToolbar(editor)
      typewriterScroll()
    },
    onCreate: ({ editor }) => emit('editor:update', editor),
  })
  ed.editor = editor
  emit('editor:ready', editor)
}

export function refreshDecorations() {
  const e = ed.editor
  if (!e) return
  e.view.dispatch(e.state.tr.setMeta(codexKey, 'refresh'))
}

export function editorText() {
  const e = ed.editor
  return e ? e.state.doc.textBetween(0, e.state.doc.content.size, '\n\n', ' ') : ''
}

export function editorWordCount() {
  return countWords(editorText())
}

export function openLink(href) {
  if (!/^(https?:|mailto:)/i.test(href || '')) return
  const d = desktop()
  if (d) d.open_external(href)
  else window.open(href, '_blank', 'noopener,noreferrer')
}

// ------------------------------------------------------------------ typewriter scrolling
function typewriterScroll() {
  if (!state.settings?.editor.typewriter || !ed.editor) return
  const view = ed.editor.view
  try {
    const coords = view.coordsAtPos(view.state.selection.head)
    const scroller = $('#editorScroll')
    const rect = scroller.getBoundingClientRect()
    const target = rect.top + rect.height / 2
    const delta = coords.top - target
    if (Math.abs(delta) > 24) scroller.scrollBy({ top: delta, behavior: state.settings.editor.reduceMotion ? 'auto' : 'smooth' })
  } catch {}
}

// ------------------------------------------------------------------ media upload
export async function uploadFiles(files, pos = null) {
  if (!canEdit()) return
  for (const file of files) {
    const t = toast(`Uploading ${file.name}… 0%`, { timeout: 0 })
    try {
      const info = await upload('/api/assets', file, (p) => (t.firstChild.textContent = `Uploading ${file.name}… ${Math.round(p * 100)}%`))
      const node = info.kind === 'image' ? { type: 'image', attrs: { src: info.src, alt: file.name.replace(/\.[^.]+$/, '') } } : { type: info.kind, attrs: { src: info.src } }
      const chain = ed.editor.chain().focus()
      if (pos != null) chain.insertContentAt(pos, node).run()
      else chain.insertContent(node).run()
    } catch (e) {
      errorToast(e)
    } finally {
      t.remove()
    }
  }
}

export function pickAndUpload(accept = 'image/*,video/*,audio/*') {
  const input = h('input', { type: 'file', accept, multiple: true, style: { display: 'none' } })
  input.onchange = () => {
    uploadFiles([...input.files])
    input.remove()
  }
  document.body.append(input)
  input.click()
}

// ------------------------------------------------------------------ image toolbar
function updateImageToolbar(editor) {
  const bar = $('#imageToolbar')
  const sel = editor.state.selection
  if (!(sel instanceof NodeSelection) || sel.node.type.name !== 'image' || !editor.isEditable) {
    bar.hidden = true
    return
  }
  const attrs = sel.node.attrs
  const set = (patch) => editor.chain().focus().updateAttributes('image', patch).run()
  clear(bar)
  const btn = (label, title, run, active) => h(`button.tb-btn${active ? '.active' : ''}`, { title, onmousedown: (e) => e.preventDefault(), onclick: run }, label)
  bar.append(
    ...[25, 50, 75, 100].map((w) => btn(`${w}%`, `Width ${w}%`, () => set({ width: w === 100 ? null : w }), (attrs.width || 100) === w)),
    h('span.tb-sep'),
    btn(icon('align-left'), 'Align left', () => set({ align: 'left' }), attrs.align === 'left'),
    btn(icon('align-center-horizontal'), 'Center', () => set({ align: 'center' }), attrs.align === 'center'),
    btn(icon('align-right'), 'Align right', () => set({ align: 'right' }), attrs.align === 'right'),
    btn(icon('arrows-out-line-horizontal'), 'Full width', () => set({ align: 'full', width: null }), attrs.align === 'full'),
    h('span.tb-sep'),
    btn(icon('closed-captioning'), 'Caption', async () => {
      const { promptDialog } = await import('../lib/ui.js')
      const v = await promptDialog('Caption', { value: attrs.caption || '', maxlength: 500 })
      if (v !== null) set({ caption: v.trim() || null })
    }),
    btn(icon('text-t'), 'Alt text (for screen readers)', async () => {
      const { promptDialog } = await import('../lib/ui.js')
      const v = await promptDialog('Alternative text', { value: attrs.alt || '', maxlength: 500 })
      if (v !== null) set({ alt: v.trim() || null })
    }),
    btn(icon('trash'), 'Remove image', () => editor.chain().focus().deleteSelection().run()),
  )
  bar.hidden = false
  const dom = editor.view.nodeDOM(sel.from)
  const r = (dom?.querySelector?.('img') || dom)?.getBoundingClientRect?.()
  if (r) {
    const ws = $('#workspace').getBoundingClientRect()
    bar.style.top = Math.max(ws.top + 56, r.top - 46) - ws.top + 'px'
    bar.style.left = Math.max(8, r.left - ws.left + r.width / 2 - bar.offsetWidth / 2) + 'px'
  }
}
