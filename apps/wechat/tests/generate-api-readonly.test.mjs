import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { checkGeneratedSource, writeGeneratedSourceAtomically } from "../scripts/generate-api.mjs";

const root = resolve(import.meta.dirname, "..");
const scriptPath = resolve(root, "scripts/generate-api.mjs");
const outputPath = resolve(root, "miniprogram/api/generated/types.gen.ts");
const outputDirectory = resolve(outputPath, "..");

function observe(child) {
  return new Promise((resolveResult, reject) => {
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += String(chunk);
    });
    child.stderr.on("data", (chunk) => {
      stderr += String(chunk);
    });
    child.on("error", reject);
    child.on("close", (exitCode) => {
      resolveResult({ exitCode, stderr, stdout });
    });
  });
}

function spawnCli(arguments_) {
  const child = spawn(process.execPath, [scriptPath, ...arguments_], {
    cwd: root,
    env: process.env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (child.pid === undefined) throw new Error("generate-api child did not expose a PID");
  return { child, result: observe(child) };
}

function snapshotGeneratedContract() {
  const target = statSync(outputPath);
  const directory = statSync(outputDirectory);
  return {
    directory: {
      entries: readdirSync(outputDirectory).sort(),
      mode: directory.mode,
      mtimeMs: directory.mtimeMs,
    },
    target: {
      hash: createHash("sha256").update(readFileSync(outputPath)).digest("hex"),
      mode: target.mode,
      mtimeMs: target.mtimeMs,
      size: target.size,
    },
  };
}

describe("generate-api check mode repository isolation", () => {
  it("returns the exact missing, stale, and synchronized freshness statuses without writes", () => {
    const temporaryDirectory = mkdtempSync(resolve(tmpdir(), "generate-api-check-test-"));
    const generatedOutputPath = resolve(temporaryDirectory, "types.gen.ts");
    const canonical = "export type Canonical = true;\n";
    try {
      expect(checkGeneratedSource(generatedOutputPath, canonical)).toMatchObject({
        exitCode: 1,
        message: expect.stringContaining("missing"),
      });
      expect(existsSync(generatedOutputPath)).toBe(false);

      writeFileSync(generatedOutputPath, "export type Canonical = false;\n", {
        encoding: "utf8",
        flag: "wx",
      });
      const staleBefore = snapshotPath(temporaryDirectory, generatedOutputPath);
      expect(checkGeneratedSource(generatedOutputPath, canonical)).toMatchObject({
        exitCode: 1,
        message: expect.stringContaining("First difference at line 1."),
      });
      expect(snapshotPath(temporaryDirectory, generatedOutputPath)).toEqual(staleBefore);

      writeFileSync(generatedOutputPath, canonical, "utf8");
      const synchronizedBefore = snapshotPath(temporaryDirectory, generatedOutputPath);
      expect(checkGeneratedSource(generatedOutputPath, canonical)).toEqual({
        exitCode: 0,
        message: "Generated API types match the backend OpenAPI contract.\n",
      });
      expect(snapshotPath(temporaryDirectory, generatedOutputPath)).toEqual(synchronizedBefore);
    } finally {
      rmSync(temporaryDirectory, { force: true, recursive: true });
    }
  });

  it("returns 2 for invalid CLI arguments without running generation", async () => {
    const { result } = spawnCli(["--invalid"]);
    await expect(result).resolves.toMatchObject({
      exitCode: 2,
      stderr: "Usage: node scripts/generate-api.mjs [--check]\n",
      stdout: "",
    });
  });

  it("keeps PID-shaped sentinels and generated contract metadata across concurrent checks", async () => {
    const executions = [spawnCli(["--check"]), spawnCli(["--check"])];
    const sentinels = executions.map(({ child }) => `${outputPath}.${String(child.pid)}.tmp`);
    try {
      for (const [index, sentinel] of sentinels.entries()) {
        writeFileSync(sentinel, `sentinel-${String(index)}`, {
          encoding: "utf8",
          flag: "wx",
        });
      }
      const before = snapshotGeneratedContract();
      const results = await Promise.all(executions.map(({ result }) => result));

      expect(results.map(({ exitCode }) => exitCode)).toEqual([0, 0]);
      expect(snapshotGeneratedContract()).toEqual(before);
      expect(sentinels.map((sentinel) => readFileSync(sentinel, "utf8"))).toEqual([
        "sentinel-0",
        "sentinel-1",
      ]);
    } finally {
      for (const sentinel of sentinels) rmSync(sentinel, { force: true });
    }
  }, 120_000);

  it("atomically replaces generated output and removes only its owned temporary directory", () => {
    const temporaryDirectory = mkdtempSync(resolve(tmpdir(), "generate-api-write-test-"));
    const generatedDirectory = resolve(temporaryDirectory, "generated");
    const generatedOutputPath = resolve(generatedDirectory, "types.gen.ts");
    try {
      mkdirSync(generatedDirectory);
      writeFileSync(generatedOutputPath, "stale\n", { encoding: "utf8", flag: "wx" });
      writeGeneratedSourceAtomically(generatedOutputPath, "fresh\n");

      expect(readFileSync(generatedOutputPath, "utf8")).toBe("fresh\n");
      expect(readdirSync(generatedDirectory)).toEqual(["types.gen.ts"]);
    } finally {
      rmSync(temporaryDirectory, { force: true, recursive: true });
    }
  });
});

function snapshotPath(directoryPath, targetPath) {
  const target = statSync(targetPath);
  const directory = statSync(directoryPath);
  return {
    directory: {
      entries: readdirSync(directoryPath).sort(),
      mode: directory.mode,
      mtimeMs: directory.mtimeMs,
    },
    target: {
      hash: createHash("sha256").update(readFileSync(targetPath)).digest("hex"),
      mode: target.mode,
      mtimeMs: target.mtimeMs,
      size: target.size,
    },
  };
}
