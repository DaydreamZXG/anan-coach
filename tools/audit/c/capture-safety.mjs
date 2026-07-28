import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import {
  lstat,
  mkdir,
  open,
  realpath,
} from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

export function requiredFullCommit(value, label = "commit") {
  const commit = String(value ?? "").trim();
  assert.match(
    commit,
    /^[0-9a-f]{40}$/u,
    `${label} must be a full 40-character lowercase Git commit`,
  );
  return commit;
}

export function requiredSafeLabel(value, label = "label") {
  const safeLabel = String(value ?? "");
  assert.match(
    safeLabel,
    /^[a-z0-9][a-z0-9-]*$/u,
    `${label} must contain only lowercase letters, digits, and hyphens`,
  );
  return safeLabel;
}

export async function readCleanSourceGitEvidence(
  sourceRepositoryRoot,
  declaredSourceCommit,
) {
  const expectedCommit = requiredFullCommit(
    declaredSourceCommit,
    "WECHAT_AUDIT_SOURCE_COMMIT",
  );
  const [{ stdout: headOutput }, { stdout: statusOutput }] = await Promise.all([
    execFileAsync(
      "git",
      ["-C", sourceRepositoryRoot, "rev-parse", "--verify", "HEAD"],
      { encoding: "utf8" },
    ),
    execFileAsync(
      "git",
      [
        "-C",
        sourceRepositoryRoot,
        "status",
        "--porcelain=v1",
        "--untracked-files=normal",
      ],
      { encoding: "utf8" },
    ),
  ]);
  const head = requiredFullCommit(headOutput.trim(), "source HEAD");
  const status = statusOutput.trim();
  assert.equal(
    head,
    expectedCommit,
    "WECHAT_AUDIT_SOURCE_COMMIT does not match source repository HEAD",
  );
  assert.equal(status, "", `Refusing a dirty source worktree:\n${status}`);
  return {
    head,
    declaredCommit: expectedCommit,
    clean: true,
    status: "",
  };
}

export function assertSourceGitEvidenceUnchanged(initial, final) {
  assert.deepEqual(
    final,
    initial,
    "Source Git evidence changed during the audit capture",
  );
  return {
    validation: "unchanged-clean-head",
    initial,
    final,
  };
}

export function parseLoopbackWebSocketEndpoint(value) {
  if (value === undefined || String(value).trim() === "") return null;
  const endpoint = new URL(String(value).trim());
  assert.ok(
    endpoint.protocol === "ws:" || endpoint.protocol === "wss:",
    "WECHAT_DEVTOOLS_AUTOMATION_ENDPOINT must use ws:// or wss://",
  );
  assert.ok(
    ["127.0.0.1", "::1", "[::1]", "localhost"].includes(endpoint.hostname),
    "WECHAT_DEVTOOLS_AUTOMATION_ENDPOINT must use a literal loopback host or localhost",
  );
  assert.equal(
    endpoint.username,
    "",
    "WECHAT_DEVTOOLS_AUTOMATION_ENDPOINT must not contain credentials",
  );
  assert.equal(
    endpoint.password,
    "",
    "WECHAT_DEVTOOLS_AUTOMATION_ENDPOINT must not contain credentials",
  );
  assert.notEqual(
    endpoint.port,
    "",
    "WECHAT_DEVTOOLS_AUTOMATION_ENDPOINT must declare an explicit port",
  );
  assert.equal(
    endpoint.pathname,
    "/",
    "WECHAT_DEVTOOLS_AUTOMATION_ENDPOINT must not contain a path",
  );
  assert.equal(
    endpoint.search,
    "",
    "WECHAT_DEVTOOLS_AUTOMATION_ENDPOINT must not contain a query",
  );
  assert.equal(
    endpoint.hash,
    "",
    "WECHAT_DEVTOOLS_AUTOMATION_ENDPOINT must not contain a fragment",
  );
  return endpoint.href.replace(/\/$/u, "");
}

export function parseRuntimeIdentity(rawIdentity, sourceCommit) {
  const identity =
    typeof rawIdentity === "string" ? JSON.parse(rawIdentity) : rawIdentity;
  assert.equal(
    identity?.schemaVersion,
    1,
    "Audit runtime identity has an unsupported schema",
  );
  assert.match(
    String(identity?.nonce ?? ""),
    /^[0-9a-f]{64}$/u,
    "Audit runtime identity nonce must be 32 random bytes encoded as lowercase hex",
  );
  assert.equal(
    identity?.sourceCommit,
    sourceCommit,
    "Audit runtime identity source commit does not match the declared source",
  );
  return Object.freeze({
    schemaVersion: 1,
    nonce: identity.nonce,
    sourceCommit: identity.sourceCommit,
  });
}

export function assertRuntimeIdentityMatches(diskIdentity, liveIdentity) {
  assert.deepEqual(
    liveIdentity,
    diskIdentity,
    "Connected DevTools runtime identity does not match the prepared audit runtime",
  );
  return {
    validation: "exact-disk-to-running-match",
    ...diskIdentity,
  };
}

export function assertPathWithin(boundary, target, label = "path") {
  const resolvedBoundary = path.resolve(boundary);
  const resolvedTarget = path.resolve(target);
  const relative = path.relative(resolvedBoundary, resolvedTarget);
  assert.ok(
    relative !== "" &&
      !relative.startsWith("..") &&
      !path.isAbsolute(relative),
    `${label} must be a strict descendant of ${resolvedBoundary}: ${resolvedTarget}`,
  );
  return { boundary: resolvedBoundary, target: resolvedTarget, relative };
}

export async function assertSafeOutputTarget(
  allowedRoot,
  target,
  { targetMayNotExist = true } = {},
) {
  const { boundary, target: resolvedTarget, relative } = assertPathWithin(
    allowedRoot,
    target,
    "Audit output",
  );
  const boundaryStats = await lstat(boundary);
  assert.ok(
    boundaryStats.isDirectory() && !boundaryStats.isSymbolicLink(),
    `Audit output boundary must be a real directory: ${boundary}`,
  );
  const canonicalBoundary = await realpath(boundary);
  let cursor = boundary;
  const segments = relative.split(path.sep);
  for (const [index, segment] of segments.entries()) {
    cursor = path.join(cursor, segment);
    let stats;
    try {
      stats = await lstat(cursor);
    } catch (error) {
      if (error?.code === "ENOENT" && targetMayNotExist) break;
      throw error;
    }
    assert.ok(
      !stats.isSymbolicLink(),
      `Audit output path must not traverse a symlink: ${cursor}`,
    );
    if (index < segments.length - 1) {
      assert.ok(
        stats.isDirectory(),
        `Audit output ancestor must be a directory: ${cursor}`,
      );
    }
    const canonicalCursor = await realpath(cursor);
    const canonicalRelative = path.relative(
      canonicalBoundary,
      canonicalCursor,
    );
    assert.ok(
      canonicalRelative === "" ||
        (!canonicalRelative.startsWith("..") &&
          !path.isAbsolute(canonicalRelative)),
      `Audit output realpath escapes ${canonicalBoundary}: ${canonicalCursor}`,
    );
  }
  return resolvedTarget;
}

export async function ensureSafeDirectoryTree(allowedRoot, targetDirectory) {
  const { boundary, relative } = assertPathWithin(
    allowedRoot,
    targetDirectory,
    "Audit output directory",
  );
  await assertSafeOutputTarget(boundary, targetDirectory);
  let cursor = boundary;
  for (const segment of relative.split(path.sep)) {
    cursor = path.join(cursor, segment);
    try {
      await mkdir(cursor, { recursive: false });
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
    }
    const stats = await lstat(cursor);
    assert.ok(
      stats.isDirectory() && !stats.isSymbolicLink(),
      `Audit output directory must not be a symlink: ${cursor}`,
    );
  }
  await assertSafeOutputTarget(boundary, targetDirectory, {
    targetMayNotExist: false,
  });
  return path.resolve(targetDirectory);
}

export async function reserveProfileOutputs({
  allowedRoot,
  auditRoot,
  profile,
}) {
  requiredSafeLabel(profile, "Audit profile");
  const resolvedAuditRoot = await ensureSafeDirectoryTree(
    allowedRoot,
    auditRoot,
  );
  const screenshotRoot = await ensureSafeDirectoryTree(
    allowedRoot,
    path.join(resolvedAuditRoot, "screenshots"),
  );
  const logRoot = await ensureSafeDirectoryTree(
    allowedRoot,
    path.join(resolvedAuditRoot, "logs"),
  );
  const profileScreenshotDirectory = path.join(screenshotRoot, profile);
  const profileLogPath = path.join(logRoot, `${profile}.jsonl`);
  const profileManifestPath = path.join(
    logRoot,
    `${profile}-manifest.json`,
  );
  await Promise.all([
    assertSafeOutputTarget(allowedRoot, profileScreenshotDirectory),
    assertSafeOutputTarget(allowedRoot, profileLogPath),
    assertSafeOutputTarget(allowedRoot, profileManifestPath),
  ]);
  try {
    await mkdir(profileScreenshotDirectory, { recursive: false });
  } catch (error) {
    if (error?.code === "EEXIST") {
      throw new Error(
        `Audit profile is already reserved or captured: ${profileScreenshotDirectory}`,
        { cause: error },
      );
    }
    throw error;
  }

  let logHandle;
  let manifestHandle;
  try {
    logHandle = await open(profileLogPath, "wx");
    manifestHandle = await open(profileManifestPath, "wx");
  } catch (error) {
    await Promise.allSettled([logHandle?.close(), manifestHandle?.close()]);
    throw new Error(
      `Audit profile log or manifest already exists: ${profile}`,
      { cause: error },
    );
  }
  return {
    auditRoot: resolvedAuditRoot,
    screenshotRoot,
    logRoot,
    profileScreenshotDirectory,
    profileLogPath,
    profileManifestPath,
    logHandle,
    manifestHandle,
  };
}

export function assertDisjointTrees(first, second, labels = {}) {
  const resolvedFirst = path.resolve(first);
  const resolvedSecond = path.resolve(second);
  const secondFromFirst = path.relative(resolvedFirst, resolvedSecond);
  const firstFromSecond = path.relative(resolvedSecond, resolvedFirst);
  const overlaps =
    secondFromFirst === "" ||
    (!secondFromFirst.startsWith("..") && !path.isAbsolute(secondFromFirst)) ||
    (!firstFromSecond.startsWith("..") && !path.isAbsolute(firstFromSecond));
  assert.ok(
    !overlaps,
    `${labels.first ?? "First tree"} and ${labels.second ?? "second tree"} must be disjoint`,
  );
}
