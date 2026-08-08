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

function readCapabilities(python, script, cwd) {
  return new Promise((resolve) => {
    const probe = spawn(String(python), [script, "list-toolchains", "--json"], {
      cwd,
      env: restrictedEnvironment(),
      shell: false,
      windowsHide: true,
    });
    let serialized = "";
    probe.stdout.on("data", (data) => {
      serialized += data.toString();
    });
    probe.on("error", () => resolve(null));
    probe.on("close", (code) => {
      if (code !== 0) {
        resolve(null);
        return;
      }
      try {
        resolve(JSON.parse(serialized));
      } catch (error) {
        resolve(null);
      }
    });
  });
}

function activate(context) {
  const disposable = vscode.commands.registerCommand(
    "universalCompiler.buildActiveFile",
    async () => {
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
      const output = vscode.window.createOutputChannel("Universal Compiler");
      const capabilities = await readCapabilities(python, script, cwd);
      if (!capabilities) {
        output.appendLine("Could not read the backend capability catalog.");
        vscode.window.showErrorMessage(
          "Universal Compiler: backend capability probe failed."
        );
        return;
      }
      const extension = path.extname(editor.document.uri.fsPath)
        .replace(/^\./, "")
        .toLowerCase();
      const candidates = Object.values(capabilities).filter(
        (capability) =>
          capability.extensions.includes(extension) &&
          capability.host_supported &&
          capability.lifecycle !== "deprecated"
      );
      if (candidates.length === 0) {
        output.appendLine(`No active backend supports .${extension} on this host.`);
        vscode.window.showErrorMessage(
          `Universal Compiler: no active backend supports .${extension}.`
        );
        return;
      }
      if (backend && backend !== "auto") {
        const selected = capabilities[String(backend)];
        if (
          !selected ||
          !selected.extensions.includes(extension) ||
          !selected.host_supported
        ) {
          output.appendLine(
            `Backend ${String(backend)} is incompatible with .${extension}.`
          );
          vscode.window.showErrorMessage(
            `Universal Compiler: backend ${String(backend)} is incompatible with .${extension}.`
          );
          return;
        }
        if (selected.lifecycle === "deprecated") {
          output.appendLine(`Warning: ${String(backend)} is deprecated.`);
        }
      }
      const versions = candidates
        .filter((capability) => capability.verified_version)
        .map((capability) => `${capability.backend} ${capability.verified_version}`)
        .join(", ");
      output.appendLine(
        `Capability catalog: ${candidates.map((capability) => capability.backend).join(", ")}${versions ? `; versions: ${versions}` : ""}`
      );
      const args = [script, "build", editor.document.uri.fsPath];
      if (backend && backend !== "auto") {
        args.push("--backend", String(backend));
      }
      args.push("--json", "--no-analytics");

      const child = spawn(String(python), args, {
        cwd,
        env: restrictedEnvironment(),
        shell: false,
        windowsHide: true,
      });
      output.appendLine(`$ ${String(python)} ${args.join(" ")}`);
      const stdout = [];
      child.stdout.on("data", (data) => stdout.push(data.toString()));
      child.stderr.on("data", (data) => output.append(data.toString()));
      child.on("error", (error) => {
        output.appendLine(`Universal Compiler failed to start: ${error.message}`);
      });
      child.on("close", (code, signal) => {
        const serialized = stdout.join("");
        try {
          const result = JSON.parse(serialized);
          if (!String(result.schema_version || "").startsWith("uc.result.")) {
            throw new Error("unsupported result schema");
          }
          output.appendLine(
            `Build ${result.success ? "succeeded" : "failed"}: ${result.message || result.status}.`
          );
        } catch (error) {
          if (serialized) {
            output.append(serialized);
          }
          output.appendLine(`Could not parse build result: ${error.message}`);
        }
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
