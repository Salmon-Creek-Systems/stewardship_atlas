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
});

async function executeQuery() {
    const resultsDiv = document.getElementById("results");
    const format = document.getElementById("format").value;
    
    try {
        const response = await fetch('/sql_query/{swalename}', {
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