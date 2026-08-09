const vscode = require("vscode");
const path = require("path");
const { spawn } = require("child_process");

const MAX_CAPTURE_BYTES = 2 * 1024 * 1024;
const INHERITED_ENVIRONMENT = Object.freeze([
  "APPDATA",
  "COMSPEC",
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
  "USERPROFILE",
  "VIRTUAL_ENV",
  "WINDIR",
]);

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

function appendBounded(parts, data, state) {
  const bytes = Buffer.from(data.toString(), "utf8");
  if (state.bytes >= MAX_CAPTURE_BYTES) {
    state.truncated = true;
    return;
  }
  const accepted = bytes.subarray(0, MAX_CAPTURE_BYTES - state.bytes);
  parts.push(accepted.toString("utf8"));
  state.bytes += accepted.length;
  if (accepted.length < bytes.length) {
    state.truncated = true;
  }
}

function terminateChild(child) {
  if (
    !child ||
    (child.exitCode !== null && child.exitCode !== undefined) ||
    child.killed
  ) {
    return;
  }
  try {
    if (process.platform === "win32" && child.pid) {
      const killer = spawn(
        "taskkill",
        ["/PID", String(child.pid), "/T", "/F"],
        { shell: false, windowsHide: true, stdio: "ignore" }
      );
      killer.on("error", () => {
        try {
          child.kill();
        } catch (_) {
          // The process may have exited between the checks.
        }
      });
    } else {
      child.kill("SIGTERM");
    }
  } catch (_) {
    // Cancellation is best effort if the process already exited.
  }
}

function readCapabilities(python, script, cwd, token) {
  return new Promise((resolve) => {
    const probe = spawn(String(python), [script, "list-toolchains", "--json"], {
      cwd,
      env: restrictedEnvironment(),
      shell: false,
      windowsHide: true,
    });
    const serialized = [];
    const capture = { bytes: 0, truncated: false };
    let settled = false;
    let cancellation;
    const finish = (value) => {
      if (settled) {
        return;
      }
      settled = true;
      if (cancellation) {
        cancellation.dispose();
      }
      resolve(value);
    };
    if (token) {
      cancellation = token.onCancellationRequested(() => {
        terminateChild(probe);
        finish(null);
      });
    }
    probe.stdout.on("data", (data) => appendBounded(serialized, data, capture));
    probe.on("error", () => finish(null));
    probe.on("close", (code) => {
      if (code !== 0 || capture.truncated) {
        finish(null);
        return;
      }
      try {
        finish(JSON.parse(serialized.join("")));
      } catch (_) {
        finish(null);
      }
    });
  });
}

function resolveScriptContext(editor, configuredScript) {
  const workspaceFolder = vscode.workspace.getWorkspaceFolder(editor.document.uri);
  const workspaceRoot = workspaceFolder?.uri.fsPath;
  const fallbackCwd = path.dirname(editor.document.uri.fsPath);
  const cwd = path.resolve(workspaceRoot || fallbackCwd);
  const configured = String(configuredScript || "UniversalCompiler.py").replace(
    /\$\{workspaceFolder\}/g,
    workspaceRoot || cwd
  );
  return { script: path.resolve(cwd, configured), cwd };
}

function buildArguments(script, source, settings) {
  const args = [script, "build", source];
  if (settings.backend && settings.backend !== "auto") {
    args.push("--backend", String(settings.backend));
  }
  if (settings.profile) {
    args.push("--profile", String(settings.profile));
  }
  if (settings.target && settings.target !== "native") {
    args.push("--target", String(settings.target));
  }
  if (settings.architecture && settings.architecture !== "native") {
    args.push("--architecture", String(settings.architecture));
  }
  args.push(settings.verify === false ? "--no-verify" : "--verify");
  if (settings.diagnosticsPath) {
    args.push("--diagnostics-path", String(settings.diagnosticsPath));
  }
  if (settings.recordDiagnostics === false) {
    args.push("--no-diagnostics");
  }
  for (const adapter of settings.adapters || []) {
    args.push("--adapter", String(adapter));
  }
  args.push("--json", "--no-analytics");
  return args;
}

function parseProblems(result, sourceUri) {
  const problems = [];
  const outputs = [];
  for (const command of result.commands || []) {
    if (command.stderr) {
      outputs.push(command.stderr);
    }
    if (command.stdout) {
      outputs.push(command.stdout);
    }
  }
  const lines = outputs.join("\n").split(/\r?\n/).filter(Boolean);
  const locationPattern = /^(.*?):(\d+)(?::(\d+))?\s*:\s*(.*)$/;
  for (const line of lines) {
    const match = line.match(locationPattern);
    if (!match) {
      continue;
    }
    const lineNumber = Math.max(0, Number.parseInt(match[2], 10) - 1);
    const column = Math.max(0, Number.parseInt(match[3] || "1", 10) - 1);
    problems.push(
      new vscode.Diagnostic(
        new vscode.Range(lineNumber, column, lineNumber, column),
        match[4] || line,
        vscode.DiagnosticSeverity.Error
      )
    );
  }
  if (problems.length === 0 && !result.success) {
    problems.push(
      new vscode.Diagnostic(
        new vscode.Range(0, 0, 0, 0),
        result.message || "Universal Compiler build failed",
        vscode.DiagnosticSeverity.Error
      )
    );
  }
  return problems;
}

async function buildActiveFile(editor, config, output, problemCollection, token) {
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
  if (token?.isCancellationRequested) {
    return;
  }

  const python = config.get("pythonPath", "python");
  const scriptSetting = config.get(
    "scriptPath",
    "${workspaceFolder}/UniversalCompiler.py"
  );
  const { script, cwd } = resolveScriptContext(editor, scriptSetting);
  const capabilities = await readCapabilities(python, script, cwd, token);
  if (!capabilities) {
    if (!token?.isCancellationRequested) {
      output.appendLine("Could not read the backend capability catalog.");
      vscode.window.showErrorMessage(
        "Universal Compiler: backend capability probe failed."
      );
    }
    return;
  }

  const source = editor.document.uri.fsPath;
  const extension = path.extname(source).replace(/^\./, "").toLowerCase();
  const candidates = Object.values(capabilities).filter(
    (capability) =>
      Array.isArray(capability.extensions) &&
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

  const settings = {
    backend: config.get("backend", "auto"),
    profile: config.get("profile", "Default"),
    target: config.get("target", "native"),
    architecture: config.get("architecture", "native"),
    verify: config.get("verify", true),
    diagnosticsPath: config.get("diagnosticsPath", ""),
    recordDiagnostics: config.get("recordDiagnostics", true),
    adapters: config.get("adapters", []),
  };
  if (settings.backend && settings.backend !== "auto") {
    const selected = capabilities[String(settings.backend)];
    if (
      !selected ||
      !Array.isArray(selected.extensions) ||
      !selected.extensions.includes(extension) ||
      !selected.host_supported ||
      selected.lifecycle === "deprecated"
    ) {
      output.appendLine(
        `Backend ${String(settings.backend)} is incompatible with .${extension}.`
      );
      vscode.window.showErrorMessage(
        `Universal Compiler: backend ${String(settings.backend)} is incompatible with .${extension}.`
      );
      return;
    }
  }

  const versions = candidates
    .filter((capability) => capability.verified_version)
    .map((capability) => `${capability.backend} ${capability.verified_version}`)
    .join(", ");
  output.appendLine(
    `Capability catalog: ${candidates.map((capability) => capability.backend).join(", ")}${versions ? `; versions: ${versions}` : ""}`
  );

  const args = buildArguments(script, source, settings);
  const child = spawn(String(python), args, {
    cwd,
    env: restrictedEnvironment(),
    shell: false,
    windowsHide: true,
  });
  const stdout = [];
  const stdoutCapture = { bytes: 0, truncated: false };
  const stderr = [];
  const stderrCapture = { bytes: 0, truncated: false };
  let cancelled = false;
  let settled = false;
  let cancellation;
  const finish = (code) => {
    if (settled) {
      return;
    }
    settled = true;
    if (cancellation) {
      cancellation.dispose();
    }
    return code;
  };
  if (token) {
    cancellation = token.onCancellationRequested(() => {
      cancelled = true;
      terminateChild(child);
    });
  }
  child.stdout.on("data", (data) =>
    appendBounded(stdout, data, stdoutCapture)
  );
  child.stderr.on("data", (data) =>
    appendBounded(stderr, data, stderrCapture)
  );
  child.on("error", (error) => {
    if (settled) {
      return;
    }
    output.appendLine(`Universal Compiler failed to start: ${error.message}`);
    vscode.window.showErrorMessage("Universal Compiler: failed to start the build.");
    finish(1);
  });
  return new Promise((resolve) => {
    child.on("close", (code, signal) => {
      if (settled) {
        resolve(1);
        return;
      }
      if (cancelled || token?.isCancellationRequested) {
        output.appendLine("Universal Compiler build cancelled.");
        problemCollection.delete(editor.document.uri);
        resolve(finish(130));
        return;
      }
      if (stderr.length) {
        output.append(stderr.join(""));
      }
      if (stdoutCapture.truncated || stderrCapture.truncated) {
        output.appendLine("Universal Compiler result exceeded the capture limit.");
        problemCollection.set(editor.document.uri, [
          new vscode.Diagnostic(
            new vscode.Range(0, 0, 0, 0),
            "Build result exceeded the extension capture limit",
            vscode.DiagnosticSeverity.Error
          ),
        ]);
        resolve(finish(1));
        return;
      }
      const serialized = stdout.join("");
      try {
        const result = JSON.parse(serialized);
        if (!String(result.schema_version || "").startsWith("uc.result.")) {
          throw new Error("unsupported result schema");
        }
        if (result.success) {
          problemCollection.delete(editor.document.uri);
        } else {
          problemCollection.set(
            editor.document.uri,
            parseProblems(result, editor.document.uri)
          );
        }
        output.appendLine(
          `Build ${result.success ? "succeeded" : "failed"}: ${result.message || result.status}.`
        );
        if (result.diagnostics) {
          const diagnostics = result.diagnostics;
          const artifactHashes = diagnostics.artifacts?.sha256 || {};
          output.appendLine(
            `Correlation ${result.correlation_id || diagnostics.correlation_id}; ` +
              `exit ${diagnostics.exit_classification}; ` +
              `cache ${diagnostics.cache?.status || "unknown"}.`
          );
          if (artifactHashes.output) {
            output.appendLine(`Artifact SHA-256: ${artifactHashes.output}`);
          }
        }
        if (result.success) {
          vscode.window.showInformationMessage("Universal Compiler: build succeeded.");
        } else {
          vscode.window.showErrorMessage("Universal Compiler: build failed.");
        }
        resolve(finish(result.success ? 0 : 1));
      } catch (error) {
        if (serialized) {
          output.append(serialized);
        }
        output.appendLine(`Could not parse build result: ${error.message}`);
        problemCollection.set(editor.document.uri, [
          new vscode.Diagnostic(
            new vscode.Range(0, 0, 0, 0),
            "Universal Compiler returned invalid JSON",
            vscode.DiagnosticSeverity.Error
          ),
        ]);
        resolve(finish(code || 1));
      }
      output.appendLine(
        `Universal Compiler finished with ${signal ? `signal ${signal}` : `exit code ${code}`}.`
      );
    });
  });
}

function activate(context) {
  const output = vscode.window.createOutputChannel("Universal Compiler");
  const problemCollection = vscode.languages.createDiagnosticCollection(
    "universal-compiler"
  );
  const disposable = vscode.commands.registerCommand(
    "universalCompiler.buildActiveFile",
    async () => {
      const editor = vscode.window.activeTextEditor;
      const config = vscode.workspace.getConfiguration("universalCompiler");
      return vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Window,
          cancellable: true,
          title: "Universal Compiler: building",
        },
        (_progress, token) =>
          buildActiveFile(editor, config, output, problemCollection, token)
      );
    }
  );
  context.subscriptions.push(disposable, output, problemCollection);
}

function deactivate() {}

module.exports = {
  activate,
  deactivate,
  buildArguments,
  parseProblems,
  restrictedEnvironment,
};
