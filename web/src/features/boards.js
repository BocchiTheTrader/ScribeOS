// Corkboard (index cards) and statistics dashboard.

import { api } from '../lib/api.js'
import { clear, debounce, formatNumber, h, icon } from '../lib/dom.js'
import { canEdit, state } from '../lib/store.js'
import { errorToast, modal } from '../lib/ui.js'
import { mountChapter } from '../editor/editor.js'
import { STATUS, reloadProject, saveTree } from './sidebar.js'

export function openCorkboard() {
  const m = modal('Corkboard', { width: 1200, className: 'corkboard-modal', onClose: () => reloadProject() })
  const board = h('div.corkboard')
  m.body.append(h('p.muted.small', 'Each card is a chapter. Drag cards to reorder, edit synopses inline, click a title to open it.'), board)
  let dragId = null
  const patch = debounce((id, data) => api.patch(`/api/project/nodes/${id}`, data).catch(errorToast), 500)

  const card = (c, partId) => {
    const el = h('div.index-card', { draggable: canEdit(), dataset: { id: c.id }, style: c.label ? { borderTopColor: c.label } : {} },
      h('button.card-title', { onclick: () => { m.close(); mountChapter(c.id) } }, c.title),
      h('textarea.card-synopsis', { value: c.synopsis, placeholder: canEdit() ? 'Synopsis…' : '', readOnly: !canEdit(), maxlength: 5000, oninput: (e) => { c.synopsis = e.target.value; patch(c.id, { synopsis: c.synopsis }) } }),
      h('div.card-foot',
        h('select.input.small', { disabled: !canEdit(), onchange: (e) => { c.status = e.target.value; patch(c.id, { status: c.status }); el.querySelector('.status-dot').className = `status-dot status-${c.status}` } }, Object.entries(STATUS).map(([k, l]) => h('option', { value: k, selected: c.status === k }, l))),
        h('span.status-dot', { class: `status-${c.status}` }),
        h('small.muted', `${formatNumber(c.wordCount)} w`)))
    el.addEventListener('dragstart', () => { dragId = c.id; el.classList.add('dragging') })
    el.addEventListener('dragend', () => { dragId = null; el.classList.remove('dragging') })
    el.addEventListener('dragover', (e) => { if (dragId && dragId !== c.id) { e.preventDefault(); el.classList.add('drop-target') } })
    el.addEventListener('dragleave', () => el.classList.remove('drop-target'))
    el.addEventListener('drop', async (e) => {
      e.preventDefault()
      el.classList.remove('drop-target')
      if (!dragId || dragId === c.id) return
      const tree = state.project.tree.map((n) => (n.type === 'part' ? { ...n, children: [...n.children] } : n))
      let moving = null
      for (const n of tree) {
        if (n.id === dragId) moving = tree.splice(tree.indexOf(n), 1)[0]
        else if (n.type === 'part') {
          const i = n.children.findIndex((x) => x.id === dragId)
          if (i >= 0) moving = n.children.splice(i, 1)[0]
        }
        if (moving) break
      }
      const container = partId ? tree.find((n) => n.id === partId).children : tree
      container.splice(container.findIndex((x) => x.id === c.id), 0, moving)
      await saveTree(tree)
      render()
    })
    return el
  }

  const render = () => {
    clear(board)
    let loose = h('div.card-row')
    for (const n of state.project.tree) {
      if (n.type === 'part') {
        if (loose.childElementCount) board.append(loose)
        loose = h('div.card-row')
        board.append(h('h4.part-heading', icon('folder-simple'), n.title), h('div.card-row', n.children.map((c) => card(c, n.id))))
      } else loose.append(card(n, null))
    }
    if (loose.childElementCount) board.append(loose)
  }
  render()
}

export async function openStats() {
  const m = modal('Writing statistics', { width: 760 })
  let s
  try {
    s = await api.get('/api/stats')
  } catch (e) {
    return errorToast(e)
  }
  const tile = (label, value, sub) => h('div.stat-tile', h('span.stat-value', value), h('span.stat-label', label), sub ? h('small.muted', sub) : null)
  m.body.append(h('div.stat-tiles',
    tile('Today', formatNumber(s.today.added), s.goal ? `goal ${formatNumber(s.goal)}` : null),
    tile('Day streak', formatNumber(s.streak), s.goal ? 'days meeting the goal' : 'days writing'),
    tile('Last 7 days', formatNumber(s.week)),
    tile('Best day', formatNumber(s.best))))

  // Heatmap: 53 columns (weeks) × 7 rows (days).
  const max = Math.max(1, ...s.series.map((d) => d.added))
  const grid = h('div.heatmap', { role: 'img', 'aria-label': 'Words written per day over the last year' })
  const first = new Date(s.series[0].date)
  const pad = (first.getDay() + 6) % 7
  for (let i = 0; i < pad; i++) grid.append(h('span.hm-cell.empty'))
  for (const d of s.series) {
    const level = d.added === 0 ? 0 : Math.min(4, Math.ceil((d.added / max) * 4))
    grid.append(h(`span.hm-cell.l${level}`, { title: `${d.date}: ${formatNumber(d.added)} words` }))
  }
  m.body.append(h('h4', 'Last 12 months'), grid)
  if (s.project) {
    const p = s.project
    const pct = p.target ? Math.min(100, (p.words / p.target) * 100) : 0
    m.body.append(h('h4', `Project: ${state.project.name}`),
      h('p', `${formatNumber(p.words)} words`, p.target ? ` of ${formatNumber(p.target)} (${pct.toFixed(0)}%)` : ''),
      p.target ? h('div.goal-bar.big', h('div', { style: { width: pct + '%' } })) : null,
      p.perDay != null ? h('p.muted', p.daysLeft > 0 ? `Write ${formatNumber(p.perDay)} words a day to finish by ${p.deadline}.` : 'The deadline has passed.') : null)
  }
  if (canEdit()) {
    const t = state.project.targets || {}
    const words = h('input.input.narrow', { type: 'number', min: 0, step: 1000, value: t.projectWords || '' })
    const date = h('input.input', { type: 'date', value: t.deadline || '' })
    m.body.append(h('div.target-form', h('label.field.inline', 'Word target', words), h('label.field.inline', 'Deadline', date),
      h('button.btn.small', { onclick: async () => {
        try {
          await api.patch('/api/project', { targets: { projectWords: parseInt(words.value, 10) || 0, deadline: date.value || null } })
          await reloadProject()
          m.close()
          openStats()
        } catch (e) {
          errorToast(e)
        }
      } }, 'Save target')))
  }
}
