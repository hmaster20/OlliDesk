/**
 * OlliDesk Editor — Monaco Editor with Python Bridge
 */

let monacoEditor = null;
let diffEditor = null;
let pythonBridge = null;
let currentFilePath = null;
let isDirty = false;
let originalContent = '';

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

        monacoEditor.onDidChangeModelContent(() => {
            isDirty = true;
            updateStatusBar('Modified');
        });

        setupKeybindings();

        console.log('Monaco Editor initialized');
    });
}

async function initPythonBridge() {
    return new Promise((resolve) => {
        new QWebChannel(qt.webChannelTransport, (channel) => {
            pythonBridge = channel.objects.pythonBridge;
            console.log('Python Bridge connected');
            resolve();
        });
    });
}

async function callPython(method, ...args) {
    if (!pythonBridge) {
        console.error('Python Bridge not initialized');
        return null;
    }

    return new Promise((resolve) => {
        pythonBridge[method](...args, (result) => {
            resolve(result);
        });
    });
}

async function openFile(filePath) {
    try {
        updateStatusBar('Loading...');
        const content = await callPython('read_file', filePath);

        if (content === null || content === undefined) {
            updateStatusBar('Error: file not found');
            return;
        }

        currentFilePath = filePath;
        originalContent = content;
        isDirty = false;

        const language = detectLanguage(filePath);
        const model = monaco.editor.createModel(content, language);
        monacoEditor.setModel(model);

        updateStatusBar(filePath);
        console.log('File opened: ' + filePath);
    } catch (error) {
        updateStatusBar('Error: ' + error);
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
        const content = monacoEditor.getValue();
        const success = await callPython('write_file', currentFilePath, content);

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

    const originalModel = monaco.editor.createModel(originalText, language);
    const modifiedModel = monaco.editor.createModel(modifiedText, language);

    diffEditor.setModel({
        original: originalModel,
        modified: modifiedModel,
    });

    updateStatusBar('Diff mode');
}

async function acceptDiff() {
    if (!diffEditor || !currentFilePath) return;

    try {
        const modifiedContent = diffEditor.getModel().modified.getValue();
        const success = await callPython('write_file', currentFilePath, modifiedContent);

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
        () => saveFile()
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

    if (!message.startsWith('Error')) {
        setTimeout(function () {
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
