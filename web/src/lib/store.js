// Shared application state with a minimal pub/sub.

const listeners = new Map()

export const state = {
  me: null, // /api/me
  settings: null, // /api/settings
  project: null, // /api/project
  notes: [],
  codex: [],
  members: [],
  chat: [],
  currentChapterId: null,
  readonly: false,
  desktop: null, // pywebview api or null
}

export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set())
  listeners.get(event).add(fn)
  return () => listeners.get(event).delete(fn)
}

export function emit(event, data) {
  for (const fn of listeners.get(event) || []) {
    try {
      fn(data)
    } catch (e) {
      console.error(e)
    }
  }
}

export const isOwner = () => state.me?.role === 'owner'
export const canEdit = () => ['owner', 'editor'].includes(state.me?.role)
export const canComment = () => ['owner', 'editor', 'commenter'].includes(state.me?.role)
export const teamActive = () => !!state.me?.team?.active

export function chapters() {
  const out = []
  for (const n of state.project?.tree || []) {
    if (n.type === 'part') out.push(...n.children)
    else out.push(n)
  }
  return out
}

export const findChapter = (id) => chapters().find((c) => c.id === id)
