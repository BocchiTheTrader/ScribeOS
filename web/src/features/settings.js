// Settings dialog (tabs) and the quick theme switcher.

import { api, desktop, upload } from '../lib/api.js'
import { $, clear, h, icon } from '../lib/dom.js'
import { emit, isOwner, state } from '../lib/store.js'
import { confirmDialog, errorToast, modal, popover, toast } from '../lib/ui.js'
import { BUNDLED, customFonts, loadCustomFonts } from './fonts.js'
import { CYCLE, THEMES, applyAppearance, contrast, currentThemeChoice, setGuestTheme } from './themes.js'

export async function saveSettings(patch) {
  if (!isOwner()) {
    // Guests keep appearance tweaks locally for this session.
    deepAssign(state.settings, patch)
    applyAppearance()
    emit('settings:changed')
    return
  }
  try {
    state.settings = await api.patch('/api/settings', patch)
    applyAppearance()
    emit('settings:changed', patch)
  } catch (e) {
    errorToast(e)
  }
}

function deepAssign(target, patch) {
  for (const [k, v] of Object.entries(patch)) {
    if (v && typeof v === 'object' && !Array.isArray(v)) deepAssign((target[k] ||= {}), v)
    else target[k] = v
  }
}

export async function setTheme(theme) {
  if (!isOwner()) {
    setGuestTheme(theme)
    applyAppearance()
    return
  }
  if (state.project?.theme) {
    // A per-project theme overrides the global one; clear it when the user picks a new theme.
    state.project.theme = null
    api.patch('/api/project', { theme: null }).catch(() => {})
  }
  await saveSettings({ editor: { theme } })
}

export function cycleTheme() {
  const cur = currentThemeChoice()
  const next = CYCLE[(CYCLE.indexOf(cur) + 1) % CYCLE.length]
  setTheme(next)
  toast(`Theme: ${THEMES.find((t) => t.id === next).name}`, { timeout: 1200 })
}

export function themePopover(anchor) {
  const cur = currentThemeChoice()
  popover(anchor, [
    { header: 'Theme' },
    ...THEMES.map((t) => ({
      element: h(`button.popover-item${cur === t.id ? '.active' : ''}`, { onclick: () => { document.querySelector('.popover')?.remove(); setTheme(t.id) } },
        t.swatch ? h('span.theme-swatch', { style: { background: `linear-gradient(135deg, ${t.swatch[0]} 50%, ${t.swatch[1]} 50%)` } }) : icon(t.id === 'system' ? 'desktop' : 'paint-brush'),
        h('span', t.name), cur === t.id ? icon('check') : null),
    })),
    isOwner() && state.project ? { separator: true } : null,
    isOwner() && state.project ? { label: state.project.theme ? `Unpin theme from this project` : 'Always use this theme for this project', icon: 'push-pin', run: async () => {
      const theme = state.project.theme ? null : (cur === 'system' ? null : cur)
      state.project = { ...state.project, ...(await api.patch('/api/project', { theme })) }
      applyAppearance()
    } } : null,
    { separator: true },
    { label: 'Appearance settings…', icon: 'sliders', run: () => openSettings('appearance') },
  ], { align: 'right' })
}

// ------------------------------------------------------------------ settings dialog
const TABS = [
  { id: 'appearance', label: 'Appearance', icon: 'paint-brush' },
  { id: 'editor', label: 'Writing', icon: 'pen-nib' },
  { id: 'fonts', label: 'Fonts', icon: 'text-aa', owner: true },
  { id: 'storage', label: 'Storage & backups', icon: 'hard-drives', owner: true },
  { id: 'voice', label: 'Voice', icon: 'microphone', owner: true },
  { id: 'team', label: 'Teamwork', icon: 'users-three', owner: true },
  { id: 'shortcuts', label: 'Shortcuts', icon: 'keyboard' },
  { id: 'about', label: 'About', icon: 'info' },
]

export function openSettings(tab = 'appearance') {
  const m = modal('Settings', { width: 820, className: 'settings-modal' })
  const nav = h('nav.settings-nav', { role: 'tablist' })
  const pane = h('div.settings-pane', { role: 'tabpanel' })
  m.body.append(h('div.settings-layout', nav, pane))
  const show = (id) => {
    for (const b of nav.children) b.classList.toggle('active', b.dataset.tab === id)
    clear(pane)
    RENDER[id](pane)
  }
  for (const t of TABS) {
    if (t.owner && !isOwner()) continue
    nav.append(h('button.settings-tab', { role: 'tab', dataset: { tab: t.id }, onclick: () => show(t.id) }, icon(t.icon), t.label))
  }
  show(tab)
}

const ed = () => state.settings.editor

function row(label, control, hint) {
  return h('div.setting-row', h('div.setting-label', h('span', label), hint ? h('small', hint) : null), h('div.setting-control', control))
}

function slider(key, min, max, step, unit = '') {
  const val = h('output', `${ed()[key]}${unit}`)
  const input = h('input', { type: 'range', min, max, step, value: ed()[key], oninput: (e) => { val.textContent = `${e.target.value}${unit}`; saveSettingsSoon({ editor: { [key]: Number(e.target.value) } }) } })
  return h('div.slider', input, val)
}

function toggle(key, onchange) {
  return h('label.switch', h('input', { type: 'checkbox', checked: !!ed()[key], onchange: (e) => { saveSettings({ editor: { [key]: e.target.checked } }); onchange?.(e.target.checked) } }), h('span.switch-ui'))
}

let pendingPatch = {}
let pendingTimer = null
function saveSettingsSoon(patch) {
  deepAssign(state.settings, patch)
  applyAppearance()
  deepAssign(pendingPatch, patch)
  clearTimeout(pendingTimer)
  pendingTimer = setTimeout(() => {
    const p = pendingPatch
    pendingPatch = {}
    saveSettings(p)
  }, 400)
}

function fontSelect(key) {
  const sel = h('select.input', { onchange: (e) => saveSettings({ editor: { [key]: e.target.value } }) })
  const groups = {}
  for (const f of BUNDLED) (groups[f.category] ||= []).push(f.family)
  if (customFonts.length) groups['Imported'] = customFonts.map((f) => f.family)
  for (const [g, fams] of Object.entries(groups)) sel.append(h('optgroup', { label: g }, fams.map((f) => h('option', { value: f, selected: ed()[key] === f }, f))))
  const known = Object.values(groups).flat()
  if (!known.includes(ed()[key])) sel.append(h('option', { value: ed()[key], selected: true }, `${ed()[key]} (system)`))
  return sel
}

const RENDER = {
  appearance(p) {
    const cur = currentThemeChoice()
    p.append(h('h4', 'Theme'),
      h('div.theme-grid', THEMES.map((t) => h(`button.theme-card${cur === t.id ? '.active' : ''}`, { onclick: async () => { await setTheme(t.id); clear(p); RENDER.appearance(p) } },
        h('span.theme-preview', { dataset: { preview: t.id } }, h('span.tp-line'), h('span.tp-line.short'), h('span.tp-accent')), t.name))))
    if (cur === 'custom' && isOwner()) p.append(customThemeEditor())
    p.append(h('h4', 'Typography'),
      row('Body font', fontSelect('bodyFont')),
      row('Heading font', fontSelect('headingFont')),
      row('Interface font', fontSelect('uiFont')),
      row('Font size', slider('fontSize', 12, 36, 1, 'px')),
      row('Line height', slider('lineHeight', 1.0, 3.0, 0.05)),
      row('Letter spacing', slider('letterSpacing', -0.05, 0.3, 0.01, 'em')),
      row('Paragraph spacing', slider('paragraphSpacing', 0, 3, 0.1, 'em')),
      row('First-line indent', toggle('paragraphIndent'), 'Book-style paragraphs'),
      h('h4', 'Layout'),
      row('Page width', slider('contentWidth', 480, 1600, 20, 'px')),
      row('Interface scale', slider('uiScale', 80, 160, 5, '%')),
      row('Vertical text', toggle('verticalText'), 'Top-to-bottom, right-to-left (manga / Japanese)'),
      row('Reduce motion', toggle('reduceMotion'), 'Disables animations and smooth scrolling'),
    )
  },
  editor(p) {
    p.append(
      row('Typewriter mode', toggle('typewriter'), 'Keep the current line centred'),
      row('Focus mode', toggle('focusMode', () => emit('editor:redraw')), 'Dim everything except the current paragraph'),
      row('Smart typography', toggle('smartTypography', () => emit('editor:remount')), '“Curly quotes”, em-dashes — and ellipses…'),
      row('Spell check', toggle('spellcheck', () => emit('editor:remount'))),
      row('Highlight Codex names', toggle('codexHighlight', () => emit('editor:redraw')), 'Underline characters, places and items you defined'),
      isOwner() ? row('Daily word goal', h('input.input.narrow', { type: 'number', min: 0, max: 100000, step: 50, value: ed().dailyGoal, onchange: (e) => saveSettings({ editor: { dailyGoal: Math.max(0, parseInt(e.target.value, 10) || 0) } }) })) : null,
      row('Default sprint length', h('input.input.narrow', { type: 'number', min: 1, max: 240, value: ed().sprintMinutes, onchange: (e) => saveSettings({ editor: { sprintMinutes: Math.max(1, Math.min(240, parseInt(e.target.value, 10) || 25)) } }) }), 'minutes'),
      isOwner() ? row('Author name', h('input.input', { value: state.settings.authorName, maxlength: 120, placeholder: 'Used on title pages and in Teamwork', onchange: (e) => saveSettings({ authorName: e.target.value }) })) : null,
    )
  },
  async fonts(p) {
    const list = h('div.font-list')
    const render = () => {
      clear(list)
      if (!customFonts.length) list.append(h('p.muted', 'No imported fonts yet.'))
      for (const f of customFonts) {
        list.append(h('div.font-item', h('span', { style: { fontFamily: `'${f.family}'` } }, f.family), h('small.muted', f.file),
          h('button.icon-btn.small', { title: 'Remove font', onclick: async () => { await api.del(`/api/fonts/${encodeURIComponent(f.file)}`).catch(errorToast); await loadCustomFonts(); render() } }, icon('trash'))))
      }
    }
    const input = h('input', { type: 'file', accept: '.ttf,.otf,.woff,.woff2', multiple: true, hidden: true, onchange: async () => {
      for (const file of input.files) {
        try {
          const r = await upload('/api/fonts', file)
          toast(`Imported ${r.family}`)
        } catch (e) {
          errorToast(e)
        }
      }
      await loadCustomFonts()
      render()
    } })
    p.append(h('p.muted', `${BUNDLED.length} fonts are built in and work offline. Import your own .ttf/.otf/.woff2 files to use them in the editor and in PDF exports.`),
      h('div.row-actions', h('button.btn', { onclick: () => input.click() }, icon('upload-simple'), 'Import font…'), desktop() ? h('button.btn.ghost', { onclick: () => desktop().reveal('fonts') }, icon('folder-open'), 'Open fonts folder') : null), input, list,
      h('h4', 'Use a font installed on this computer'),
      h('div.row-actions', h('button.btn.ghost', { onclick: async (e) => {
        e.currentTarget.disabled = true
        e.currentTarget.textContent = 'Scanning…'
        const fams = await api.get('/api/fonts/system').catch(() => [])
        const dl = h('select.input', { size: 8, onchange: (ev) => saveSettings({ editor: { bodyFont: ev.target.value } }) }, fams.map((f) => h('option', { value: f, style: { fontFamily: `'${f}'` } }, f)))
        e.target.replaceWith(h('div', dl, h('small.muted', 'Selecting a font applies it to the body text.')))
      } }, 'List system fonts…')))
    render()
  },
  storage(p) {
    const st = state.settings.storage
    const pathRow = (label, key, hint) => {
      const input = h('input.input', { value: st[key], readOnly: !!desktop(), onchange: (e) => saveSettings({ storage: { [key]: e.target.value } }) })
      return row(label, h('div.path-picker', input, desktop() ? h('button.btn.small', { onclick: async () => {
        const dir = await desktop().choose_folder(st[key])
        if (dir) { input.value = dir; saveSettings({ storage: { [key]: dir } }) }
      } }, 'Change…') : null), hint)
    }
    const num = (key, min, max) => h('input.input.narrow', { type: 'number', min, max, value: st[key], onchange: (e) => saveSettings({ storage: { [key]: Math.max(min, Math.min(max, parseInt(e.target.value, 10) || min)) } }) })
    const backupList = h('div.backup-list')
    const loadBackups = async () => {
      clear(backupList)
      if (!state.project) return backupList.append(h('p.muted', 'Open a project to see its backups.'))
      const list = await api.get('/api/backups').catch(() => [])
      if (!list.length) backupList.append(h('p.muted', 'No backups yet for this project.'))
      for (const b of list.slice(0, 15)) {
        const m = b.name.match(/(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/)
        backupList.append(h('div.backup-item', h('span', m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}` : b.name), h('small.muted', `${(b.size / 1024).toFixed(0)} KB`),
          h('button.btn.small.ghost', { onclick: async () => {
            if (!(await confirmDialog('This opens the backup as a separate copy of the project. Your current project is not changed.', { title: 'Restore backup?', ok: 'Open copy' }))) return
            try { await api.post('/api/backups/restore', { name: b.name }); location.reload() } catch (e) { errorToast(e) }
          } }, 'Restore as copy')))
      }
    }
    p.append(
      pathRow('New projects folder', 'projectsDir', 'Where new projects are created. You can also save any project anywhere with Save As.'),
      pathRow('Backups folder', 'backupDir', 'Put this on another drive or a synced folder for safety.'),
      row('Backup every', h('span', num('backupIntervalMin', 1, 1440), ' minutes (while you write)')),
      row('Backups to keep', num('backupKeep', 1, 500), 'Plus one per day for the last 30 days'),
      row('Automatic snapshot every', h('span', num('snapshotIntervalMin', 1, 1440), ' minutes')),
      row('Max upload size', h('span', num('maxUploadMB', 1, 10000), ' MB'), 'Images, GIFs, video and audio'),
      h('div.row-actions', state.project ? h('button.btn', { onclick: async () => { try { await api.post('/api/backups'); toast('Backup created'); loadBackups() } catch (e) { errorToast(e) } } }, icon('floppy-disk'), 'Back up now') : null,
        desktop() ? h('button.btn.ghost', { onclick: () => desktop().reveal('backups') }, icon('folder-open'), 'Open backups folder') : null),
      h('h4', 'Backups of this project'), backupList)
    loadBackups()
  },
  async voice(p) {
    const v = state.settings.voice
    const status = h('p.muted', 'Checking…')
    p.append(status,
      row('Speech model', h('select.input', { onchange: (e) => saveSettings({ voice: { model: e.target.value } }) },
        [['tiny', 'Tiny — fastest (~75 MB)'], ['base', 'Base — balanced (~145 MB)'], ['small', 'Small — accurate (~480 MB)'], ['medium', 'Medium — best (~1.5 GB)']].map(([k, l]) => h('option', { value: k, selected: v.model === k }, l))), 'Downloaded once, on first use'),
      row('Language', h('select.input', { onchange: (e) => saveSettings({ voice: { language: e.target.value } }) },
        [['auto', 'Detect automatically'], ['en', 'English'], ['tr', 'Türkçe'], ['de', 'Deutsch'], ['fr', 'Français'], ['es', 'Español'], ['it', 'Italiano'], ['pt', 'Português'], ['ja', '日本語'], ['ko', '한국어'], ['zh', '中文'], ['ru', 'Русский'], ['ar', 'العربية']].map(([k, l]) => h('option', { value: k, selected: v.language === k }, l)))),
      row('Voice commands', h('label.switch', h('input', { type: 'checkbox', checked: v.commands, onchange: (e) => saveSettings({ voice: { commands: e.target.checked } }) }), h('span.switch-ui')), 'Say “new paragraph”, “comma”, “question mark”, “scene break”, “stop dictation”…'),
      h('p.muted.small', 'Audio is processed entirely on this computer and never uploaded.'))
    try {
      const s = await api.get('/api/voice/status')
      status.textContent = s.available ? '✓ Offline dictation is ready.' : s.missing.length ? `Offline engine not installed (${s.missing.join(', ')}). Install with: pip install "scribeos[voice]". The browser's speech recognition is used where available.` : 'No microphone detected.'
    } catch {
      status.textContent = 'Voice status unavailable.'
    }
  },
  team(p) {
    const t = state.settings.team
    p.append(
      row('Port', h('input.input.narrow', { type: 'number', min: 1024, max: 65535, value: t.port, onchange: (e) => saveSettings({ team: { port: parseInt(e.target.value, 10) || 8765 } }) }), 'Teammates connect to this port on your computer'),
      row('Default role for new guests', h('select.input', { onchange: (e) => saveSettings({ team: { defaultRole: e.target.value } }) }, [['editor', 'Editor — can write'], ['commenter', 'Commenter — notes & chat'], ['viewer', 'Viewer — read only']].map(([k, l]) => h('option', { value: k, selected: t.defaultRole === k }, l)))),
      row('Guest upload limit', h('span', h('input.input.narrow', { type: 'number', min: 1, max: 2000, value: t.guestMaxUploadMB, onchange: (e) => saveSettings({ team: { guestMaxUploadMB: parseInt(e.target.value, 10) || 25 } }) }), ' MB')),
      row('Use HTTPS', h('label.switch', h('input', { type: 'checkbox', checked: t.https, onchange: (e) => saveSettings({ team: { https: e.target.checked } }) }), h('span.switch-ui')), 'Encrypts traffic with a self-signed certificate. Browsers show a one-time warning; compare the fingerprint shown in the Teamwork panel.'),
      h('p.muted.small', 'The first time you start Teamwork, Windows may ask whether to allow ScribeOS through the firewall. Choose “Private networks” only.'))
  },
  shortcuts(p) {
    const rows = [['Command palette', 'Ctrl+K'], ['New chapter', 'Ctrl+N'], ['Save now', 'Ctrl+S'], ['Open project', 'Ctrl+O'], ['Save project as', 'Ctrl+Shift+S'], ['Find in project', 'Ctrl+Shift+F'], ['Cycle theme', 'Ctrl+Shift+T'], ['Zen mode', 'F11'], ['Typewriter mode', 'Ctrl+Shift+Y'], ['Scratchpad', 'Alt+N'], ['Dictation', 'Ctrl+Shift+Space'], ['Export', 'Ctrl+E'], ['Snapshots', 'Ctrl+Shift+P'], ['Teamwork', 'Ctrl+Shift+U'], ['Settings', 'Ctrl+,'], ['Move chapter', 'Alt+↑ / Alt+↓'], ['Open link in editor', 'Ctrl+click'], ['Bold / Italic / Underline', 'Ctrl+B / I / U']]
    p.append(h('table.shortcuts', h('tbody', rows.map(([a, k]) => h('tr', h('td', a), h('td', h('kbd', k)))))))
  },
  about(p) {
    p.append(h('div.about', h('div.brand.big', icon('feather', true), h('span', 'ScribeOS')), h('p', 'A calm, offline-first writing studio. Your projects are plain folders you own.'), h('p.muted', 'Crafted with focus in mind by BocchiTheTrader.')))
  },
}

function customThemeEditor() {
  const c = state.settings.editor.customTheme
  const warn = h('p.contrast-warn')
  const check = () => {
    const r = contrast(c.bg, c.text)
    warn.textContent = r < 4.5 ? `⚠ Text contrast ${r.toFixed(1)}:1 is below the recommended 4.5:1` : `✓ Text contrast ${r.toFixed(1)}:1`
    warn.classList.toggle('bad', r < 4.5)
  }
  const color = (key, label) => h('label.color-field', h('input', { type: 'color', value: c[key], oninput: (e) => { c[key] = e.target.value; check(); saveSettingsSoon({ editor: { customTheme: { [key]: e.target.value } } }) } }), label)
  check()
  return h('div.custom-theme', h('h4', 'Custom theme colours'),
    h('div.color-grid', color('bg', 'Background'), color('surface', 'Panels'), color('text', 'Text'), color('muted', 'Muted text'), color('accent', 'Accent'), color('border', 'Borders')),
    row('Base', h('select.input', { onchange: (e) => saveSettings({ editor: { customTheme: { base: e.target.value } } }) }, ['dark', 'light'].map((b) => h('option', { value: b, selected: c.base === b }, b)))), warn)
}
