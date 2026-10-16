import '@phosphor-icons/web/regular'
import '@phosphor-icons/web/fill'
import './styles/base.css'
import './styles/themes.css'
import './styles/app.css'
import './styles/editor.css'

import { api, setCsrf, waitForDesktop } from './lib/api.js'
import { $, $$, h } from './lib/dom.js'
import { connectEvents } from './lib/events.js'
import { canEdit, emit, findChapter, isOwner, on, state } from './lib/store.js'
import { closePanel, closePopovers, toast } from './lib/ui.js'
import { ed, flushSave, mountChapter, refreshDecorations, saveOnUnload } from './editor/editor.js'
import { openCorkboard, openStats } from './features/boards.js'
import { loadCustomFonts } from './features/fonts.js'
import { initHud, refreshGoal } from './features/hud.js'
import { initNotes, loadNotes, toggleNotes } from './features/notes.js'
import { openPalette, registerCommands } from './features/palette.js'
import { initCodexHover, loadCodex, openAssets, openCodex, openSearch, openSnapshots, openTrash } from './features/panels.js'
import { importDocument, newProject, openProject, projectMenu, saveProjectAs, showWelcome } from './features/project.js'
import { cycleTheme, openSettings, saveSettings, setTheme, themePopover } from './features/settings.js'
import { initSidebar, newChapter, reloadProject, renderTree } from './features/sidebar.js'
import { initTeam, openTeamPanel, refreshMe, updateBadge } from './features/team.js'
import { THEMES, applyAppearance } from './features/themes.js'
import { initToolbar, openTemplates } from './features/toolbar.js'
import { openExport } from './features/exporter.js'
import { initVoice, readAloud, toggleDictation } from './features/voice.js'

async function boot() {
  let me
  try {
    me = await api.get('/api/me')
  } catch {
    $('#bootMessage').textContent = 'Cannot reach ScribeOS. Is the app still running?'
    return
  }
  if (!me.signedIn) {
    location.replace('/join')
    return
  }
  state.me = me
  setCsrf(me.csrf)
  document.body.classList.toggle('is-guest', !isOwner())
  document.body.classList.toggle('is-readonly', !canEdit())
  if (me.desktop) state.desktop = await waitForDesktop()
  state.settings = await api.get('/api/settings')
  applyAppearance()
  loadCustomFonts()
  connectEvents()
  wireServerEvents()

  if (!me.projectOpen) {
    if (isOwner()) showWelcome()
    else $('#bootMessage').textContent = 'The host has no project open right now. This page will refresh when they open one.'
    return
  }
  state.project = await api.get('/api/project')
  $('#app').hidden = false
  $('#bootMessage').hidden = true
  if (!canEdit()) {
    const banner = $('#readonlyBanner')
    banner.hidden = false
    banner.textContent = me.role === 'commenter' ? 'You can read, chat and use the scratchpad. Ask the host for editor access to write.' : 'You are viewing this project in read-only mode.'
  }
  for (const el of $$('.owner-only')) el.hidden = !isOwner()

  initToolbar()
  initSidebar()
  initHud()
  initNotes()
  initTeam()
  initVoice()
  initCodexHover()
  wireButtons()
  registerCommands(commandList())
  updateBadge()

  emit('project:loaded', state.project)
  await loadCodex()
  const start = findChapter(state.project.activeChapterId) ? state.project.activeChapterId : state.project.tree.find((n) => n.type === 'chapter')?.id || state.project.tree.flatMap((n) => n.children || [])[0]?.id
  await mountChapter(start)
  document.title = `${state.project.name} · ScribeOS`

  on('team:changed', async () => {
    updateBadge()
    if (ed.chapterId) await mountChapter(ed.chapterId, { force: true })
    emit('team:refresh')
  })
  on('editor:remount', () => ed.chapterId && mountChapter(ed.chapterId, { force: true }))
  on('editor:redraw', () => ed.editor && (refreshDecorations(), ed.editor.view.dispatch(ed.editor.state.tr)))
  window.addEventListener('beforeunload', saveOnUnload)
  document.addEventListener('visibilitychange', () => document.visibilityState === 'hidden' && flushSave())
}

function wireServerEvents() {
  on('server:project-changed', () => location.reload())
  on('server:tree-changed', async () => {
    if (!state.project) return
    await reloadProject()
    if (ed.chapterId && !findChapter(ed.chapterId)) mountChapter(state.project.activeChapterId, { force: true })
    else if (ed.chapterId) $('#chapterTitle').value = findChapter(ed.chapterId).title
  })
  on('server:chapter-saved', (ev) => emit('chapter:saved', ev))
  on('server:chapter-replaced', (ev) => ev.id === ed.chapterId && !ed.collab && mountChapter(ev.id, { force: true }))
  on('server:notes-changed', () => state.project && loadNotes())
  on('server:codex-changed', () => state.project && loadCodex())
  on('server:settings-changed', async () => {
    state.settings = await api.get('/api/settings')
    applyAppearance()
    refreshGoal()
  })
  on('server:team-changed', () => refreshMe())
  on('server:toast', (ev) => toast(ev.message))
  on('server:role-changed', () => {
    toast('Your role changed — reloading…')
    setTimeout(() => location.reload(), 800)
  })
  const signedOut = (msg) => {
    document.body.replaceChildren(h('div.boot-message', msg, h('br'), h('a', { href: '/join' }, 'Rejoin')))
  }
  on('server:kicked', () => signedOut('You were disconnected from the session by the host.'))
  on('server:signed-out', () => signedOut('Your session has ended.'))
  on('connection', (up) => document.body.classList.toggle('offline', !up))
}

function toggleZen() {
  document.body.classList.toggle('zen')
  closePanel()
  state.desktop?.toggle_fullscreen?.()
}

function wireButtons() {
  $('#btnProjectMenu').onclick = (e) => projectMenu(e.currentTarget)
  $('#btnSettings').onclick = () => openSettings()
  $('#btnCodex').onclick = () => openCodex()
  $('#btnCorkboard').onclick = openCorkboard
  $('#btnStats').onclick = openStats
  $('#btnTheme').onclick = (e) => themePopover(e.currentTarget)
  $('#btnFocus').onclick = () => saveSettings({ editor: { focusMode: !state.settings.editor.focusMode } }).then(() => emit('editor:redraw'))
  $('#btnTypewriter').onclick = () => saveSettings({ editor: { typewriter: !state.settings.editor.typewriter } })
  $('#btnZen').onclick = toggleZen
  $('#btnPalette').onclick = openPalette
  $('#btnSnapshot').onclick = openSnapshots
  $('#btnSearch').onclick = openSearch
  $('#btnExport').onclick = openExport
  $('#btnExport').hidden = !canEdit()

  document.addEventListener('keydown', (e) => {
    const mod = e.ctrlKey || e.metaKey
    const k = e.key.toLowerCase()
    const run = (fn) => { e.preventDefault(); fn() }
    if (mod && !e.shiftKey && k === 'k') return run(openPalette)
    if (mod && !e.shiftKey && k === 'n') return run(() => newChapter())
    if (mod && !e.shiftKey && k === 's') return run(() => flushSave().then(() => toast('Saved', { timeout: 1000 })))
    if (mod && e.shiftKey && k === 's' && isOwner()) return run(saveProjectAs)
    if (mod && !e.shiftKey && k === 'o' && isOwner()) return run(() => openProject())
    if (mod && e.shiftKey && k === 'f') return run(openSearch)
    if (mod && e.shiftKey && k === 't') return run(cycleTheme)
    if (mod && e.shiftKey && k === 'y') return run(() => $('#btnTypewriter').click())
    if (mod && e.shiftKey && k === 'p') return run(openSnapshots)
    if (mod && e.shiftKey && k === 'u') return run(() => openTeamPanel())
    if (mod && e.shiftKey && e.code === 'Space') return run(toggleDictation)
    if (mod && !e.shiftKey && k === 'e') return run(openExport)
    if (mod && e.key === ',') return run(() => openSettings())
    if (e.altKey && k === 'n') return run(toggleNotes)
    if (e.key === 'F11') return run(toggleZen)
    if (e.key === 'Escape' && !document.querySelector('.modal-overlay')) {
      closePopovers()
      if (!$('#sidePanel').hidden) closePanel()
      else if (document.body.classList.contains('zen')) toggleZen()
    }
  })
}

function commandList() {
  return [
    { label: 'New chapter', icon: 'plus', hint: 'Ctrl+N', edit: true, run: () => newChapter() },
    { label: 'Insert template…', icon: 'layout', edit: true, run: openTemplates },
    { label: 'Export…', icon: 'export', hint: 'Ctrl+E', edit: true, run: openExport },
    { label: 'Find in project', icon: 'magnifying-glass', hint: 'Ctrl+Shift+F', run: openSearch },
    { label: 'Snapshots of this chapter', icon: 'clock-counter-clockwise', hint: 'Ctrl+Shift+P', run: openSnapshots },
    { label: 'Codex: characters, places, items', icon: 'book-open-text', run: () => openCodex() },
    { label: 'Corkboard', icon: 'squares-four', run: openCorkboard },
    { label: 'Media library', icon: 'images', run: openAssets },
    { label: 'Scratchpad notes', icon: 'sticky-note', hint: 'Alt+N', run: toggleNotes },
    { label: 'Writing statistics', icon: 'chart-bar', owner: true, run: openStats },
    { label: 'Teamwork', icon: 'users-three', hint: 'Ctrl+Shift+U', run: () => openTeamPanel() },
    { label: 'Dictation (voice to text)', icon: 'microphone', hint: 'Ctrl+Shift+Space', edit: true, run: toggleDictation },
    { label: 'Read aloud', icon: 'speaker-high', run: readAloud },
    { label: 'Toggle zen mode', icon: 'corners-out', hint: 'F11', run: toggleZen },
    { label: 'Toggle typewriter mode', icon: 'text-align-center', hint: 'Ctrl+Shift+Y', run: () => $('#btnTypewriter').click() },
    { label: 'Toggle focus mode', icon: 'crosshair', run: () => $('#btnFocus').click() },
    ...THEMES.map((t) => ({ label: `Theme: ${t.name}`, icon: 'palette', run: () => setTheme(t.id) })),
    { label: 'Cycle theme', icon: 'palette', hint: 'Ctrl+Shift+T', run: cycleTheme },
    { label: 'Settings', icon: 'gear', hint: 'Ctrl+,', run: () => openSettings() },
    { label: 'Fonts…', icon: 'text-aa', owner: true, run: () => openSettings('fonts') },
    { label: 'Storage & backups…', icon: 'hard-drives', owner: true, run: () => openSettings('storage') },
    { label: 'Trash (deleted chapters)', icon: 'trash', run: openTrash },
    { label: 'New project…', icon: 'folder-plus', owner: true, run: newProject },
    { label: 'Open project…', icon: 'folder-open', hint: 'Ctrl+O', owner: true, run: () => openProject() },
    { label: 'Save project as / copy…', icon: 'copy', hint: 'Ctrl+Shift+S', owner: true, run: saveProjectAs },
    { label: 'Import document (DOCX, MD, TXT)…', icon: 'file-arrow-down', owner: true, run: importDocument },
    { label: 'Keyboard shortcuts', icon: 'keyboard', run: () => openSettings('shortcuts') },
  ]
}

boot().catch((e) => {
  console.error(e)
  $('#bootMessage').hidden = false
  $('#bootMessage').textContent = `Something went wrong while starting: ${e.message}`
})

void renderTree
