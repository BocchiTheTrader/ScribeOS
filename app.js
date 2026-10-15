/*
======================================================================================
  Brand     : BocchiTheDev | Creative Writing & Narrative Architecture Labs
  App       : ScribeOS - Minimalist Deep-Dark Story Studio
  Security  : Strict XSS sanitization via DOMPurify
  Files     : 3 of 3 (app.js)
======================================================================================
*/

const DEFAULT_STATE = {
    activeProjectId: 'p1',
    projects: [{
        id: 'p1',
        name: 'Project A: Cyberpunk Novel',
        activeChapterId: 'c1',
        chapters: [{ id: 'c1', title: 'Chapter 1: The Neon Rain', content: '', wordCount: 0 }],
        notes: []
    }],
    settings: {
        fontFamily: 'Lora, serif',
        fontSize: 18,
        lineHeight: 1.6,
        contentWidth: 800,
        zenMode: false,
        typewriter: false,
        dailyGoal: 1000,
        dailyProgress: { date: new Date().toDateString(), count: 0, startWords: 0 }
    }
};

const TEMPLATES = {
    monomyth: `<h2>The Hero's Journey</h2><hr><h3>1. The Ordinary World</h3><p>[Describe the hero's flawed, normal life]</p><h3>2. Call to Adventure</h3><p>[The inciting incident]</p><h3>3. Refusal of the Call</h3><p>[Fear or reluctance]</p><h3>4. Meeting the Mentor</h3><p>[Guidance is given]</p><h3>5. Crossing the Threshold</h3><p>[Entering the unknown]</p>`,
    threeAct: `<h2>Three-Act Structure</h2><hr><h3>Act I: Setup</h3><p><strong>Inciting Incident:</strong> [What changes everything?]</p><h3>Act II: Confrontation</h3><p><strong>Midpoint:</strong> [The false victory/defeat]</p><h3>Act III: Resolution</h3><p><strong>Climax:</strong> [The final battle/choice]</p>`,
    saveCat: `<h2>Save the Cat! Beat Sheet</h2><hr><p><strong>Opening Image:</strong> [Visual representation of the struggle]</p><p><strong>Theme Stated:</strong> [What is the story about?]</p><p><strong>Catalyst:</strong> [The inciting incident]</p><p><strong>Break into Two:</strong> [Proactive choice to enter the new world]</p><p><strong>Dark Night of the Soul:</strong> [Lowest point]</p>`,
    character: `<h2>Character Dossier</h2><hr><ul><li><strong>Name:</strong> [Name]</li><li><strong>Archetype:</strong> [e.g., The Rebel]</li><li><strong>Fatal Flaw:</strong> [Internal weakness]</li><li><strong>Core Desire:</strong> [What they think they want]</li><li><strong>Need:</strong> [What they actually need]</li><li><strong>Physical Quirks:</strong> [Habits/Scars]</li></ul>`,
    world: `<h2>Worldbuilding Codex</h2><hr><h3>Magic / Tech System</h3><p>[Rules, costs, and limitations]</p><h3>Factions & Politics</h3><p>[Who has the power?]</p><h3>Geography</h3><p>[Key locations]</p>`
};

const sanitize = (dirty) => DOMPurify.sanitize(dirty, { USE_PROFILES: { html: true } });
const sanitizeText = (dirty) => DOMPurify.sanitize(dirty, { ALLOWED_TAGS: [] });

let state = JSON.parse(localStorage.getItem('scribeos_state')) || DEFAULT_STATE;

const today = new Date().toDateString();
if (state.settings.dailyProgress.date !== today) {
    state.settings.dailyProgress = { date: today, count: 0, startWords: getTotalWordCount() };
}

function saveState() {
    localStorage.setItem('scribeos_state', JSON.stringify(state));
    updateTelemetry();
}

function debounce(func, wait) {
    let timeout;
    return function (...args) {
        clearTimeout(timeout);
        timeout = setTimeout(() => func(...args), wait);
    };
}
const debouncedSave = debounce(saveState, 500);

function getActiveProject() { return state.projects.find(p => p.id === state.activeProjectId) || state.projects[0]; }
function getActiveChapter() { 
    const p = getActiveProject();
    return p.chapters.find(c => c.id === p.activeChapterId) || p.chapters[0]; 
}

const elements = {
    projectSelect: document.getElementById('projectSelect'),
    btnNewProject: document.getElementById('btnNewProject'),
    btnDeleteProject: document.getElementById('btnDeleteProject'),
    chapterTree: document.getElementById('chapterTree'),
    btnNewChapter: document.getElementById('btnNewChapter'),
    editor: document.getElementById('editor'),
    chapterTitleDisplay: document.getElementById('chapterTitleDisplay'),
    btnSettings: document.getElementById('btnSettings'),
    btnNotesToggle: document.getElementById('btnNotesToggle'),
    notesLayer: document.getElementById('notesLayer'),
    btnAddNoteGlobal: document.getElementById('btnAddNoteGlobal'),
    btnZenMode: document.getElementById('btnZenMode'),
    btnTypewriter: document.getElementById('btnTypewriter'),
    btnTemplates: document.getElementById('btnTemplates')
};

function init() {
    applySettings();
    renderProjects();
    renderChapters();
    loadChapterContent();
    renderNotes();
    updateTelemetry();
    setupEventListeners();
}

function applySettings() {
    const s = state.settings;
    document.documentElement.style.setProperty('--editor-font', sanitizeText(s.fontFamily));
    document.documentElement.style.setProperty('--editor-size', parseInt(s.fontSize) + 'px');
    document.documentElement.style.setProperty('--editor-lh', parseFloat(s.lineHeight));
    document.documentElement.style.setProperty('--editor-width', parseInt(s.contentWidth) + 'px');
    
    document.body.classList.toggle('zen-mode', s.zenMode);
    elements.btnTypewriter.classList.toggle('active', s.typewriter);
}

function renderProjects() {
    elements.projectSelect.innerHTML = state.projects.map(p => 
        `<option value="${sanitizeText(p.id)}" ${p.id === state.activeProjectId ? 'selected' : ''}>${sanitizeText(p.name)}</option>`
    ).join('');
}

function renderChapters() {
    const p = getActiveProject();
    elements.chapterTree.innerHTML = p.chapters.map(c => `
        <li class="chapter-item ${c.id === p.activeChapterId ? 'active' : ''}" data-id="${sanitizeText(c.id)}">
            <div class="chapter-info" onclick="switchChapter('${sanitizeText(c.id)}')">
                <i class="ph ph-file-text"></i>
                <span class="chapter-title">${sanitizeText(c.title)}</span>
                <span class="chapter-wordcount">${parseInt(c.wordCount) || 0}</span>
            </div>
            <div class="chapter-controls">
                <button onclick="moveChapter('${sanitizeText(c.id)}', -1)" title="Move Up"><i class="ph ph-caret-up"></i></button>
                <button onclick="moveChapter('${sanitizeText(c.id)}', 1)" title="Move Down"><i class="ph ph-caret-down"></i></button>
                <button onclick="deleteChapter('${sanitizeText(c.id)}')" title="Delete"><i class="ph ph-trash"></i></button>
            </div>
        </li>
    `).join('');
}

function loadChapterContent() {
    const c = getActiveChapter();
    if(c) {
        elements.chapterTitleDisplay.textContent = c.title; 
        elements.editor.innerHTML = sanitize(c.content);
    } else {
        elements.chapterTitleDisplay.textContent = '';
        elements.editor.innerHTML = '';
    }
}

window.switchChapter = function(id) {
    const current = getActiveChapter();
    if(current) current.content = sanitize(elements.editor.innerHTML);
    getActiveProject().activeChapterId = id;
    saveState(); renderChapters(); loadChapterContent();
};

window.moveChapter = function(id, direction) {
    const p = getActiveProject();
    const idx = p.chapters.findIndex(c => c.id === id);
    if (idx < 0 || idx + direction < 0 || idx + direction >= p.chapters.length) return;
    [p.chapters[idx], p.chapters[idx + direction]] = [p.chapters[idx + direction], p.chapters[idx]];
    saveState(); renderChapters();
};

window.deleteChapter = function(id) {
    if(!confirm("Erase this chapter permanently?")) return;
    const p = getActiveProject();
    if (p.chapters.length === 1) return alert("Cannot delete the last chapter.");
    p.chapters = p.chapters.filter(c => c.id !== id);
    if (p.activeChapterId === id) p.activeChapterId = p.chapters[0].id;
    saveState(); renderChapters(); loadChapterContent();
};

elements.editor.addEventListener('input', () => {
    const c = getActiveChapter();
    if(!c) return;
    c.content = sanitize(elements.editor.innerHTML);
    c.wordCount = countWords(elements.editor.innerText);
    
    const totalNow = getTotalWordCount();
    state.settings.dailyProgress.count = Math.max(0, totalNow - state.settings.dailyProgress.startWords);
    
    debouncedSave();
    document.getElementById('hudWords').textContent = totalNow;
    document.getElementById('hudChars').textContent = elements.editor.innerText.length;
    updateDailyGoalUI();
    
    if(state.settings.typewriter) maintainTypewriterFocus();
});

elements.chapterTitleDisplay.addEventListener('input', () => {
    const c = getActiveChapter();
    if(c) {
        c.title = sanitizeText(elements.chapterTitleDisplay.textContent) || 'Untitled';
        debouncedSave();
        const activeNode = document.querySelector('.chapter-item.active .chapter-title');
        if(activeNode) activeNode.textContent = c.title;
    }
});

function maintainTypewriterFocus() {
    const sel = window.getSelection();
    if (!sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    const rect = range.getBoundingClientRect();
    if(rect.top === 0 && rect.bottom === 0) return; 
    
    const targetY = window.innerHeight / 2;
    const offset = rect.top - targetY;
    
    if (Math.abs(offset) > 50) window.scrollBy({ top: offset, behavior: 'smooth' });
}

window.formatDoc = function(cmd, value=null) {
    document.execCommand(cmd, false, value);
    elements.editor.focus();
};

document.getElementById('btnInsertImage').addEventListener('click', () => {
    document.getElementById('imageUpload').click();
});
document.getElementById('imageUpload').addEventListener('change', function(e) {
    const file = e.target.files[0];
    if(!file) return;
    if(file.size > 2 * 1024 * 1024) return alert("File too large. Please keep images under 2MB for local storage.");
    
    const reader = new FileReader();
    reader.onload = function(event) {
        formatDoc('insertImage', sanitize(event.target.result));
    };
    reader.readAsDataURL(file);
    this.value = '';
});

function renderNotes() {
    document.querySelectorAll('.sticky-note').forEach(e => e.remove());
    const p = getActiveProject();
    
    p.notes.forEach(note => {
        const div = document.createElement('div');
        div.className = `sticky-note ${sanitizeText(note.color)}`;
        div.style.left = parseInt(note.x) + 'px';
        div.style.top = parseInt(note.y) + 'px';
        div.innerHTML = `
            <div class="note-header">
                <div class="note-tools">
                    <button class="color-btn" data-color="yellow"><i class="ph-fill ph-circle" style="color:var(--note-yellow)"></i></button>
                    <button class="color-btn" data-color="cyan"><i class="ph-fill ph-circle" style="color:var(--note-cyan)"></i></button>
                    <button class="color-btn" data-color="purple"><i class="ph-fill ph-circle" style="color:var(--note-purple)"></i></button>
                    <button class="color-btn" data-color="green"><i class="ph-fill ph-circle" style="color:var(--note-green)"></i></button>
                </div>
                <button class="del-note"><i class="ph ph-x"></i></button>
            </div>
            <div class="note-body" contenteditable="true">${sanitize(note.text)}</div>
        `;
        
        const header = div.querySelector('.note-header');
        header.onmousedown = function(e) {
            if(e.target.closest('button')) return; 
            let shiftX = e.clientX - div.getBoundingClientRect().left;
            let shiftY = e.clientY - div.getBoundingClientRect().top;
            
            function moveAt(pageX, pageY) {
                div.style.left = pageX - shiftX + 'px';
                div.style.top = pageY - shiftY + 'px';
            }
            
            function onMouseMove(e) { moveAt(e.pageX, e.pageY); }
            document.addEventListener('mousemove', onMouseMove);
            
            document.onmouseup = function() {
                document.removeEventListener('mousemove', onMouseMove);
                document.onmouseup = null;
                note.x = parseInt(div.style.left);
                note.y = parseInt(div.style.top);
                debouncedSave();
            };
        };
        
        div.querySelector('.del-note').onclick = () => {
            p.notes = p.notes.filter(n => n.id !== note.id);
            div.remove();
            saveState();
        };
        
        div.querySelectorAll('.color-btn').forEach(btn => {
            btn.onclick = () => {
                note.color = btn.dataset.color;
                div.className = `sticky-note ${note.color}`;
                saveState();
            };
        });
        
        div.querySelector('.note-body').oninput = (e) => {
            note.text = sanitize(e.target.innerHTML);
            debouncedSave();
        };
        
        elements.notesLayer.appendChild(div);
    });
}

elements.btnAddNoteGlobal.onclick = () => {
    getActiveProject().notes.push({
        id: 'n_' + Date.now(),
        text: 'New Note...',
        color: 'yellow',
        x: window.innerWidth / 2 - 125,
        y: window.innerHeight / 2 - 100
    });
    saveState(); renderNotes();
};

elements.btnNotesToggle.onclick = () => {
    elements.notesLayer.classList.toggle('hidden');
    elements.btnNotesToggle.classList.toggle('active');
};

document.querySelectorAll('.close-modal').forEach(btn => {
    btn.onclick = (e) => e.target.closest('.modal-overlay').classList.add('hidden');
});

elements.btnTemplates.onclick = () => document.getElementById('templatesDrawer').classList.remove('hidden');

document.querySelectorAll('.template-card').forEach(card => {
    card.onclick = () => {
        formatDoc('insertHTML', TEMPLATES[card.dataset.template]);
        document.getElementById('templatesDrawer').classList.add('hidden');
    };
});

function countWords(str) {
    const matches = str.match(/\S+/g);
    return matches ? matches.length : 0;
}

function getTotalWordCount() {
    return getActiveProject().chapters.reduce((sum, c) => sum + (parseInt(c.wordCount) || 0), 0);
}

function updateDailyGoalUI() {
    const current = state.settings.dailyProgress.count;
    const target = state.settings.dailyGoal;
    document.getElementById('hudGoalCurrent').textContent = current;
    document.getElementById('hudGoalTarget').textContent = target;
    const percent = Math.min(100, (current / target) * 100);
    document.getElementById('goalProgressFill').style.width = percent + '%';
    document.getElementById('goalProgressFill').style.background = percent >= 100 ? 'var(--note-green)' : 'var(--accent)';
}

function updateTelemetry() {
    const totalWords = getTotalWordCount();
    document.getElementById('hudWords').textContent = totalWords;
    document.getElementById('hudChars').textContent = elements.editor.innerText.length;
    document.getElementById('hudPages').textContent = Math.round(totalWords / 250);
    document.getElementById('hudReadTime').textContent = Math.ceil(totalWords / 200);
    updateDailyGoalUI();
}

elements.btnSettings.onclick = () => {
    const s = state.settings;
    document.getElementById('set-fontFamily').value = s.fontFamily;
    document.getElementById('set-fontSize').value = s.fontSize;
    document.getElementById('val-fontSize').textContent = s.fontSize;
    document.getElementById('set-lineHeight').value = s.lineHeight;
    document.getElementById('val-lineHeight').textContent = s.lineHeight;
    document.getElementById('set-contentWidth').value = s.contentWidth;
    document.getElementById('val-contentWidth').textContent = s.contentWidth;
    document.getElementById('set-dailyGoal').value = s.dailyGoal;
    document.getElementById('settingsDrawer').classList.remove('hidden');
};

document.querySelectorAll('.setting-group input, .setting-group select').forEach(input => {
    input.addEventListener('input', (e) => {
        const id = e.target.id;
        const val = sanitizeText(e.target.value);
        const s = state.settings;
        
        if(id === 'set-fontFamily') s.fontFamily = val;
        if(id === 'set-fontSize') { s.fontSize = val; document.getElementById('val-fontSize').textContent = val; }
        if(id === 'set-lineHeight') { s.lineHeight = val; document.getElementById('val-lineHeight').textContent = val; }
        if(id === 'set-contentWidth') { s.contentWidth = val; document.getElementById('val-contentWidth').textContent = val; }
        if(id === 'set-dailyGoal') s.dailyGoal = parseInt(val) || 1000;
        
        applySettings(); debouncedSave();
    });
});

elements.btnZenMode.onclick = () => {
    state.settings.zenMode = !state.settings.zenMode;
    applySettings(); saveState();
};

elements.btnTypewriter.onclick = () => {
    state.settings.typewriter = !state.settings.typewriter;
    applySettings(); saveState();
};

elements.projectSelect.onchange = (e) => {
    state.activeProjectId = sanitizeText(e.target.value);
    saveState(); renderChapters(); loadChapterContent(); renderNotes();
};

elements.btnNewProject.onclick = () => {
    const name = prompt("Enter new project name:");
    if(!name) return;
    const newP = {
        id: 'p_' + Date.now(), name: sanitizeText(name),
        activeChapterId: 'c_default',
        chapters: [{ id: 'c_default', title: 'Chapter 1', content: '', wordCount: 0 }],
        notes: []
    };
    state.projects.push(newP);
    state.activeProjectId = newP.id;
    saveState(); renderProjects(); renderChapters(); loadChapterContent(); renderNotes();
};

elements.btnDeleteProject.onclick = () => {
    if (state.projects.length <= 1) {
        return alert("Cannot delete the only project. Please create a new project first.");
    }
    if (confirm(`Are you sure you want to permanently delete "${getActiveProject().name}"?`)) {
        state.projects = state.projects.filter(p => p.id !== state.activeProjectId);
        state.activeProjectId = state.projects[0].id;
        saveState(); renderProjects(); renderChapters(); loadChapterContent(); renderNotes();
    }
};

elements.btnNewChapter.onclick = () => {
    const p = getActiveProject();
    const newC = { id: 'c_' + Date.now(), title: 'New Chapter', content: '', wordCount: 0 };
    p.chapters.push(newC);
    p.activeChapterId = newC.id;
    saveState(); renderChapters(); loadChapterContent();
};

document.getElementById('btnExportMd').onclick = () => {
    let html = elements.editor.innerHTML;
    let md = html.replace(/<h1>(.*?)<\/h1>/gi, '# $1\n\n')
                 .replace(/<h2>(.*?)<\/h2>/gi, '## $1\n\n')
                 .replace(/<h3>(.*?)<\/h3>/gi, '### $1\n\n')
                 .replace(/<b>(.*?)<\/b>|<strong>(.*?)<\/strong>/gi, '**$1$2**')
                 .replace(/<i>(.*?)<\/i>|<em>(.*?)<\/em>/gi, '*$1$2*')
                 .replace(/<br\s*[\/]?>/gi, '\n')
                 .replace(/<p>(.*?)<\/p>/gi, '$1\n\n')
                 .replace(/<[^>]+>/g, ''); 
    
    const blob = new Blob([`# ${getActiveChapter().title}\n\n${md}`], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `${getActiveChapter().title.replace(/[^a-z0-9]/gi, '_')}.md`; a.click();
};

document.getElementById('btnExportPdf').onclick = () => {
    const chapter = getActiveChapter();
    const contentToPrint = document.createElement('div');
    contentToPrint.style.padding = '40px';
    contentToPrint.style.fontFamily = state.settings.fontFamily;
    contentToPrint.style.fontSize = '12pt';
    contentToPrint.style.lineHeight = '1.6';
    contentToPrint.style.color = '#000';
    contentToPrint.style.background = '#fff';
    
    contentToPrint.innerHTML = `<h1 style="text-align:center; font-family:'Inter', sans-serif; margin-bottom: 30px;">${chapter.title}</h1>${chapter.content}`;
    
    const opt = {
        margin:       1,
        filename:     `${chapter.title.replace(/[^a-z0-9]/gi, '_')}.pdf`,
        image:        { type: 'jpeg', quality: 0.98 },
        html2canvas:  { scale: 2 },
        jsPDF:        { unit: 'in', format: 'letter', orientation: 'portrait' }
    };
    html2pdf().set(opt).from(contentToPrint).save();
};

document.getElementById('btnBackup').onclick = () => {
    const blob = new Blob([JSON.stringify(state, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `ScribeOS_Backup_${new Date().toISOString().slice(0,10)}.json`; a.click();
};

document.getElementById('btnRestoreData').onclick = () => document.getElementById('jsonUpload').click();
document.getElementById('jsonUpload').onchange = (e) => {
    const file = e.target.files[0];
    if(!file) return;
    const reader = new FileReader();
    reader.onload = (event) => {
        try {
            const newState = JSON.parse(event.target.result);
            if(newState.projects && newState.settings) {
                state = newState;
                saveState(); location.reload();
            } else alert("Invalid Backup File format.");
        } catch(e) { alert("Error parsing JSON. File might be corrupted."); }
    };
    reader.readAsText(file);
};

document.getElementById('btnDangerWipe').onclick = () => {
    if(prompt("Type 'DELETE' to permanently wipe all ScribeOS data:") === 'DELETE') {
        localStorage.removeItem('scribeos_state');
        location.reload();
    }
};

function setupEventListeners() {
    document.addEventListener('keydown', (e) => {
        if(e.altKey && e.key.toLowerCase() === 'n') {
            e.preventDefault(); elements.btnNotesToggle.click();
        }
    });
}

init();