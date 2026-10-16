import { Extension, Node, mergeAttributes } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

const ASSET_RE = /^\/api\/assets\/[0-9a-f]{64}\.[a-z0-9]{2,4}$/

/** Block image with width (%), alignment and caption. Works for PNG/JPEG/WebP/AVIF/SVG and animated GIFs. */
export const FigureImage = Node.create({
  name: 'image',
  group: 'block',
  atom: true,
  draggable: true,
  selectable: true,
  addAttributes() {
    return {
      src: { default: null },
      alt: { default: null },
      title: { default: null },
      width: { default: null },
      align: { default: 'center' },
      caption: { default: null },
    }
  },
  parseHTML() {
    return [
      {
        tag: 'figure',
        getAttrs: (el) => {
          const img = el.querySelector('img')
          if (!img) return false
          const w = parseInt(img.style.width, 10)
          return { src: img.getAttribute('src'), alt: img.getAttribute('alt'), width: Number.isFinite(w) ? w : null, align: el.dataset.align || 'center', caption: el.querySelector('figcaption')?.textContent || null }
        },
      },
      { tag: 'img[src]', getAttrs: (el) => ({ src: el.getAttribute('src'), alt: el.getAttribute('alt') }) },
    ]
  },
  renderHTML({ node }) {
    const { src, alt, title, width, align, caption } = node.attrs
    const safe = ASSET_RE.test(src || '') ? src : ''
    const img = ['img', { src: safe, alt: alt || '', title: title || null, draggable: 'false', loading: 'lazy', style: width ? `width:${width}%` : null }]
    const children = [img]
    if (caption) children.push(['figcaption', {}, caption])
    return ['figure', { class: `img-figure align-${align || 'center'}`, 'data-align': align || 'center' }, ...children]
  },
})

const mediaNode = (name, tag) =>
  Node.create({
    name,
    group: 'block',
    atom: true,
    draggable: true,
    addAttributes() {
      return { src: { default: null } }
    },
    parseHTML() {
      return [{ tag: `${tag}[src]`, getAttrs: (el) => ({ src: el.getAttribute('src') }) }]
    },
    renderHTML({ node }) {
      const safe = ASSET_RE.test(node.attrs.src || '') ? node.attrs.src : ''
      return ['div', { class: `media-block media-${name}` }, [tag, mergeAttributes({ src: safe, controls: 'true', preload: 'metadata' })]]
    },
  })

export const Video = mediaNode('video', 'video')
export const Audio = mediaNode('audio', 'audio')

/** Dims every top-level block except the one containing the cursor. */
export const FocusMode = Extension.create({
  name: 'focusMode',
  addOptions() {
    return { enabled: () => false }
  },
  addProseMirrorPlugins() {
    const opts = this.options
    return [
      new Plugin({
        key: new PluginKey('focusMode'),
        props: {
          decorations(state) {
            if (!opts.enabled()) return null
            const { $from } = state.selection
            if ($from.depth < 1) return null
            const start = $from.before(1)
            const node = state.doc.nodeAt(start)
            if (!node) return null
            return DecorationSet.create(state.doc, [Decoration.node(start, start + node.nodeSize, { class: 'is-focused' })])
          },
        },
      }),
    ]
  },
})

export const codexKey = new PluginKey('codexHighlight')

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function buildCodexDecorations(doc, entries) {
  if (!entries.length) return DecorationSet.empty
  const names = []
  const byName = new Map()
  for (const e of entries) {
    for (const n of [e.name, ...(e.aliases || [])]) {
      const k = n.trim()
      if (k.length < 2) continue
      names.push(k)
      byName.set(k.toLowerCase(), e)
    }
  }
  if (!names.length) return DecorationSet.empty
  names.sort((a, b) => b.length - a.length)
  const re = new RegExp(`(?<![\\p{L}\\p{N}])(${names.map(escapeRe).join('|')})(?![\\p{L}\\p{N}])`, 'giu')
  const decos = []
  doc.descendants((node, pos) => {
    if (!node.isText) return
    re.lastIndex = 0
    let m
    while ((m = re.exec(node.text)) && decos.length < 5000) {
      const entry = byName.get(m[1].toLowerCase())
      if (!entry) continue
      decos.push(Decoration.inline(pos + m.index, pos + m.index + m[1].length, { class: `codex-mention codex-${entry.kind}`, 'data-codex-id': entry.id, style: `--codex-color:${/^#[0-9a-f]{6}$/i.test(entry.color) ? entry.color : '#f59e0b'}` }))
    }
  })
  return DecorationSet.create(doc, decos)
}

/** Underlines mentions of Codex entries (characters, places…). */
export const CodexHighlight = Extension.create({
  name: 'codexHighlight',
  addOptions() {
    return { entries: () => [], enabled: () => true }
  },
  addProseMirrorPlugins() {
    const opts = this.options
    return [
      new Plugin({
        key: codexKey,
        state: {
          init: (_, state) => (opts.enabled() ? buildCodexDecorations(state.doc, opts.entries()) : DecorationSet.empty),
          apply(tr, old, _o, newState) {
            if (tr.getMeta(codexKey) === 'refresh' || tr.docChanged) {
              return opts.enabled() ? buildCodexDecorations(newState.doc, opts.entries()) : DecorationSet.empty
            }
            return old
          },
        },
        props: {
          decorations(state) {
            return codexKey.getState(state)
          },
        },
      }),
    ]
  },
})

/** Routes pasted/dropped files to an uploader instead of embedding base64 data. */
export const FileDrop = Extension.create({
  name: 'fileDrop',
  addOptions() {
    return { onFiles: null }
  },
  addProseMirrorPlugins() {
    const opts = this.options
    const handle = (view, files, pos) => {
      const media = [...files].filter((f) => /^(image|video|audio)\//.test(f.type))
      if (!media.length || !opts.onFiles) return false
      opts.onFiles(media, pos)
      return true
    }
    return [
      new Plugin({
        key: new PluginKey('fileDrop'),
        props: {
          handlePaste(view, event) {
            const files = event.clipboardData?.files
            return files?.length ? handle(view, files, null) : false
          },
          handleDrop(view, event) {
            const files = event.dataTransfer?.files
            if (!files?.length) return false
            event.preventDefault()
            const pos = view.posAtCoords({ left: event.clientX, top: event.clientY })?.pos ?? null
            return handle(view, files, pos)
          },
        },
      }),
    ]
  },
})
