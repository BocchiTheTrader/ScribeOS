import '@phosphor-icons/web/fill'
import './styles/base.css'
import './styles/themes.css'
import './styles/app.css'

const $ = (s) => document.querySelector(s)

async function init() {
  try {
    const me = await (await fetch('/api/me')).json()
    if (me.signedIn) {
      location.replace('/')
      return
    }
  } catch {}
  let status = { active: false }
  try {
    status = await (await fetch('/api/team/status')).json()
  } catch {}
  if (!status.active) {
    $('#joinInactive').hidden = false
    return
  }
  const params = new URLSearchParams(location.search)
  $('#room').value = params.get('room') || ''
  $('#name').value = localStorage.getItem('scribeos:guest-name') || ''
  $('#joinForm').hidden = false
  ;($('#room').value ? ($('#name').value ? $('#password') : $('#name')) : $('#room')).focus()
  if (status.locked) $('#joinError').textContent = 'The host has locked this session.'

  $('#joinForm').addEventListener('submit', async (e) => {
    e.preventDefault()
    const btn = $('#joinBtn')
    btn.disabled = true
    $('#joinError').textContent = ''
    try {
      const res = await fetch('/api/team/join', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ room: $('#room').value.trim(), name: $('#name').value.trim(), password: $('#password').value }),
      })
      if (!res.ok) {
        const j = await res.json().catch(() => ({}))
        throw new Error(typeof j.detail === 'string' ? j.detail : 'Could not join')
      }
      try {
        localStorage.setItem('scribeos:guest-name', $('#name').value.trim())
      } catch {}
      location.replace('/')
    } catch (err) {
      $('#joinError').textContent = err.message
      btn.disabled = false
    }
  })
}

init()
