import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const miniProgramRoot = path.resolve(scriptDirectory, "../miniprogram");
const pageRoot = path.join(miniProgramRoot, "pages");

test("only pages that render workbench components declare them", () => {
  const app = readJson(path.join(miniProgramRoot, "app.json"));
  assert.equal(app.usingComponents?.["safe-top"], undefined);
  assert.equal(app.usingComponents?.["workbench-nav"], undefined);

  for (const wxmlFile of filesBelow(pageRoot, ".wxml")) {
    const template = fs.readFileSync(wxmlFile, "utf8");
    const jsonFile = wxmlFile.replace(/\.wxml$/u, ".json");
    const page = readJson(jsonFile);
    for (const tag of ["safe-top", "workbench-nav"]) {
      const declaration = page.usingComponents?.[tag];
      assert.equal(
        Boolean(declaration),
        template.includes(`<${tag}`),
        `${jsonFile} must declare ${tag} exactly when its template renders it`,
      );
      if (declaration) {
        const target = path.resolve(path.dirname(jsonFile), declaration);
        assert.equal(target, path.join(miniProgramRoot, "components", tag, "index"));
      }
    }
  }
});

test("network status listener is removable and detached while hidden", () => {
  const source = fs.readFileSync(path.join(miniProgramRoot, "app.ts"), "utf8");
  assert.match(source, /wx\.onNetworkStatusChange\(handleNetworkStatusChange\)/u);
  assert.match(source, /wx\.offNetworkStatusChange\(/u);
  assert.match(source, /onHide\(\) \{/u);
  assert.doesNotMatch(source, /wx\.onNetworkStatusChange\(\s*\(/u);
});

function filesBelow(root, extension) {
  return fs.readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(root, entry.name);
    if (entry.isDirectory()) return filesBelow(target, extension);
    return entry.isFile() && target.endsWith(extension) ? [target] : [];
  });
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}
