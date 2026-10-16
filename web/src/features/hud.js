// Status bar: word/char counts, daily goal, writing sprints.

import { api } from '../lib/api.js'
import { $, countWords, debounce, formatNumber } from '../lib/dom.js'
import { chapters, isOwner, on, state } from '../lib/store.js'
import { promptDialog, toast } from '../lib/ui.js'
import { editorText } from '../editor/editor.js'

let today = { added: 0 }
let sprint = null

export function initHud() {
  const update = debounce(updateCounts, 150)
  on('editor:update', update)
  on('words:changed', () => {
    updateCounts()
    refreshGoal()
  })
  on('project:loaded', updateCounts)
  $('#btnSprint').onclick = toggleSprint
  refreshGoal()
  setInterval(refreshGoal, 60000)
}

function updateCounts() {
  const text = editorText()
  const chapterWords = countWords(text)
  const total = chapters().reduce((s, c) => s + (c.wordCount || 0), 0)
  $('#hudWords').textContent = `${formatNumber(chapterWords)} / ${formatNumber(total)}`
  $('#hudChars').textContent = formatNumber(text.length)
  $('#hudPages').textContent = formatNumber(Math.max(1, Math.round(total / 250)))
  $('#hudRead').textContent = formatNumber(Math.ceil(total / 230))
  if (sprint) sprint.words = Math.max(0, total - sprint.startTotal)
}

export async function refreshGoal() {
  if (!isOwner()) return
  try {
    const s = await api.get('/api/stats')
    today = s.today
  } catch {
    return
  }
  const goal = state.settings.editor.dailyGoal || 0
  $('#hudGoal').textContent = `${formatNumber(today.added)} / ${formatNumber(goal)}`
  const pct = goal ? Math.min(100, (today.added / goal) * 100) : 0
  const fill = $('#goalFill')
  fill.style.width = pct + '%'
  fill.classList.toggle('done', pct >= 100)
}

async function toggleSprint() {
  if (sprint) {
    endSprint(true)
    return
  }
  const v = await promptDialog('Sprint length in minutes', { value: String(state.settings.editor.sprintMinutes), type: 'number', title: 'Writing sprint', ok: 'Start' })
  const minutes = Math.min(240, Math.max(1, parseInt(v, 10) || 0))
  if (!v || !minutes) return
  const total = chapters().reduce((s, c) => s + (c.wordCount || 0), 0)
  sprint = { end: Date.now() + minutes * 60000, startTotal: total, words: 0, minutes }
  sprint.timer = setInterval(tick, 1000)
  $('#btnSprint').classList.add('active')
  tick()
}

function tick() {
  if (!sprint) return
  const left = Math.max(0, sprint.end - Date.now())
  const m = Math.floor(left / 60000)
  const s = Math.floor((left % 60000) / 1000)
  $('#sprintLabel').textContent = `${m}:${String(s).padStart(2, '0')} · ${sprint.words}w`
  if (left <= 0) endSprint(false)
}

function endSprint(cancelled) {
  clearInterval(sprint.timer)
  const { words, minutes } = sprint
  sprint = null
  $('#btnSprint').classList.remove('active')
  $('#sprintLabel').textContent = 'Sprint'
  if (!cancelled) toast(`Sprint finished: ${formatNumber(words)} words in ${minutes} min 🎉`, { timeout: 8000 })
}
