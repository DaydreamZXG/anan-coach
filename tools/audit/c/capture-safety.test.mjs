import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import {
  mkdtemp,
  mkdir,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";

import {
  assertRuntimeIdentityMatches,
  assertSafeOutputTarget,
  parseLoopbackWebSocketEndpoint,
  readCleanSourceGitEvidence,
  reserveProfileOutputs,
} from "./capture-safety.mjs";

const execFileAsync = promisify(execFile);

test("rejects a declared source commit that does not match the real HEAD", async (t) => {
  const repository = await mkdtemp(
    path.join(os.tmpdir(), "wechat-audit-source-"),
  );
  t.after(() => rm(repository, { force: true, recursive: true }));
  await execFileAsync("git", ["init", "-q", repository]);
  await writeFile(path.join(repository, "tracked.txt"), "evidence\n", "utf8");
  await execFileAsync("git", ["-C", repository, "add", "tracked.txt"]);
  await execFileAsync(
    "git",
    ["-C", repository, "commit", "-q", "-m", "fixture"],
    {
      env: {
        ...process.env,
        GIT_AUTHOR_EMAIL: "audit@example.invalid",
        GIT_AUTHOR_NAME: "Audit Test",
        GIT_COMMITTER_EMAIL: "audit@example.invalid",
        GIT_COMMITTER_NAME: "Audit Test",
      },
    },
  );
  await assert.rejects(
    readCleanSourceGitEvidence(repository, "0".repeat(40)),
    /does not match source repository HEAD/u,
  );
  const { stdout: headOutput } = await execFileAsync(
    "git",
    ["-C", repository, "rev-parse", "HEAD"],
    { encoding: "utf8" },
  );
  await writeFile(path.join(repository, "tracked.txt"), "dirty\n", "utf8");
  await assert.rejects(
    readCleanSourceGitEvidence(repository, headOutput.trim()),
    /Refusing a dirty source worktree/u,
  );
});

test("rejects a non-loopback DevTools automation endpoint", () => {
  assert.throws(
    () => parseLoopbackWebSocketEndpoint("ws://192.0.2.25:9420"),
    /must use a literal loopback host or localhost/u,
  );
  assert.equal(
    parseLoopbackWebSocketEndpoint("ws://127.0.0.1:9420"),
    "ws://127.0.0.1:9420",
  );
});

test("rejects a running runtime identity that differs from disk", () => {
  const diskIdentity = {
    schemaVersion: 1,
    nonce: "a".repeat(64),
    sourceCommit: "b".repeat(40),
  };
  assert.throws(
    () =>
      assertRuntimeIdentityMatches(diskIdentity, {
        ...diskIdentity,
        nonce: "c".repeat(64),
      }),
    /does not match the prepared audit runtime/u,
  );
});

test("rejects an output path whose ancestor is a symlink", async (t) => {
  const temporaryRoot = await mkdtemp(
    path.join(os.tmpdir(), "wechat-audit-output-"),
  );
  t.after(() => rm(temporaryRoot, { force: true, recursive: true }));
  const allowedRoot = path.join(temporaryRoot, "audit");
  const outsideRoot = path.join(temporaryRoot, "outside");
  await Promise.all([
    mkdir(allowedRoot, { recursive: false }),
    mkdir(outsideRoot, { recursive: false }),
  ]);
  await symlink(outsideRoot, path.join(allowedRoot, "escaped"));
  await assert.rejects(
    assertSafeOutputTarget(
      allowedRoot,
      path.join(allowedRoot, "escaped", "result.json"),
    ),
    /must not traverse a symlink/u,
  );
});

test("atomically rejects a second process for the same profile", async (t) => {
  const temporaryRoot = await mkdtemp(
    path.join(os.tmpdir(), "wechat-audit-lock-"),
  );
  t.after(() => rm(temporaryRoot, { force: true, recursive: true }));
  const allowedRoot = path.join(temporaryRoot, "audit");
  await mkdir(allowedRoot, { recursive: false });
  const options = {
    allowedRoot,
    auditRoot: path.join(allowedRoot, "probe"),
    profile: "standard-390x844",
  };
  const first = await reserveProfileOutputs(options);
  t.after(async () => {
    await Promise.allSettled([
      first.logHandle.close(),
      first.manifestHandle.close(),
    ]);
  });
  await assert.rejects(
    reserveProfileOutputs(options),
    /already reserved or captured/u,
  );
});

test("rejects a pre-existing profile output", async (t) => {
  const temporaryRoot = await mkdtemp(
    path.join(os.tmpdir(), "wechat-audit-existing-output-"),
  );
  t.after(() => rm(temporaryRoot, { force: true, recursive: true }));
  const allowedRoot = path.join(temporaryRoot, "audit");
  const auditRoot = path.join(allowedRoot, "probe");
  const logRoot = path.join(auditRoot, "logs");
  await Promise.all([
    mkdir(path.join(auditRoot, "screenshots"), { recursive: true }),
    mkdir(logRoot, { recursive: true }),
  ]);
  await writeFile(
    path.join(logRoot, "standard-390x844.jsonl"),
    "existing evidence\n",
    "utf8",
  );
  await assert.rejects(
    reserveProfileOutputs({
      allowedRoot,
      auditRoot,
      profile: "standard-390x844",
    }),
    /log or manifest already exists/u,
  );
});
