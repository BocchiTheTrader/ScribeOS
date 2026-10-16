// Fetch wrapper: JSON in/out, CSRF header on every mutating request, readable errors.

let csrf = ''
export const setCsrf = (t) => (csrf = t || '')

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

async function request(method, url, body, opts = {}) {
  const headers = { ...(opts.headers || {}) }
  if (method !== 'GET') headers['X-CSRF-Token'] = csrf
  let payload = body
  if (body !== undefined && !(body instanceof Blob) && !(body instanceof ArrayBuffer) && !opts.raw) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  const res = await fetch(url, { method, headers, body: payload, credentials: 'same-origin' })
  if (!res.ok) {
    let msg = res.statusText
    try {
      const j = await res.json()
      msg = typeof j.detail === 'string' ? j.detail : Array.isArray(j.detail) ? j.detail[0]?.msg || msg : msg
    } catch {}
    throw new ApiError(res.status, msg || `Request failed (${res.status})`)
  }
  if (opts.blob) return res
  const ct = res.headers.get('content-type') || ''
  return ct.includes('application/json') ? res.json() : res.text()
}

export const api = {
  get: (u) => request('GET', u),
  post: (u, b = {}) => request('POST', u, b),
  put: (u, b) => request('PUT', u, b),
  patch: (u, b) => request('PATCH', u, b),
  del: (u) => request('DELETE', u),
  download: (u, b) => request('POST', u, b, { blob: true }),
  raw: (u, blob, filename) => request('POST', u, blob, { raw: true, headers: { 'X-Filename': encodeURIComponent(filename || 'upload'), 'Content-Type': 'application/octet-stream' } }),
}

/** Upload with progress (fetch has no upload progress events). */
export function upload(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    xhr.setRequestHeader('X-CSRF-Token', csrf)
    xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name || 'upload'))
    xhr.setRequestHeader('Content-Type', 'application/octet-stream')
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total)
    xhr.onload = () => {
      let data = null
      try {
        data = JSON.parse(xhr.responseText)
      } catch {}
      if (xhr.status >= 200 && xhr.status < 300) resolve(data)
      else reject(new ApiError(xhr.status, data?.detail || `Upload failed (${xhr.status})`))
    }
    xhr.onerror = () => reject(new ApiError(0, 'Network error during upload'))
    xhr.send(file)
  })
}

export const desktop = () => window.pywebview?.api

/** pywebview injects its API asynchronously; wait briefly for it. */
export function waitForDesktop(timeout = 1500) {
  if (window.pywebview?.api) return Promise.resolve(window.pywebview.api)
  return new Promise((resolve) => {
    const done = () => resolve(window.pywebview?.api || null)
    window.addEventListener('pywebviewready', done, { once: true })
    setTimeout(done, timeout)
  })
}
