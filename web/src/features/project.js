// Welcome screen and project menu: new / open / recent / save as / import / archive.

import { api, desktop, upload } from '../lib/api.js'
import { $, clear, h, icon, timeAgo } from '../lib/dom.js'
import { isOwner, state } from '../lib/store.js'
import { confirmDialog, errorToast, modal, popover, promptDialog, toast } from '../lib/ui.js'
import { flushSave } from '../editor/editor.js'
import { openTrash } from './panels.js'

const reload = () => location.reload()

export async function newProject() {
  const name = await promptDialog('Project name', { title: 'New project', value: 'My Novel', ok: 'Next' })
  if (!name?.trim()) return
  let parent = state.settings.storage.projectsDir
  const d = desktop()
  if (d) {
    const ok = await confirmDialog(`It will be saved in:\n${parent}`, { title: 'Where should it live?', ok: 'Use this folder' })
    if (!ok) {
      const dir = await d.choose_folder(parent)
      if (!dir) return
      parent = dir
    }
  } else {
    const p = await promptDialog('Folder to create the project in', { value: parent, maxlength: 1000 })
    if (p === null) return
    parent = p
  }
  try {
    await flushSave()
    await api.post('/api/projects', { name: name.trim(), parent })
    reload()
  } catch (e) {
    errorToast(e)
  }
}

export async function openProject(path) {
  if (!path) {
    const d = desktop()
    path = d ? await d.choose_project() : await promptDialog('Path of the .scribe project folder', { maxlength: 1000 })
    if (!path) return
  }
  try {
    await flushSave()
    await api.post('/api/projects/open', { path })
    reload()
  } catch (e) {
    if (e.status === 423 && (await confirmDialog(`${e.message}. Open it anyway? Only do this if you are sure it is not open elsewhere, or edits may be lost.`, { title: 'Project is in use', ok: 'Open anyway', danger: true }))) {
      await api.post('/api/projects/open', { path, force: true }).then(reload, errorToast)
    } else if (e.status !== 423) errorToast(e)
  }
}

export async function saveProjectAs() {
  const d = desktop()
  const parent = d ? await d.choose_folder(state.settings.storage.projectsDir) : await promptDialog('Folder to save a copy into', { value: state.settings.storage.projectsDir, maxlength: 1000 })
  if (!parent) return
  const name = await promptDialog('Name of the copy', { value: state.project.name })
  if (name === null) return
  try {
    await flushSave()
    await api.post('/api/projects/save-as', { parent, name: name || null })
    toast('Saved — you are now working in the copy')
    setTimeout(reload, 600)
  } catch (e) {
    errorToast(e)
  }
}

function pickFile(accept, cb) {
  const input = h('input', { type: 'file', accept, style: { display: 'none' } })
  input.onchange = () => {
    if (input.files[0]) cb(input.files[0])
    input.remove()
  }
  document.body.append(input)
  input.click()
}

export function importV1() {
  pickFile('.json,application/json', async (file) => {
    try {
      const r = await upload('/api/import/v1', file)
      toast(`Imported ${r.created.length} project(s)`)
      setTimeout(reload, 600)
    } catch (e) {
      errorToast(e)
    }
  })
}

export function importArchive() {
  pickFile('.scribez,.zip', async (file) => {
    const t = toast('Importing archive…', { timeout: 0 })
    try {
      await upload('/api/projects/import-archive', file)
      reload()
    } catch (e) {
      errorToast(e)
    } finally {
      t.remove()
    }
  })
}

export function importDocument() {
  pickFile('.docx,.md,.markdown,.txt,.html,.htm', async (file) => {
    const t = toast(`Importing ${file.name}…`, { timeout: 0 })
    try {
      const r = await upload('/api/import', file)
      toast(`Added ${r.chapters.length} chapter(s)`)
      const { reloadProject } = await import('./sidebar.js')
      await reloadProject()
    } catch (e) {
      errorToast(e)
    } finally {
      t.remove()
    }
  })
}

export async function showWelcome() {
  const w = $('#welcome')
  clear(w)
  w.hidden = false
  $('#bootMessage').hidden = true
  const recent = await api.get('/api/recent').catch(() => [])
  const list = h('div.recent-list')
  for (const r of recent) {
    list.append(h(`div.recent-item${r.exists ? '' : '.missing'}`,
      h('button.recent-open', { disabled: !r.exists, onclick: () => openProject(r.path) }, icon('book-bookmark'), h('span.recent-name', r.name), h('small.muted', r.exists ? timeAgo(r.updatedAt) : 'missing — moved or deleted'), h('small.muted.path', r.path)),
      h('button.icon-btn.small', { title: 'Remove from list', onclick: async () => { await api.post('/api/recent/remove', { path: r.path }); showWelcome() } }, icon('x'))))
  }
  w.append(h('div.welcome-card',
    h('div.brand.big', icon('feather', true), h('span', 'ScribeOS')),
    h('p.muted', 'A calm place to write. Your projects are folders you own.'),
    h('div.welcome-actions',
      h('button.btn.primary', { onclick: newProject }, icon('plus'), 'New project'),
      h('button.btn', { onclick: () => openProject() }, icon('folder-open'), 'Open project…'),
      h('button.btn.ghost', { onclick: importArchive }, icon('archive'), 'Open archive (.scribez)…'),
      h('button.btn.ghost', { onclick: importV1 }, icon('arrow-square-in'), 'Import ScribeOS 1.x backup…')),
    recent.length ? h('h4', 'Recent') : null, list))
}

export function projectMenu(anchor) {
  const owner = isOwner()
  popover(anchor, [
    owner ? { label: 'New project…', icon: 'plus', run: newProject } : null,
    owner ? { label: 'Open project…', icon: 'folder-open', hint: 'Ctrl+O', run: () => openProject() } : null,
    owner ? { label: 'Recent projects…', icon: 'clock', run: recentDialog } : null,
    owner ? { label: 'Save as / copy to…', icon: 'copy', hint: 'Ctrl+Shift+S', run: saveProjectAs } : null,
    owner && desktop() ? { label: 'Show in folder', icon: 'folder-notch-open', run: () => desktop().reveal('project') } : null,
    owner ? { separator: true } : null,
    owner ? { label: 'Import document (DOCX, MD, TXT)…', icon: 'file-arrow-down', run: importDocument } : null,
    owner ? { label: 'Open archive (.scribez)…', icon: 'archive', run: importArchive } : null,
    owner ? { label: 'Import ScribeOS 1.x backup…', icon: 'arrow-square-in', run: importV1 } : null,
    { separator: true },
    { label: 'Rename project…', icon: 'pencil-simple', disabled: !['owner', 'editor'].includes(state.me.role), run: async () => {
      const name = await promptDialog('Project name', { value: state.project.name, maxlength: 200 })
      if (!name?.trim()) return
      await api.patch('/api/project', { name: name.trim() }).catch(errorToast)
      const { reloadProject } = await import('./sidebar.js')
      reloadProject()
    } },
    { label: 'Trash', icon: 'trash', run: openTrash },
    owner ? { separator: true } : null,
    owner ? { label: 'Close project', icon: 'x-circle', run: async () => { await flushSave(); await api.post('/api/projects/close'); reload() } } : null,
  ])
}

async function recentDialog() {
  const m = modal('Recent projects', { width: 560 })
  const recent = await api.get('/api/recent').catch(() => [])
  for (const r of recent) m.body.append(h('button.recent-open.wide', { disabled: !r.exists, onclick: () => { m.close(); openProject(r.path) } }, icon('book-bookmark'), h('span.recent-name', r.name), h('small.muted.path', r.path)))
}
