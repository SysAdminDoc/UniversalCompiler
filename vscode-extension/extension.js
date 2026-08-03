const vscode = require("vscode");

function quote(value) {
  return `"${String(value).replaceAll('"', '\\"')}"`;
}

function activate(context) {
  const disposable = vscode.commands.registerCommand(
    "universalCompiler.buildActiveFile",
    () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showWarningMessage("Universal Compiler: no active source file.");
        return;
      }

      const config = vscode.workspace.getConfiguration("universalCompiler");
      const python = config.get("pythonPath", "python");
      const configuredScript = config.get(
        "scriptPath",
        "${workspaceFolder}/UniversalCompiler.py"
      );
      const script = vscode.workspace
        .getWorkspaceFolder(editor.document.uri)
        ?.uri.fsPath
        ? configuredScript.replace(
            "${workspaceFolder}",
            vscode.workspace.getWorkspaceFolder(editor.document.uri).uri.fsPath
          )
        : configuredScript;
      const backend = config.get("backend", "auto");
      const terminal = vscode.window.createTerminal("Universal Compiler");
      terminal.show();
      const backendArg = backend && backend !== "auto" ? ` --backend ${quote(backend)}` : "";
      terminal.sendText(
        `${quote(python)} ${quote(script)} build ${quote(editor.document.uri.fsPath)}${backendArg}`
      );
    }
  );
  context.subscriptions.push(disposable);
}

function deactivate() {}

module.exports = { activate, deactivate };
