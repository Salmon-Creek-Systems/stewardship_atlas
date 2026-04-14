// Global variable to store the editor instance
let editor;

// Initialize CodeMirror after DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    editor = CodeMirror.fromTextArea(document.getElementById("sql-editor"), {
        mode: "text/x-sql",
        theme: "monokai",
        lineNumbers: true,
        indentWithTabs: true,
        smartIndent: true,
        lineWrapping: true,
        matchBrackets: true,
        autofocus: true
    });

    // Set sample query
    editor.setValue(`-- Sample query to get all roads
SELECT * FROM roads
LIMIT 10;`);

    document.getElementById('nl-section').style.display = '';
});

async function generateSQL() {
    const nlInput = document.getElementById('nl-input');
    const nlSource = document.getElementById('nl-source');
    const generateBtn = document.getElementById('generate-btn');
    const naturalLanguage = nlInput.value.trim();
    if (!naturalLanguage) return;

    const atlasName = document.querySelector('.container').dataset.atlasName;
    const appUrl = document.querySelector('.container').dataset.appUrl;

    generateBtn.disabled = true;
    generateBtn.textContent = 'Generating…';
    nlSource.style.display = 'none';

    try {
        const response = await fetch(`${appUrl}/sql_generate/${atlasName}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ natural_language: naturalLanguage })
        });

        if (!response.ok) {
            const err = await response.json().catch(() => ({ detail: response.statusText }));
            throw new Error(err.detail || response.statusText);
        }

        const data = await response.json();
        editor.setValue(data.sql);
        nlSource.textContent = `Generated from: "${data.original_nl}"`;
        nlSource.className = 'nl-source';
        nlSource.style.display = '';
    } catch (error) {
        nlSource.textContent = `Error: ${error.message}`;
        nlSource.className = 'nl-source error';
        nlSource.style.display = '';
    } finally {
        generateBtn.disabled = false;
        generateBtn.textContent = 'Generate SQL';
    }
}

async function executeQuery() {
    const resultsDiv = document.getElementById("results");
    const format = document.getElementById("format").value;
    const atlasName = document.querySelector('.container').dataset.atlasName;
    const appUrl = document.querySelector('.container').dataset.appUrl;

    try {
        const response = await fetch(`${appUrl}/sql_query/${atlasName}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                query: editor.getValue(),
                return_format: format
            })
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        if (data.status === "success") {
            resultsDiv.textContent = data.result;
            resultsDiv.className = "";
        } else {
            throw new Error(data.detail || "Unknown error");
        }
    } catch (error) {
        resultsDiv.textContent = `Error: ${error.message}`;
        resultsDiv.className = "error";
    }
}
