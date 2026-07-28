import { execFileSync } from "node:child_process";
import {
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const root = resolve(import.meta.dirname, "..");
const repositoryRoot = resolve(root, "../..");
const backendRoot = resolve(repositoryRoot, "backend");
const outputPath = resolve(root, "miniprogram/api/generated/types.gen.ts");

function normalizeGeneratedSource(source) {
  return `${source.replaceAll("\r\n", "\n").trimEnd()}\n`;
}

function describeFirstDifference(committed, generated) {
  const committedLines = committed.split("\n");
  const generatedLines = generated.split("\n");
  const comparedLineCount = Math.max(committedLines.length, generatedLines.length);
  for (let index = 0; index < comparedLineCount; index += 1) {
    if (committedLines[index] !== generatedLines[index]) {
      return [
        `First difference at line ${String(index + 1)}.`,
        `Committed: ${JSON.stringify(committedLines[index] ?? "<end-of-file>")}`,
        `Generated: ${JSON.stringify(generatedLines[index] ?? "<end-of-file>")}`,
      ].join("\n");
    }
  }
  return "Generated and committed sources have different bytes.";
}

export function checkGeneratedSource(generatedOutputPath, generated) {
  if (!existsSync(generatedOutputPath)) {
    return {
      exitCode: 1,
      message: `Generated API types are missing at ${generatedOutputPath}. Run npm run api:generate.\n`,
    };
  }

  const committed = readFileSync(generatedOutputPath, "utf8");
  if (committed !== generated) {
    return {
      exitCode: 1,
      message: [
        "Generated API types are stale.",
        describeFirstDifference(committed, generated),
        "Run npm run api:generate and commit the result.",
        "",
      ].join("\n"),
    };
  }
  return {
    exitCode: 0,
    message: "Generated API types match the backend OpenAPI contract.\n",
  };
}

export function writeGeneratedSourceAtomically(generatedOutputPath, generated) {
  const outputDirectory = dirname(generatedOutputPath);
  mkdirSync(outputDirectory, { recursive: true });
  const ownedTemporaryDirectory = mkdtempSync(
    resolve(outputDirectory, `.${basename(generatedOutputPath)}.generate-`),
  );
  try {
    const ownedTemporaryPath = resolve(ownedTemporaryDirectory, basename(generatedOutputPath));
    writeFileSync(ownedTemporaryPath, generated, {
      encoding: "utf8",
      flag: "wx",
    });
    renameSync(ownedTemporaryPath, generatedOutputPath);
  } finally {
    rmSync(ownedTemporaryDirectory, { recursive: true, force: true });
  }
}

function run(arguments_) {
  const checkOnly = arguments_.length === 1 && arguments_[0] === "--check";
  if (arguments_.length > 0 && !checkOnly) {
    process.stderr.write("Usage: node scripts/generate-api.mjs [--check]\n");
    return 2;
  }

  const temporaryRoot = mkdtempSync(resolve(tmpdir(), "gongkao-openapi-"));
  try {
    const schemaPath = resolve(temporaryRoot, "openapi.json");
    const generatedDirectory = resolve(temporaryRoot, "generated");
    const python = existsSync(resolve(backendRoot, ".venv/bin/python"))
      ? resolve(backendRoot, ".venv/bin/python")
      : "python3";
    const deterministicEnvironment = {
      ...process.env,
      LANG: "C",
      LC_ALL: "C",
      PYTHONDONTWRITEBYTECODE: "1",
      PYTHONHASHSEED: "0",
      TZ: "UTC",
    };

    execFileSync(
      python,
      [resolve(backendRoot, "scripts/export_openapi.py"), "--output", schemaPath],
      { cwd: backendRoot, env: deterministicEnvironment, stdio: "inherit" },
    );
    execFileSync(
      resolve(root, "node_modules/.bin/openapi-ts"),
      [
        "--input",
        schemaPath,
        "--output",
        generatedDirectory,
        "--plugins",
        "@hey-api/typescript",
        "--no-log-file",
        "--silent",
      ],
      { cwd: root, env: deterministicEnvironment, stdio: "inherit" },
    );

    const generated = normalizeGeneratedSource(
      readFileSync(resolve(generatedDirectory, "types.gen.ts"), "utf8"),
    );
    if (checkOnly) {
      const result = checkGeneratedSource(outputPath, generated);
      if (result.exitCode === 0) {
        process.stdout.write(result.message);
      } else {
        process.stderr.write(result.message);
      }
      return result.exitCode;
    } else {
      writeGeneratedSourceAtomically(outputPath, generated);
      process.stdout.write(`Generated ${outputPath}\n`);
      return 0;
    }
  } finally {
    rmSync(temporaryRoot, { recursive: true, force: true });
  }
}

const isMain =
  process.argv[1] !== undefined && import.meta.url === pathToFileURL(resolve(process.argv[1])).href;
if (isMain) process.exitCode = run(process.argv.slice(2));
