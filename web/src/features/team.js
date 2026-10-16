// Teamwork panel: start/stop the session, invite, members & roles, chat.

import { api } from '../lib/api.js'
import { $, clear, copyText, h, icon, timeAgo } from '../lib/dom.js'
import { isOwner, on, state } from '../lib/store.js'
import { confirmDialog, errorToast, openPanel, panelOpen, toast } from '../lib/ui.js'

const ROLE_LABEL = { owner: 'Host', editor: 'Editor', commenter: 'Commenter', viewer: 'Viewer' }

export function initTeam() {
  $('#btnTeam').onclick = () => openTeamPanel()
  on('server:members-changed', refreshMembers)
  on('server:chat', ({ message }) => {
    state.chat.push(message)
    if (panelOpen('team')) renderChat()
    else {
      $('#btnTeam').classList.add('notify')
      toast(`${message.from}: ${message.text.slice(0, 80)}`, { timeout: 4000 })
    }
  })
  on('team:refresh', () => panelOpen('team') && rerender())
}

let body = null

export function openTeamPanel(force = false) {
  if (force && panelOpen('team')) return rerender()
  body = openPanel('team', 'Teamwork', (b) => { body = b; rerender() })
  $('#btnTeam').classList.remove('notify')
}

async function rerender() {
  if (!body) return
  clear(body)
  const team = state.me.team || { active: false }
  if (!team.active) {
    if (isOwner()) renderStart(body)
    else body.append(h('p.muted', 'The host has ended the session.'))
    return
  }
  if (isOwner()) renderHost(body, team)
  else body.append(h('div.guest-info', h('p', 'You are connected as ', h('strong', state.me.session.name), ` (${ROLE_LABEL[state.me.role]}).`),
    h('button.btn.small.ghost', { onclick: async () => { await api.post('/api/logout'); location.href = '/join' } }, icon('sign-out'), 'Leave session')))
  body.append(h('h4', 'People'), h('div#memberList.member-list'))
  body.append(h('h4', 'Chat'), h('div#chatLog.chat-log', { 'aria-live': 'polite' }), chatForm())
  await refreshMembers()
  state.chat = await api.get('/api/chat').catch(() => [])
  renderChat()
}

function renderStart(b) {
  const t = state.settings.team
  const pw = h('input.input', { type: 'text', placeholder: 'Leave empty to generate a strong passphrase', maxlength: 200, minlength: 8, autocomplete: 'off' })
  const role = h('select.input', [['editor', 'Editor — can write'], ['commenter', 'Commenter — notes & chat'], ['viewer', 'Viewer — read only']].map(([k, l]) => h('option', { value: k, selected: t.defaultRole === k }, l)))
  const https = h('input', { type: 'checkbox', checked: t.https })
  const start = h('button.btn.primary.wide', { onclick: async () => {
    if (pw.value && pw.value.length < 8) return toast('Password must be at least 8 characters', { type: 'error' })
    start.disabled = true
    start.textContent = 'Starting…'
    try {
      await api.post('/api/team/start', { password: pw.value || null, defaultRole: role.value, https: https.checked })
      await refreshMe()
      rerender()
    } catch (e) {
      errorToast(e)
      start.disabled = false
      start.textContent = 'Start session'
    }
  } }, icon('broadcast'), 'Start session')
  b.append(
    h('p', 'Invite people on your network to write with you in real time. They only need a web browser.'),
    h('label.field', 'Password', pw),
    h('label.field', 'People join as', role),
    h('label.check', https, 'Use HTTPS (self-signed certificate)'),
    start,
    h('p.muted.small', `Guests connect to port ${t.port}. Windows may ask to allow ScribeOS through the firewall — choose “Private networks”. A snapshot of every chapter is taken when the session starts.`),
    h('details.muted.small', h('summary', 'Collaborating over the internet?'), h('p', 'Teamwork is designed for your local network. For remote teammates, use a private network tool like Tailscale (everyone joins your tailnet and uses your Tailscale IP), or a tunnel such as Cloudflare Tunnel with HTTPS enabled. Never share the password publicly.')),
  )
}

function renderHost(b, team) {
  let showPw = false
  const pwText = h('code.secret', '••••••••••')
  const url = team.urls[0]
  b.append(
    h('div.live-banner', icon('broadcast', true), h('span', 'Session is live'), h('small', `since ${timeAgo(team.startedAt * 1000)}`)),
    h('div.qr', { 'aria-label': 'QR code with the invite link' }),
    h('label.field', 'Invite link', h('div.copy-row', h('input.input', { value: url, readOnly: true }), h('button.btn.small', { onclick: () => copyText(url).then(() => toast('Link copied')) }, icon('copy')))),
    team.urls.length > 1 ? h('details.small', h('summary', 'Other addresses'), team.urls.slice(1).map((u) => h('div.mono.small', u))) : null,
    h('div.kv', h('span', 'Room code'), h('code', team.roomCode)),
    h('div.kv', h('span', 'Password'), pwText,
      h('button.icon-btn.small', { title: 'Show / hide', onclick: () => { showPw = !showPw; pwText.textContent = showPw ? team.password : '••••••••••' } }, icon('eye')),
      h('button.icon-btn.small', { title: 'Copy password', onclick: () => copyText(team.password).then(() => toast('Password copied')) }, icon('copy')),
      h('button.icon-btn.small', { title: 'New password (signs everyone out)', onclick: async () => {
        if (!(await confirmDialog('Everyone currently connected will be signed out and must rejoin with the new password.', { title: 'Generate a new password?', ok: 'Generate' }))) return
        await api.patch('/api/team', { regeneratePassword: true }).catch(errorToast)
        await refreshMe()
        rerender()
      } }, icon('arrows-clockwise'))),
    h('button.btn.small.ghost', { onclick: () => copyText(`Join my ScribeOS session:\n${url}\nRoom: ${team.roomCode}\nPassword: ${team.password}`).then(() => toast('Invitation copied')) }, icon('envelope-simple'), 'Copy full invitation'),
    team.https ? h('div.kv.small', h('span', 'Certificate'), h('code.mono.small.wrap', team.fingerprint)) : null,
    h('div.row-actions',
      h('label.check', h('input', { type: 'checkbox', checked: team.locked, onchange: async (e) => { await api.patch('/api/team', { locked: e.target.checked }).catch(errorToast); await refreshMe() } }), 'Lock room (no new joins)')),
    h('label.field', 'New guests join as', h('select.input', { onchange: (e) => api.patch('/api/team', { defaultRole: e.target.value }).catch(errorToast) }, ['editor', 'commenter', 'viewer'].map((r) => h('option', { value: r, selected: team.defaultRole === r }, ROLE_LABEL[r])))),
    h('button.btn.danger.wide', { onclick: async () => {
      if (!(await confirmDialog('All guests will be disconnected. Their work is already saved in your project.', { title: 'End Teamwork session?', ok: 'End session', danger: true }))) return
      await api.post('/api/team/stop').catch(errorToast)
      await refreshMe()
      rerender()
    } }, icon('stop-circle'), 'End session'),
  )
  const qr = b.querySelector('.qr')
  // The SVG is generated by our own server from the invite URL; render it as an image, not markup.
  qr.append(h('img', { src: 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(team.qr), alt: 'QR code', width: 160, height: 160 }))
}

export async function refreshMe() {
  const prevActive = !!state.me?.team?.active
  state.me = await api.get('/api/me')
  if (prevActive !== !!state.me.team?.active) {
    const { emit } = await import('../lib/store.js')
    emit('team:changed')
  }
  updateBadge()
}

export function updateBadge() {
  $('#teamBadge').hidden = !state.me?.team?.active
  $('#btnTeam').classList.toggle('active', !!state.me?.team?.active)
}

async function refreshMembers() {
  if (!state.me?.team?.active) {
    state.members = []
    renderPresence()
    return
  }
  try {
    const t = await api.get('/api/team')
    state.members = t.members || []
  } catch {
    return
  }
  renderPresence()
  const list = document.getElementById('memberList')
  if (!list) return
  clear(list)
  for (const m of state.members) {
    const me = m.id === state.me.session.id
    list.append(h('div.member', h('span.avatar', { style: { background: m.color } }, (m.name || '?').slice(0, 1).toUpperCase()),
      h('span.member-name', m.name, me ? h('small.muted', ' (you)') : null),
      isOwner() && m.role !== 'owner'
        ? h('span.member-actions',
          h('select.input.small', { 'aria-label': `Role of ${m.name}`, onchange: (e) => api.patch(`/api/team/members/${m.id}`, { role: e.target.value }).catch(errorToast) }, ['editor', 'commenter', 'viewer'].map((r) => h('option', { value: r, selected: m.role === r }, ROLE_LABEL[r]))),
          h('button.icon-btn.small', { title: `Remove ${m.name}`, onclick: async () => {
            if (await confirmDialog(`Disconnect ${m.name}? They can rejoin with the password unless you lock the room or change it.`, { title: 'Remove guest?', ok: 'Remove', danger: true })) api.post(`/api/team/members/${m.id}/kick`).catch(errorToast)
          } }, icon('user-minus')))
        : h('small.muted', ROLE_LABEL[m.role])))
  }
}

function renderPresence() {
  const host = $('#presence')
  clear(host)
  for (const m of state.members.slice(0, 6)) host.append(h('span.avatar.small', { title: `${m.name} (${ROLE_LABEL[m.role]})`, style: { background: m.color } }, (m.name || '?').slice(0, 1).toUpperCase()))
  if (state.members.length > 6) host.append(h('span.avatar.small.more', `+${state.members.length - 6}`))
}

function renderChat() {
  const log = document.getElementById('chatLog')
  if (!log) return
  clear(log)
  for (const m of state.chat.slice(-100)) log.append(h('div.chat-msg', h('strong', { style: { color: m.color } }, m.from), h('span', m.text), h('small.muted', new Date(m.at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }))))
  log.scrollTop = log.scrollHeight
}

function chatForm() {
  const input = h('input.input', { placeholder: 'Message everyone…', maxlength: 2000, 'aria-label': 'Chat message' })
  return h('form.chat-form', { onsubmit: async (e) => {
    e.preventDefault()
    const text = input.value.trim()
    if (!text) return
    input.value = ''
    await api.post('/api/chat', { text }).catch(errorToast)
  } }, input, h('button.btn.small', { type: 'submit', 'aria-label': 'Send' }, icon('paper-plane-right')))
}
