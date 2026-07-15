import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const repositoryRoot = resolve(root, "../..");
const backendRoot = resolve(repositoryRoot, "backend");
const outputPath = resolve(root, "miniprogram/api/generated/types.gen.ts");
const checkOnly = process.argv.includes("--check");
const temporaryRoot = mkdtempSync(resolve(tmpdir(), "gongkao-openapi-"));

try {
  const schemaPath = resolve(temporaryRoot, "openapi.json");
  const generatedDirectory = resolve(temporaryRoot, "generated");
  const python = existsSync(resolve(backendRoot, ".venv/bin/python"))
    ? resolve(backendRoot, ".venv/bin/python")
    : "python3";

  execFileSync(
    python,
    [resolve(backendRoot, "scripts/export_openapi.py"), "--output", schemaPath],
    { cwd: backendRoot, stdio: "inherit" },
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
    { cwd: root, stdio: "inherit" },
  );

  const generated = readFileSync(resolve(generatedDirectory, "types.gen.ts"), "utf8");
  if (checkOnly) {
    if (!existsSync(outputPath) || readFileSync(outputPath, "utf8") !== generated) {
      process.stderr.write(
        "Generated API types are stale. Run npm run api:generate and commit the result.\n",
      );
      process.exitCode = 1;
    } else {
      process.stdout.write("Generated API types match the backend OpenAPI contract.\n");
    }
  } else {
    mkdirSync(resolve(outputPath, ".."), { recursive: true });
    writeFileSync(outputPath, generated);
    process.stdout.write(`Generated ${outputPath}\n`);
  }
} finally {
  rmSync(temporaryRoot, { recursive: true, force: true });
}
