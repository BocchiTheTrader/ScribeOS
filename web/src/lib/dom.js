// Tiny DOM helpers. User data is only ever inserted as text or attributes, never as HTML.

export const $ = (sel, root = document) => root.querySelector(sel)
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)]

// Native append(null) would insert the text "null"; conditional children (`cond ? el : null`)
// are used throughout the UI, so skip null/false the same way h() does.
const nativeAppend = Element.prototype.append
Element.prototype.append = function (...nodes) {
  return nativeAppend.apply(this, nodes.filter((n) => n != null && n !== false))
}

/**
 * h('button#save.btn.primary', { onclick, title }, 'Save', h('i.ph.ph-x'))
 */
export function h(tag, attrs, ...children) {
  if (attrs == null || typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs)) {
    if (attrs != null) children.unshift(attrs)
    attrs = {}
  }
  const [head, ...classes] = tag.split('.')
  const [name, id] = head.split('#')
  const el = document.createElement(name || 'div')
  if (id) el.id = id
  if (classes.length) el.className = classes.join(' ')
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v)
    else if (k === 'class') el.className += (el.className ? ' ' : '') + v
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v)
    else if (k === 'dataset') Object.assign(el.dataset, v)
    else if (k in el && typeof v !== 'string') el[k] = v
    else if (k === 'value') el.value = v
    else el.setAttribute(k, v === true ? '' : String(v))
  }
  append(el, children)
  return el
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue
    el.append(c instanceof Node ? c : document.createTextNode(String(c)))
  }
}

export const icon = (name, fill = false) => h(`i.ph${fill ? '-fill' : ''}.ph-${name}`, { 'aria-hidden': 'true' })

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild)
  return el
}

export function debounce(fn, ms) {
  let t
  const d = (...args) => {
    clearTimeout(t)
    t = setTimeout(() => fn(...args), ms)
  }
  d.flush = (...args) => {
    clearTimeout(t)
    return fn(...args)
  }
  d.cancel = () => clearTimeout(t)
  return d
}

export function formatNumber(n) {
  return new Intl.NumberFormat().format(n || 0)
}

export function timeAgo(iso) {
  if (!iso) return ''
  const t = typeof iso === 'number' ? iso : Date.parse(iso)
  const s = Math.round((Date.now() - t) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return new Date(t).toLocaleString()
}

export function countWords(text) {
  const m = text.match(/\S+/g)
  return m ? m.length : 0
}

export function store(key, value) {
  try {
    if (value === undefined) return JSON.parse(localStorage.getItem(key) ?? 'null')
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, JSON.stringify(value))
  } catch {
    return null
  }
}

export function copyText(text) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text)
  const ta = h('textarea', { style: { position: 'fixed', opacity: 0 } }, text)
  document.body.append(ta)
  ta.select()
  document.execCommand('copy')
  ta.remove()
  return Promise.resolve()
}
