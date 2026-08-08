const vscode = require("vscode");
const path = require("path");
const { spawn } = require("child_process");

const INHERITED_ENVIRONMENT = [
  "APPDATA",
  "COMSPEC",
  "HOME",
  "HOMEDRIVE",
  "HOMEPATH",
  "LOCALAPPDATA",
  "NUMBER_OF_PROCESSORS",
  "PATH",
  "PATHEXT",
  "PROCESSOR_ARCHITECTURE",
  "PROGRAMDATA",
  "PROGRAMFILES",
  "PROGRAMFILES(X86)",
  "SYSTEMDRIVE",
  "SYSTEMROOT",
  "TEMP",
  "TMP",
  "USERDOMAIN",
  "USERNAME",
  "USERPROFILE",
  "VIRTUAL_ENV",
  "WINDIR",
];

function restrictedEnvironment() {
  const environment = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (INHERITED_ENVIRONMENT.includes(key.toUpperCase())) {
      environment[key] = value;
    }
  }
  environment.PYTHONUNBUFFERED = "1";
  return environment;
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
      if (!vscode.workspace.isTrusted) {
        vscode.window.showWarningMessage(
          "Universal Compiler: trust the workspace before building."
        );
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
      const workspace = vscode.workspace.getWorkspaceFolder(editor.document.uri);
      const cwd = workspace?.uri.fsPath || path.dirname(script);
      const args = [script, "build", editor.document.uri.fsPath];
      if (backend && backend !== "auto") {
        args.push("--backend", String(backend));
      }
      args.push("--no-analytics");

      const output = vscode.window.createOutputChannel("Universal Compiler");
      const child = spawn(String(python), args, {
        cwd,
        env: restrictedEnvironment(),
        shell: false,
        windowsHide: true,
      });
      output.appendLine(`$ ${String(python)} ${args.join(" ")}`);
      child.stdout.on("data", (data) => output.append(data.toString()));
      child.stderr.on("data", (data) => output.append(data.toString()));
      child.on("error", (error) => {
        output.appendLine(`Universal Compiler failed to start: ${error.message}`);
      });
      child.on("close", (code, signal) => {
        output.appendLine(
          `Universal Compiler finished with ${signal ? `signal ${signal}` : `exit code ${code}`}.`
        );
      });
    }
  );
  context.subscriptions.push(disposable);
}

function deactivate() {}

module.exports = { activate, deactivate };
