// Applies appearance settings: theme tokens, fonts, sizes and layout modes.

import { store } from '../lib/dom.js'
import { isOwner, state } from '../lib/store.js'
import { fontStack } from './fonts.js'

export const THEMES = [
  { id: 'dark', name: 'Dark (OLED)', swatch: ['#000000', '#f59e0b'] },
  { id: 'light', name: 'Light', swatch: ['#ffffff', '#4f46e5'] },
  { id: 'book', name: 'Book', swatch: ['#f4ecd8', '#8b3a1e'] },
  { id: 'manga', name: 'Manga', swatch: ['#ffffff', '#e11d48'] },
  { id: 'custom', name: 'Custom', swatch: null },
  { id: 'system', name: 'Follow system', swatch: null },
]
export const CYCLE = ['dark', 'light', 'book', 'manga']

const GUEST_KEY = 'scribeos:guest-theme'

/** Guests choose their own theme locally; the host's choice is stored in settings. */
export function currentThemeChoice() {
  if (!isOwner()) return store(GUEST_KEY) || state.settings.editor.theme
  return state.project?.theme || state.settings.editor.theme
}

export function resolvedTheme(choice = currentThemeChoice()) {
  if (choice === 'system') return matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  return choice
}

export function setGuestTheme(theme) {
  store(GUEST_KEY, theme)
}

function luminance(hex) {
  const c = hex.replace('#', '').match(/.{2}/g).map((x) => parseInt(x, 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
}

export function contrast(a, b) {
  const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (l1 + 0.05) / (l2 + 0.05)
}

export function applyAppearance() {
  const s = state.settings.editor
  const root = document.documentElement
  const theme = resolvedTheme()
  root.dataset.theme = theme
  const css = root.style
  if (theme === 'custom') {
    const c = s.customTheme
    root.dataset.base = c.base
    css.setProperty('--bg-base', c.bg)
    css.setProperty('--bg-surface', c.surface)
    css.setProperty('--bg-elevated', c.surface)
    css.setProperty('--text-main', c.text)
    css.setProperty('--text-muted', c.muted)
    css.setProperty('--accent', c.accent)
    css.setProperty('--border', c.border)
  } else {
    delete root.dataset.base
    for (const v of ['--bg-base', '--bg-surface', '--bg-elevated', '--text-main', '--text-muted', '--accent', '--border']) css.removeProperty(v)
  }
  css.setProperty('--editor-font', fontStack(s.bodyFont))
  css.setProperty('--heading-font', fontStack(s.headingFont))
  css.setProperty('--ui-font', fontStack(s.uiFont))
  css.setProperty('--editor-size', `${s.fontSize}px`)
  css.setProperty('--editor-lh', s.lineHeight)
  css.setProperty('--editor-ls', `${s.letterSpacing}em`)
  css.setProperty('--para-space', `${s.paragraphSpacing}em`)
  css.setProperty('--editor-width', `${s.contentWidth}px`)
  css.setProperty('--ui-scale', s.uiScale / 100)
  const b = document.body.classList
  b.toggle('indent-paragraphs', s.paragraphIndent)
  b.toggle('focus-mode', s.focusMode)
  b.toggle('typewriter', s.typewriter)
  b.toggle('vertical-text', s.verticalText)
  b.toggle('reduce-motion', s.reduceMotion || matchMedia('(prefers-reduced-motion: reduce)').matches)
  document.getElementById('btnFocus')?.classList.toggle('active', s.focusMode)
  document.getElementById('btnTypewriter')?.classList.toggle('active', s.typewriter)
}

matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => state.settings && applyAppearance())
