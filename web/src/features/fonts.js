// Bundled fonts (served locally, work offline) plus user-imported and system fonts.

import '@fontsource/lora/400.css'
import '@fontsource/lora/400-italic.css'
import '@fontsource/lora/700.css'
import '@fontsource/merriweather/400.css'
import '@fontsource/merriweather/400-italic.css'
import '@fontsource/merriweather/700.css'
import '@fontsource/eb-garamond/400.css'
import '@fontsource/eb-garamond/400-italic.css'
import '@fontsource/eb-garamond/700.css'
import '@fontsource/literata/400.css'
import '@fontsource/literata/400-italic.css'
import '@fontsource/literata/700.css'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/courier-prime/400.css'
import '@fontsource/courier-prime/700.css'
import '@fontsource/atkinson-hyperlegible/400.css'
import '@fontsource/atkinson-hyperlegible/700.css'
import '@fontsource/opendyslexic/400.css'
import '@fontsource/opendyslexic/700.css'
import '@fontsource/bangers/400.css'
import '@fontsource/noto-sans-jp/400.css'
import '@fontsource/noto-sans-jp/700.css'

import { api } from '../lib/api.js'

export const BUNDLED = [
  { family: 'Lora', stack: "'Lora', Georgia, serif", category: 'Book serif' },
  { family: 'Merriweather', stack: "'Merriweather', Georgia, serif", category: 'Book serif' },
  { family: 'EB Garamond', stack: "'EB Garamond', Garamond, Georgia, serif", category: 'Book serif' },
  { family: 'Literata', stack: "'Literata', Georgia, serif", category: 'Book serif' },
  { family: 'Inter', stack: "'Inter', system-ui, sans-serif", category: 'Sans' },
  { family: 'Atkinson Hyperlegible', stack: "'Atkinson Hyperlegible', system-ui, sans-serif", category: 'Accessibility' },
  { family: 'OpenDyslexic', stack: "'OpenDyslexic', system-ui, sans-serif", category: 'Accessibility' },
  { family: 'Courier Prime', stack: "'Courier Prime', 'Courier New', monospace", category: 'Typewriter' },
  { family: 'JetBrains Mono', stack: "'JetBrains Mono', Consolas, monospace", category: 'Typewriter' },
  { family: 'Bangers', stack: "'Bangers', 'Comic Sans MS', fantasy", category: 'Manga / comic' },
  { family: 'Noto Sans JP', stack: "'Noto Sans JP', 'Yu Gothic', sans-serif", category: 'Manga / comic' },
]

export let customFonts = []

export function fontStack(family) {
  const b = BUNDLED.find((f) => f.family === family)
  if (b) return b.stack
  const safe = String(family).replace(/[^A-Za-z0-9 \-']/g, '')
  return `'${safe}', Georgia, serif`
}

export async function loadCustomFonts() {
  try {
    customFonts = await api.get('/api/fonts')
  } catch {
    customFonts = []
  }
  for (const f of customFonts) {
    try {
      const face = new FontFace(f.family, `url(${f.url})`)
      await face.load()
      document.fonts.add(face)
    } catch {}
  }
  return customFonts
}
