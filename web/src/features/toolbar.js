import { $, clear, h, icon } from '../lib/dom.js'
import { canEdit, on, state } from '../lib/store.js'
import { popover, promptDialog, toast } from '../lib/ui.js'
import { ed, pickAndUpload } from '../editor/editor.js'
import { TEMPLATES } from '../editor/templates.js'
import { modal } from '../lib/ui.js'

const HIGHLIGHTS = [
  { label: 'Yellow', color: '#fef08a' },
  { label: 'Green', color: '#bbf7d0' },
  { label: 'Blue', color: '#bfdbfe' },
  { label: 'Pink', color: '#fbcfe8' },
  { label: 'Orange', color: '#fed7aa' },
]

const chain = () => ed.editor?.chain().focus()

const BUTTONS = [
  { id: 'undo', icon: 'arrow-counter-clockwise', title: 'Undo (Ctrl+Z)', run: () => chain()?.undo().run() },
  { id: 'redo', icon: 'arrow-clockwise', title: 'Redo (Ctrl+Y)', run: () => chain()?.redo().run() },
  '|',
  { id: 'bold', icon: 'text-b', title: 'Bold (Ctrl+B)', run: () => chain()?.toggleBold().run(), active: (e) => e.isActive('bold') },
  { id: 'italic', icon: 'text-italic', title: 'Italic (Ctrl+I)', run: () => chain()?.toggleItalic().run(), active: (e) => e.isActive('italic') },
  { id: 'underline', icon: 'text-underline', title: 'Underline (Ctrl+U)', run: () => chain()?.toggleUnderline().run(), active: (e) => e.isActive('underline') },
  { id: 'strike', icon: 'text-strikethrough', title: 'Strikethrough', run: () => chain()?.toggleStrike().run(), active: (e) => e.isActive('strike') },
  '|',
  { id: 'h1', icon: 'text-h-one', title: 'Heading 1', run: () => chain()?.toggleHeading({ level: 1 }).run(), active: (e) => e.isActive('heading', { level: 1 }) },
  { id: 'h2', icon: 'text-h-two', title: 'Heading 2', run: () => chain()?.toggleHeading({ level: 2 }).run(), active: (e) => e.isActive('heading', { level: 2 }) },
  { id: 'h3', icon: 'text-h-three', title: 'Heading 3', run: () => chain()?.toggleHeading({ level: 3 }).run(), active: (e) => e.isActive('heading', { level: 3 }) },
  { id: 'quote', icon: 'quotes', title: 'Quote', run: () => chain()?.toggleBlockquote().run(), active: (e) => e.isActive('blockquote') },
  { id: 'ul', icon: 'list-bullets', title: 'Bulleted list', run: () => chain()?.toggleBulletList().run(), active: (e) => e.isActive('bulletList') },
  { id: 'ol', icon: 'list-numbers', title: 'Numbered list', run: () => chain()?.toggleOrderedList().run(), active: (e) => e.isActive('orderedList') },
  '|',
  { id: 'align', icon: 'text-align-left', title: 'Alignment', menu: true, run: (btn) => popover(btn, [
    { label: 'Left', icon: 'text-align-left', run: () => chain()?.setTextAlign('left').run() },
    { label: 'Center', icon: 'text-align-center', run: () => chain()?.setTextAlign('center').run() },
    { label: 'Right', icon: 'text-align-right', run: () => chain()?.setTextAlign('right').run() },
    { label: 'Justify', icon: 'text-align-justify', run: () => chain()?.setTextAlign('justify').run() },
  ]) },
  { id: 'color', icon: 'text-aa', title: 'Text colour', color: true },
  { id: 'highlight', icon: 'highlighter', title: 'Highlight', menu: true, run: (btn) => popover(btn, [
    ...HIGHLIGHTS.map((c) => ({ element: h('button.popover-item', { onclick: () => { chain()?.toggleHighlight({ color: c.color }).run(); btn.blur(); document.querySelector('.popover')?.remove() } }, h('span.swatch', { style: { background: c.color } }), c.label) })),
    { separator: true },
    { label: 'Remove highlight', icon: 'eraser', run: () => chain()?.unsetHighlight().run() },
  ]), active: (e) => e.isActive('highlight') },
  { id: 'link', icon: 'link', title: 'Link (Ctrl+click to open)', run: async () => {
    const prev = ed.editor?.getAttributes('link').href || ''
    const url = await promptDialog('Link address (https://…)', { value: prev, maxlength: 2000 })
    if (url === null) return
    if (!url.trim()) return chain()?.extendMarkRange('link').unsetLink().run()
    if (!/^(https?:\/\/|mailto:)/i.test(url.trim())) return toast('Only http(s) and mailto links are allowed', { type: 'error' })
    chain()?.extendMarkRange('link').setLink({ href: url.trim() }).run()
  }, active: (e) => e.isActive('link') },
  { id: 'clear', icon: 'eraser', title: 'Clear formatting', run: () => chain()?.unsetAllMarks().clearNodes().run() },
  '|',
  { id: 'image', icon: 'image', title: 'Insert image or GIF (or paste / drag-and-drop)', run: () => pickAndUpload('image/*') },
  { id: 'media', icon: 'film-strip', title: 'Insert video or audio', run: () => pickAndUpload('video/*,audio/*') },
  { id: 'hr', icon: 'minus', title: 'Scene break', run: () => chain()?.setHorizontalRule().run() },
]

const buttons = []

export function initToolbar() {
  const host = $('#formatTools')
  clear(host)
  for (const b of BUTTONS) {
    if (b === '|') {
      host.append(h('div.divider'))
      continue
    }
    if (b.color) {
      const input = h('input', { type: 'color', value: '#f59e0b', 'aria-label': 'Text colour', oninput: (e) => chain()?.setColor(e.target.value).run() })
      const label = h('label.icon-btn.color-btn', { title: b.title }, icon(b.icon), input)
      host.append(label)
      buttons.push({ def: b, el: label })
      continue
    }
    const el = h('button.icon-btn', { title: b.title, 'aria-label': b.title, onmousedown: (e) => e.preventDefault(), onclick: (e) => b.run(e.currentTarget) }, icon(b.icon))
    host.append(el)
    buttons.push({ def: b, el })
  }
  on('editor:selection', refresh)
  on('editor:update', refresh)
  on('editor:ready', refresh)

  $('#btnTemplates').onclick = openTemplates
}

function refresh(editor) {
  const editable = canEdit() && editor?.isEditable
  for (const { def, el } of buttons) {
    if (el.tagName === 'BUTTON') el.disabled = !editable
    else el.classList.toggle('disabled', !editable)
    if (def.active) el.classList.toggle('active', !!editor && def.active(editor))
  }
}

export function openTemplates() {
  if (!canEdit()) return
  const m = modal('Narrative frameworks & templates', { width: 640 })
  const groups = [...new Set(TEMPLATES.map((t) => t.group))]
  for (const g of groups) {
    m.body.append(h('div.template-group', g))
    m.body.append(h('div.template-grid', TEMPLATES.filter((t) => t.group === g).map((t) => h('div.template-card',
      h('span.template-name', t.name),
      h('span.template-actions',
        h('button.btn.small', { onclick: () => { ed.editor?.chain().focus().insertContent(t.html).run(); m.close() } }, 'Insert here'),
        h('button.btn.small.ghost', { onclick: async () => {
          const { newChapter } = await import('./sidebar.js')
          await newChapter(t.name, t.html)
          m.close()
        } }, 'As new chapter'))))))
  }
  void state
}
