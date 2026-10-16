// Modals, dialogs, toasts, popover menus and side panels.

import { $, clear, h, icon } from './dom.js'

const overlayRoot = () => $('#overlayRoot')

export function toast(message, { type = 'info', timeout = 3500, action } = {}) {
  const el = h(`div.toast.${type}`, { role: type === 'error' ? 'alert' : 'status' }, h('span', message))
  if (action) el.append(h('button.btn.link', { onclick: () => { action.run(); el.remove() } }, action.label))
  $('#toasts').append(el)
  if (timeout) setTimeout(() => el.remove(), timeout)
  return el
}

export const errorToast = (e) => toast(e?.message || String(e), { type: 'error', timeout: 6000 })

/** Open a modal. Returns { el, body, close }. */
export function modal(title, { width = 520, onClose, className = '' } = {}) {
  const prevFocus = document.activeElement
  const body = h('div.modal-body')
  const closeBtn = h('button.icon-btn', { 'aria-label': 'Close', title: 'Close (Esc)' }, icon('x'))
  const box = h(`div.modal${className ? '.' + className : ''}`, { role: 'dialog', 'aria-modal': 'true', 'aria-label': title, style: { width: `min(${width}px, 94vw)` } },
    h('div.modal-header', h('h3', title), closeBtn), body)
  const overlay = h('div.modal-overlay', box)
  let closed = false
  const close = () => {
    if (closed) return
    closed = true
    overlay.remove()
    document.removeEventListener('keydown', onKey, true)
    onClose?.()
    prevFocus?.focus?.()
  }
  const onKey = (e) => {
    if (e.key === 'Escape' && overlay === overlayRoot().lastElementChild) {
      e.stopPropagation()
      close()
    }
  }
  closeBtn.onclick = close
  overlay.addEventListener('mousedown', (e) => e.target === overlay && close())
  document.addEventListener('keydown', onKey, true)
  overlayRoot().append(overlay)
  setTimeout(() => (box.querySelector('[autofocus]') || box.querySelector('input,select,textarea,button:not(.icon-btn)') || closeBtn).focus(), 0)
  return { el: box, body, close }
}

export function confirmDialog(message, { title = 'Are you sure?', ok = 'OK', danger = false } = {}) {
  return new Promise((resolve) => {
    let result = false
    const m = modal(title, { width: 420, onClose: () => resolve(result) })
    m.body.append(
      h('p.dialog-text', message),
      h('div.dialog-actions',
        h('button.btn', { onclick: () => m.close() }, 'Cancel'),
        h(`button.btn.${danger ? 'danger' : 'primary'}`, { autofocus: true, onclick: () => { result = true; m.close() } }, ok)),
    )
  })
}

export function promptDialog(message, { title = 'ScribeOS', value = '', ok = 'OK', placeholder = '', maxlength = 300, type = 'text' } = {}) {
  return new Promise((resolve) => {
    let result = null
    const input = h('input.input', { value, placeholder, maxlength, type, autofocus: true })
    const m = modal(title, { width: 440, onClose: () => resolve(result) })
    const submit = (e) => {
      e?.preventDefault()
      result = input.value
      m.close()
    }
    m.body.append(h('form', { onsubmit: submit }, h('label.field', message, input), h('div.dialog-actions', h('button.btn', { type: 'button', onclick: () => m.close() }, 'Cancel'), h('button.btn.primary', { type: 'submit' }, ok))))
    setTimeout(() => input.select(), 0)
  })
}

/** Popover menu anchored to an element. items: [{label, icon, run, danger, disabled, separator}] */
export function popover(anchor, items, { align = 'left' } = {}) {
  closePopovers()
  const menu = h('div.popover', { role: 'menu' })
  for (const it of items) {
    if (!it) continue
    if (it.separator) { menu.append(h('div.popover-sep')); continue }
    if (it.header) { menu.append(h('div.popover-header', it.header)); continue }
    if (it.element) { menu.append(it.element); continue }
    menu.append(h(`button.popover-item${it.danger ? '.danger' : ''}${it.active ? '.active' : ''}`, {
      role: 'menuitem', disabled: it.disabled || false,
      onclick: () => { closePopovers(); it.run?.() },
    }, it.icon ? icon(it.icon) : null, h('span', it.label), it.hint ? h('kbd', it.hint) : null))
  }
  document.body.append(menu)
  const r = anchor.getBoundingClientRect()
  const mw = menu.offsetWidth
  const mh = menu.offsetHeight
  let left = align === 'right' ? r.right - mw : r.left
  left = Math.max(8, Math.min(left, window.innerWidth - mw - 8))
  let top = r.bottom + 6
  if (top + mh > window.innerHeight - 8) top = Math.max(8, r.top - mh - 6)
  menu.style.left = left + 'px'
  menu.style.top = top + 'px'
  setTimeout(() => {
    const off = (e) => {
      if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('mousedown', off) }
    }
    document.addEventListener('mousedown', off)
    menu.querySelector('button')?.focus()
  }, 0)
  menu.addEventListener('keydown', (e) => {
    const btns = [...menu.querySelectorAll('button:not([disabled])')]
    const i = btns.indexOf(document.activeElement)
    if (e.key === 'ArrowDown') { e.preventDefault(); btns[(i + 1) % btns.length]?.focus() }
    if (e.key === 'ArrowUp') { e.preventDefault(); btns[(i - 1 + btns.length) % btns.length]?.focus() }
    if (e.key === 'Escape') { menu.remove(); anchor.focus() }
  })
  return menu
}

export function closePopovers() {
  document.querySelectorAll('.popover').forEach((p) => p.remove())
}

// ------------------------------------------------------------------ side panel (one at a time)
let currentPanel = null

export function openPanel(id, title, render) {
  const panel = $('#sidePanel')
  if (currentPanel === id) { closePanel(); return null }
  currentPanel = id
  clear(panel)
  const body = h('div.panel-body')
  panel.append(h('div.panel-header', h('h3', title), h('button.icon-btn', { title: 'Close (Esc)', 'aria-label': 'Close', onclick: closePanel }, icon('x'))), body)
  panel.hidden = false
  render(body)
  return body
}

export function closePanel() {
  currentPanel = null
  const panel = $('#sidePanel')
  panel.hidden = true
  clear(panel)
}

export const panelOpen = (id) => currentPanel === id
