"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const path = require("node:path");
const test = require("node:test");

const fakeVscode = {
  Diagnostic: class Diagnostic {
    constructor(range, message, severity) {
      this.range = range;
      this.message = message;
      this.severity = severity;
    }
  },
  DiagnosticSeverity: { Error: 0 },
  Range: class Range {
    constructor(startLine, startCharacter, endLine, endCharacter) {
      this.start = { line: startLine, character: startCharacter };
      this.end = { line: endLine, character: endCharacter };
    }
  },
};

const originalLoad = Module._load;
Module._load = function load(request, parent, isMain) {
  if (request === "vscode") {
    return fakeVscode;
  }
  return originalLoad.call(this, request, parent, isMain);
};
const extension = require("../extension");
Module._load = originalLoad;

test("build arguments remain argv-based and carry explicit settings", () => {
  assert.deepEqual(
    extension.buildArguments("UniversalCompiler.py", "C:\\work\\main.py", {
      backend: "bun",
      profile: "Release",
      target: "linux",
      architecture: "x64",
      verify: false,
      diagnosticsPath: "C:\\logs\\uc.jsonl",
      recordDiagnostics: true,
      adapters: ["vendor.backend"],
    }),
    [
      "UniversalCompiler.py",
      "build",
      "C:\\work\\main.py",
      "--backend",
      "bun",
      "--profile",
      "Release",
      "--target",
      "linux",
      "--architecture",
      "x64",
      "--no-verify",
      "--diagnostics-path",
      "C:\\logs\\uc.jsonl",
      "--adapter",
      "vendor.backend",
      "--json",
      "--no-analytics",
    ]
  );
});

test("problem parsing creates source diagnostics from compiler locations", () => {
  const problems = extension.parseProblems(
    {
      success: false,
      message: "build failed",
      commands: [{ stderr: "C:\\work\\main.py:4:2: syntax error" }],
    },
    { fsPath: "C:\\work\\main.py" }
  );

  assert.equal(problems.length, 1);
  assert.equal(problems[0].range.start.line, 3);
  assert.equal(problems[0].range.start.character, 1);
  assert.equal(problems[0].message, "syntax error");
});

test("extension keeps process execution safe and avoids terminal interpolation", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "extension.js"), "utf8");
  const packageValue = JSON.parse(
    fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8")
  );

  assert.doesNotMatch(source, /terminal\.sendText/);
  assert.match(source, /shell: false/);
  assert.match(source, /onCancellationRequested/);
  assert.match(source, /withProgress/);
  assert.equal(packageValue.contributes.configuration.properties["universalCompiler.verify"].default, true);
  assert.deepEqual(
    packageValue.contributes.configuration.properties["universalCompiler.adapters"].default,
    []
  );
  assert.equal(Object.hasOwn(extension.restrictedEnvironment(), "UC_HOSTILE"), false);
});
