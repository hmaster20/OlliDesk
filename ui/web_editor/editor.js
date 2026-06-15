/**
 * OlliDesk Editor — Monaco Editor with Python Bridge
 */

let monacoEditor = null;
let diffEditor = null;
let pythonBridge = null;
let currentFilePath = null;
let isDirty = false;
let originalContent = '';
let monacoReady = false;
let pendingFiles = [];
let requestCounter = 0;
let pendingRequests = {};

async function initEditor() {
    require.config({
        paths: { vs: 'vendor/monaco/min/vs' }
    });

    await initPythonBridge();

    require(['vs/editor/editor.main'], function() {
        monacoEditor = monaco.editor.create(document.getElementById('container'), {
            value: '// OlliDesk Editor\n// Select a file to edit',
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

        monacoEditor.onDidChangeModelContent(function() {
            isDirty = true;
            updateStatusBar('Modified');
        });

        setupKeybindings();
        monacoReady = true;
        console.log('Monaco Editor initialized');

        pendingFiles.forEach(function(fp) {
            _openFile(fp);
        });
        pendingFiles = [];
    });
}

async function initPythonBridge() {
    return new Promise(function(resolve) {
        new QWebChannel(qt.webChannelTransport, function(channel) {
            pythonBridge = channel.objects.pythonBridge;
            console.log('Python Bridge connected');
            resolve();
        });
    });
}

function readFile(path) {
    var requestId = 'r' + (requestCounter++);
    return new Promise(function(resolve, reject) {
        pendingRequests[requestId] = resolve;
        try {
            pythonBridge.read_file(requestId, path);
        } catch (e) {
            delete pendingRequests[requestId];
            reject(e);
        }
    });
}

function writeFile(path, content) {
    var requestId = 'w' + (requestCounter++);
    return new Promise(function(resolve, reject) {
        pendingRequests[requestId] = resolve;
        try {
            pythonBridge.write_file(requestId, path, content);
        } catch (e) {
            delete pendingRequests[requestId];
            reject(e);
        }
    });
}

function onFileContentReady(data) {
    var resolve = pendingRequests[data.requestId];
    if (resolve) {
        delete pendingRequests[data.requestId];
        resolve(data.content);
    }
}

function onFileWriteResult(data) {
    var resolve = pendingRequests[data.requestId];
    if (resolve) {
        delete pendingRequests[data.requestId];
        resolve(data.success);
    }
}

function openFile(filePath) {
    if (!monacoReady) {
        pendingFiles.push(filePath);
        return;
    }
    _openFile(filePath);
}

async function _openFile(filePath) {
    try {
        updateStatusBar('Loading...');
        var content = await readFile(filePath);

        if (content === null || content === undefined) {
            updateStatusBar('Error: file not found');
            return;
        }

        currentFilePath = filePath;
        originalContent = content;
        isDirty = false;

        var language = detectLanguage(filePath);
        try {
            var model = monaco.editor.createModel(content, language);
            monacoEditor.setModel(model);
        } catch (e) {
            console.warn('Failed to set language ' + language + ', fallback to plaintext:', e);
            var model = monaco.editor.createModel(content, 'plaintext');
            monacoEditor.setModel(model);
        }

        updateStatusBar(filePath);
        console.log('File opened: ' + filePath);
    } catch (error) {
        var msg = error.message || error.toString();
        updateStatusBar('Error: ' + msg);
        console.error('Error opening file:', error);
    }
}

async function saveFile() {
    if (!currentFilePath) {
        updateStatusBar('No file open');
        return;
    }

    if (!isDirty) {
        updateStatusBar('No changes');
        return;
    }

    try {
        updateStatusBar('Saving...');
        var content = monacoEditor.getValue();
        var success = await writeFile(currentFilePath, content);

        if (success) {
            originalContent = content;
            isDirty = false;
            updateStatusBar('Saved: ' + currentFilePath);
        } else {
            updateStatusBar('Error saving file');
        }
    } catch (error) {
        updateStatusBar('Error: ' + error);
        console.error('Error saving file:', error);
    }
}

function showDiff(originalText, modifiedText, language) {
    if (!language) language = 'plaintext';

    if (monacoEditor) {
        monacoEditor.dispose();
        monacoEditor = null;
    }

    diffEditor = monaco.editor.createDiffEditor(document.getElementById('container'), {
        theme: 'vs-dark',
        automaticLayout: true,
        readOnly: false,
        renderSideBySide: true,
    });

    var originalModel = monaco.editor.createModel(originalText, language);
    var modifiedModel = monaco.editor.createModel(modifiedText, language);

    diffEditor.setModel({
        original: originalModel,
        modified: modifiedModel,
    });

    updateStatusBar('Diff mode');
}

async function acceptDiff() {
    if (!diffEditor || !currentFilePath) return;

    try {
        var modifiedContent = diffEditor.getModel().modified.getValue();
        var success = await writeFile(currentFilePath, modifiedContent);

        if (success) {
            updateStatusBar('Changes applied');
            closeDiff();
            await openFile(currentFilePath);
        }
    } catch (error) {
        updateStatusBar('Error: ' + error);
    }
}

function rejectDiff() {
    updateStatusBar('Changes rejected');
    closeDiff();
    if (currentFilePath) {
        openFile(currentFilePath);
    }
}

function closeDiff() {
    if (diffEditor) {
        diffEditor.dispose();
        diffEditor = null;
    }

    monacoEditor = monaco.editor.create(document.getElementById('container'), {
        value: '',
        language: 'plaintext',
        theme: 'vs-dark',
        automaticLayout: true,
        fontSize: 14,
        minimap: { enabled: true },
    });

    setupKeybindings();
}

function setupKeybindings() {
    monacoEditor.addCommand(
        monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS,
        function() { saveFile(); }
    );
}

function detectLanguage(filePath) {
    var ext = filePath.split('.').pop().toLowerCase();
    var langMap = {
        'py': 'python',
        'js': 'javascript',
        'ts': 'typescript',
        'tsx': 'typescript',
        'jsx': 'javascript',
        'html': 'html',
        'css': 'css',
        'md': 'markdown',
        'json': 'json',
        'yaml': 'yaml',
        'yml': 'yaml',
        'xml': 'xml',
        'sql': 'sql',
        'sh': 'shell',
        'bash': 'shell',
    };
    return langMap[ext] || 'plaintext';
}

function updateStatusBar(message) {
    var statusBar = document.getElementById('status-bar');
    if (!statusBar) return;
    statusBar.textContent = message;
    statusBar.style.display = 'block';

    if (message.indexOf('Error') !== 0) {
        setTimeout(function() {
            statusBar.style.display = 'none';
        }, 3000);
    }
}

function getContent() {
    if (monacoEditor) {
        return monacoEditor.getValue();
    }
    if (diffEditor) {
        return diffEditor.getModel().modified.getValue();
    }
    return '';
}

function setContent(content, language) {
    if (!language) language = 'plaintext';
    if (monacoEditor) {
        var model = monaco.editor.createModel(content, language);
        monacoEditor.setModel(model);
    }
}
