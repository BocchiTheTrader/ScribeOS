// Export ("compile") dialog: PDF, DOCX, EPUB, Markdown, HTML, TXT, project archive.

import { api, desktop } from '../lib/api.js'
import { clear, h, icon, store } from '../lib/dom.js'
import { canEdit, chapters, state } from '../lib/store.js'
import { errorToast, modal, toast } from '../lib/ui.js'
import { ed } from '../editor/editor.js'

const FORMATS = [
  { id: 'pdf', label: 'PDF', icon: 'file-pdf', desc: 'Print-ready, vector text' },
  { id: 'docx', label: 'Word', icon: 'file-doc', desc: 'For editors & agents' },
  { id: 'epub', label: 'EPUB', icon: 'book', desc: 'E-readers' },
  { id: 'md', label: 'Markdown', icon: 'markdown-logo', desc: 'Plain text + images' },
  { id: 'html', label: 'Web page', icon: 'file-html', desc: 'Single self-contained file' },
  { id: 'txt', label: 'Plain text', icon: 'file-text', desc: 'Words only' },
  { id: 'scribez', label: 'Project archive', icon: 'archive', desc: 'Full backup (.scribez)' },
]
const PRESETS = [
  ['paperback', 'Paperback 6×9 in'],
  ['manuscript', 'Standard manuscript (Courier, double-spaced)'],
  ['a4', 'A4'],
  ['a5', 'A5'],
  ['manga', 'Manga / comic script'],
]

export function openExport() {
  if (!canEdit() || !state.project) return
  const saved = store('scribeos:export') || {}
  const opts = {
    format: 'pdf', scope: 'project', preset: 'paperback', titlePage: true, toc: true, chapterNumbers: true, pageNumbers: true, includeImages: true, sceneBreak: '* * *',
    ...saved, chapterIds: [], currentChapterId: ed.chapterId, title: state.project.name, author: state.project.author || state.settings.authorName || '',
  }
  const m = modal('Export', { width: 760, className: 'export-modal' })
  const formatGrid = h('div.format-grid')
  const options = h('div.export-options')
  const preview = h('div.export-preview')
  const exportBtn = h('button.btn.primary', { onclick: run }, icon('export'), 'Export…')
  m.body.append(formatGrid, h('div.export-layout', options, preview), h('div.dialog-actions', h('button.btn', { onclick: () => m.close() }, 'Cancel'), exportBtn))

  const renderFormats = () => {
    clear(formatGrid)
    for (const f of FORMATS) formatGrid.append(h(`button.format-card${opts.format === f.id ? '.active' : ''}`, { onclick: () => { opts.format = f.id; renderFormats(); renderOptions() } }, icon(f.icon), h('strong', f.label), h('small', f.desc)))
  }
  const check = (key, label) => h('label.check', h('input', { type: 'checkbox', checked: opts[key], onchange: (e) => (opts[key] = e.target.checked) }), label)

  const renderOptions = () => {
    clear(options)
    clear(preview)
    if (opts.format === 'scribez') {
      options.append(h('p.muted', 'Saves the whole project folder — chapters, notes, codex, images, snapshots — as one .scribez file. Open it later with Project → Open archive.'))
      return
    }
    const chapterList = h('div.chapter-checklist', { hidden: opts.scope !== 'chapters' }, chapters().map((c) => h('label.check', h('input', { type: 'checkbox', checked: opts.chapterIds.includes(c.id), onchange: (e) => {
      opts.chapterIds = e.target.checked ? [...opts.chapterIds, c.id] : opts.chapterIds.filter((x) => x !== c.id)
    } }), c.title)))
    const scope = h('div.radio-row', [['project', 'Whole project'], ['current', 'Current chapter'], ['chapters', 'Choose chapters']].map(([k, l]) => h('label.check', h('input', { type: 'radio', name: 'scope', checked: opts.scope === k, onchange: () => { opts.scope = k; chapterList.hidden = k !== 'chapters' } }), l)))
    options.append(
      h('label.field', 'Title', h('input.input', { value: opts.title, maxlength: 300, oninput: (e) => (opts.title = e.target.value) })),
      h('label.field', 'Author', h('input.input', { value: opts.author, maxlength: 200, oninput: (e) => (opts.author = e.target.value) })),
      h('div.field', h('span', 'Include'), scope, chapterList),
    )
    if (['pdf', 'docx'].includes(opts.format)) {
      options.append(h('label.field', 'Layout', h('select.input', { onchange: (e) => (opts.preset = e.target.value) }, PRESETS.map(([k, l]) => h('option', { value: k, selected: opts.preset === k }, l)))))
    }
    options.append(h('div.checks', check('titlePage', 'Title page'), check('toc', 'Table of contents'), check('chapterNumbers', 'Number chapters'), ['pdf', 'docx'].includes(opts.format) ? check('pageNumbers', 'Page numbers') : null, check('includeImages', 'Include images')),
      h('label.field', 'Scene break symbol', h('input.input.narrow', { value: opts.sceneBreak, maxlength: 20, oninput: (e) => (opts.sceneBreak = e.target.value || '* * *') })))
    if (opts.format === 'pdf') {
      preview.append(h('button.btn.ghost', { onclick: async (e) => {
        const btn = e.currentTarget
        btn.disabled = true
        btn.textContent = 'Rendering preview…'
        try {
          const { pages } = await api.post('/api/export/preview', payload())
          clear(preview)
          preview.append(...pages.map((src) => h('img.preview-page', { src, alt: 'Page preview' })))
        } catch (err) {
          errorToast(err)
          btn.disabled = false
          btn.textContent = 'Preview'
        }
      } }, icon('eye'), 'Preview first pages'))
    }
  }

  const payload = () => {
    const p = { ...opts }
    if (!p.title?.trim()) p.title = null
    return p
  }

  async function run() {
    const { chapterIds, currentChapterId, title, author, ...remember } = opts
    store('scribeos:export', remember)
    if (opts.scope === 'chapters' && !opts.chapterIds.length) return toast('Choose at least one chapter', { type: 'error' })
    exportBtn.disabled = true
    exportBtn.textContent = 'Exporting…'
    try {
      const d = desktop()
      if (d) {
        const res = await d.export_to_file(JSON.stringify(payload()))
        if (res.ok) {
          toast(`Saved to ${res.path}`, { timeout: 6000 })
          m.close()
        } else if (!res.cancelled) errorToast(new Error(res.error || 'Export failed'))
      } else {
        const res = await api.download('/api/export', payload())
        const blob = await res.blob()
        const cd = res.headers.get('content-disposition') || ''
        const name = decodeURIComponent((cd.match(/filename\*=UTF-8''([^;]+)/) || [])[1] || 'export')
        const url = URL.createObjectURL(blob)
        const a = h('a', { href: url, download: name })
        document.body.append(a)
        a.click()
        a.remove()
        setTimeout(() => URL.revokeObjectURL(url), 10000)
        m.close()
      }
    } catch (e) {
      errorToast(e)
    } finally {
      exportBtn.disabled = false
      exportBtn.textContent = 'Export…'
    }
  }

  renderFormats()
  renderOptions()
}
