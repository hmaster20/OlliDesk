/**
 * OlliDesk Editor — Monaco Editor с несколькими моделями (вкладками).
 */

let monacoEditor = null;
let diffEditor = null;
let pythonBridge = null;
let currentFilePath = null;
let monacoReady = false;
let pendingFiles = [];
let requestCounter = 0;
let pendingRequests = {};

// path -> { model, viewState, originalContent, isDirty }
const fileModels = new Map();

async function initEditor() {
    require.config({ paths: { vs: 'vendor/monaco/min/vs' } });
    await initPythonBridge();

    require(['vs/editor/editor.main'], function () {
        monacoEditor = monaco.editor.create(document.getElementById('container'), {
            value: '',
            language: 'plaintext',
            theme: 'vs-dark',
            automaticLayout: true,
            fontSize: 14,
            minimap: { enabled: true },
            scrollBeyondLastLine: false,
            renderWhitespace: 'selection',
            tabSize: 4,
            insertSpaces: true,
        });

        monacoEditor.onDidChangeModelContent(function () {
            const entry = fileModels.get(currentFilePath);
            if (!entry) return;
            const dirty = monacoEditor.getValue() !== entry.originalContent;
            if (dirty !== entry.isDirty) {
                entry.isDirty = dirty;
                if (pythonBridge && pythonBridge.set_dirty) {
                    pythonBridge.set_dirty(currentFilePath, dirty);
                }
            }
        });

        setupKeybindings();
        monacoReady = true;
        console.log('Monaco Editor initialized');

        pendingFiles.forEach(item => _openFile(item.filePath, item.line));
        pendingFiles = [];
    });
}

async function initPythonBridge() {
    return new Promise(resolve => {
        new QWebChannel(qt.webChannelTransport, function (channel) {
            pythonBridge = channel.objects.pythonBridge;
            console.log('Python Bridge connected');
            resolve();
        });
    });
}

// ---------- File I/O через Python ----------

function readFile(path) {
    const requestId = 'r' + (requestCounter++);
    return new Promise((resolve, reject) => {
        pendingRequests[requestId] = resolve;
        try { pythonBridge.read_file(requestId, path); }
        catch (e) { delete pendingRequests[requestId]; reject(e); }
    });
}

function writeFile(path, content) {
    const requestId = 'w' + (requestCounter++);
    return new Promise((resolve, reject) => {
        pendingRequests[requestId] = resolve;
        try { pythonBridge.write_file(requestId, path, content); }
        catch (e) { delete pendingRequests[requestId]; reject(e); }
    });
}

function onFileContentReady(data) {
    const resolve = pendingRequests[data.requestId];
    if (resolve) { delete pendingRequests[data.requestId]; resolve(data.content); }
}

function onFileWriteResult(data) {
    const resolve = pendingRequests[data.requestId];
    if (resolve) { delete pendingRequests[data.requestId]; resolve(data.success); }
}

// ---------- Многомодельный режим ----------

function openFile(filePath, line) {
    if (!monacoReady) {
        pendingFiles.push({ filePath, line: line || 0 });
        return;
    }
    _openFile(filePath, line);
}

async function _openFile(filePath, line) {
    try {
        let entry = fileModels.get(filePath);

        if (!entry) {
            updateStatusBar('Loading...');
            const content = await readFile(filePath);
            if (content === null || content === undefined) {
                updateStatusBar('Error: file not found');
                return;
            }
            const language = detectLanguage(filePath);
            let model;
            try {
                model = monaco.editor.createModel(content, language);
            } catch (e) {
                console.warn('Language "' + language + '" unknown, plaintext:', e);
                model = monaco.editor.createModel(content, 'plaintext');
            }
            entry = { model, viewState: null, originalContent: content, isDirty: false };
            fileModels.set(filePath, entry);
        }

        // Сохраняем view state предыдущей модели.
        if (monacoEditor && currentFilePath) {
            const prev = fileModels.get(currentFilePath);
            if (prev) prev.viewState = monacoEditor.saveViewState();
        }

        monacoEditor.setModel(entry.model);
        currentFilePath = filePath;

        if (entry.viewState) monacoEditor.restoreViewState(entry.viewState);
        if (line && line > 0) {
            monacoEditor.revealLineInCenter(line);
            monacoEditor.setPosition({ lineNumber: line, column: 1 });
        }
        monacoEditor.focus();
        updateStatusBar(filePath);
        console.log('File opened: ' + filePath);
    } catch (error) {
        const msg = error.message || error.toString();
        updateStatusBar('Error: ' + msg);
        console.error('Error opening file:', error);
    }
}

function closeFile(filePath) {
    const entry = fileModels.get(filePath);
    if (!entry) return;
    if (entry.model) entry.model.dispose();
    fileModels.delete(filePath);
    if (currentFilePath === filePath) currentFilePath = null;
}

function renameOpenFile(oldPath, newPath) {
    const entry = fileModels.get(oldPath);
    if (!entry) return;
    fileModels.delete(oldPath);
    fileModels.set(newPath, entry);
    if (currentFilePath === oldPath) currentFilePath = newPath;
    console.log('Renamed in editor: ' + oldPath + ' -> ' + newPath);
}

async function saveFile() {
    if (!currentFilePath) { updateStatusBar('No file open'); return; }
    const entry = fileModels.get(currentFilePath);
    if (!entry) return;
    if (!entry.isDirty) { updateStatusBar('No changes'); return; }

    try {
        updateStatusBar('Saving...');
        const content = monacoEditor.getValue();
        const success = await writeFile(currentFilePath, content);
        if (success) {
            entry.originalContent = content;
            entry.isDirty = false;
            if (pythonBridge && pythonBridge.set_dirty) {
                pythonBridge.set_dirty(currentFilePath, false);
            }
            updateStatusBar('Saved: ' + currentFilePath);
        } else {
            updateStatusBar('Error saving file');
        }
    } catch (error) {
        updateStatusBar('Error: ' + error);
        console.error('Error saving file:', error);
    }
}

// ---------- Diff ----------

function showDiff(originalText, modifiedText, language) {
    if (!language) language = 'plaintext';
    if (monacoEditor) { monacoEditor.dispose(); monacoEditor = null; }

    diffEditor = monaco.editor.createDiffEditor(document.getElementById('container'), {
        theme: 'vs-dark',
        automaticLayout: true,
        readOnly: false,
        renderSideBySide: true,
    });

    const originalModel = monaco.editor.createModel(originalText, language);
    const modifiedModel = monaco.editor.createModel(modifiedText, language);
    diffEditor.setModel({ original: originalModel, modified: modifiedModel });
    updateStatusBar('Diff mode');
}

async function acceptDiff() {
    if (!diffEditor || !currentFilePath) return;
    try {
        const modifiedContent = diffEditor.getModel().modified.getValue();
        const success = await writeFile(currentFilePath, modifiedContent);
        if (success) {
            updateStatusBar('Changes applied');
            const path = currentFilePath;
            const entry = fileModels.get(path);
            if (entry) {
                entry.model.setValue(modifiedContent);
                entry.originalContent = modifiedContent;
                entry.isDirty = false;
            }
            closeDiff();
            openFile(path);
        }
    } catch (error) {
        updateStatusBar('Error: ' + error);
    }
}

function rejectDiff() {
    updateStatusBar('Changes rejected');
    const path = currentFilePath;
    closeDiff();
    if (path) openFile(path);
}

function closeDiff() {
    if (diffEditor) { diffEditor.dispose(); diffEditor = null; }
    monacoEditor = monaco.editor.create(document.getElementById('container'), {
        value: '',
        language: 'plaintext',
        theme: 'vs-dark',
        automaticLayout: true,
        fontSize: 14,
        minimap: { enabled: true },
    });
    setupKeybindings();
    if (currentFilePath) {
        const entry = fileModels.get(currentFilePath);
        if (entry) monacoEditor.setModel(entry.model);
    }
}

function setupKeybindings() {
    monacoEditor.addCommand(
        monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS,
        function () { saveFile(); }
    );
}

// ---------- Определение языка ----------

function detectLanguage(filePath) {
    const name = filePath.split(/[/\\]/).pop().toLowerCase();
    const dotIdx = name.lastIndexOf('.');
    const ext = dotIdx >= 0 ? name.substring(dotIdx + 1) : '';

    const langMap = {
        'py': 'python', 'pyi': 'python', 'pyw': 'python',
        'js': 'javascript', 'mjs': 'javascript', 'cjs': 'javascript', 'jsx': 'javascript',
        'ts': 'typescript', 'tsx': 'typescript',
        'html': 'html', 'htm': 'html',
        'css': 'css', 'scss': 'scss', 'sass': 'scss', 'less': 'less',
        'vue': 'html', 'svelte': 'html',
        'json': 'json', 'jsonc': 'json',
        'yaml': 'yaml', 'yml': 'yaml',
        'toml': 'ini', 'ini': 'ini', 'cfg': 'ini',
        'xml': 'xml', 'svg': 'xml', 'xaml': 'xml',
        'md': 'markdown', 'markdown': 'markdown',
        'sh': 'shell', 'bash': 'shell', 'zsh': 'shell',
        'ps1': 'powershell', 'psm1': 'powershell',
        'bat': 'bat', 'cmd': 'bat',
        'c': 'c', 'h': 'c',
        'cpp': 'cpp', 'cxx': 'cpp', 'cc': 'cpp',
        'hpp': 'cpp', 'hh': 'cpp', 'hxx': 'cpp',
        'cs': 'csharp', 'csproj': 'xml',
        'java': 'java', 'kt': 'kotlin', 'kts': 'kotlin',
        'go': 'go', 'rs': 'rust', 'rb': 'ruby', 'php': 'php',
        'pl': 'perl', 'lua': 'lua', 'r': 'r', 'dart': 'dart',
        'swift': 'swift', 'm': 'objective-c', 'mm': 'objective-c',
        'scala': 'scala', 'groovy': 'groovy', 'sql': 'sql',
    };

    if (name === 'dockerfile' || name.startsWith('dockerfile.')) return 'dockerfile';
    if (name === 'makefile' || name === 'gnumakefile') return 'makefile';
    if (name === '.env' || name.startsWith('.env.')) return 'ini';

    return langMap[ext] || 'plaintext';
}

// ---------- Status bar ----------

function updateStatusBar(message) {
    const statusBar = document.getElementById('status-bar');
    if (!statusBar) return;
    statusBar.textContent = message;
    statusBar.style.display = 'block';
    if (message.indexOf('Error') !== 0) {
        setTimeout(function () { statusBar.style.display = 'none'; }, 3000);
    }
}

function getContent() {
    if (monacoEditor) return monacoEditor.getValue();
    if (diffEditor) return diffEditor.getModel().modified.getValue();
    return '';
}

function setContent(content, language) {
    if (!language) language = 'plaintext';
    if (monacoEditor) {
        const model = monaco.editor.createModel(content, language);
        monacoEditor.setModel(model);
    }
}