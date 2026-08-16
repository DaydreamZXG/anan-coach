#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const katex = require("katex");

const resultsDir = process.argv[2];
const outputPath = process.argv[3];
if (!resultsDir || !outputPath) {
  console.error("usage: validate_katex.js RESULTS_DIR OUTPUT_JSON");
  process.exit(2);
}

const files = fs.readdirSync(resultsDir).filter((name) => name.endsWith(".json")).sort();
const invalid = [];
const suspicious = [];
let parsed = 0;
const providers = new Map();

function repetitionReason(expression) {
  const tokens = expression.trim().split(/\s+/).filter(Boolean);
  if (tokens.length >= 900) return "near_max_length";
  if (tokens.length < 48) return null;
  const counts = new Map();
  for (const token of tokens) counts.set(token, (counts.get(token) || 0) + 1);
  const dominant = Math.max(...counts.values()) / tokens.length;
  if (dominant >= 0.45) return "dominant_token_repetition";
  const uniqueRatio = counts.size / tokens.length;
  if (tokens.length >= 96 && uniqueRatio <= 0.12) return "low_token_diversity";
  return null;
}

for (const filename of files) {
  const value = JSON.parse(fs.readFileSync(path.join(resultsDir, filename), "utf8"));
  const provider = value.provider || "unknown";
  providers.set(provider, (providers.get(provider) || 0) + 1);
  const expression = value.blocks?.[0]?.expression || "";
  const reason = repetitionReason(expression);
  if (reason) suspicious.push({ asset_id: value.asset_id, reason });
  try {
    katex.renderToString(expression, {
      displayMode: Boolean(value.blocks?.[0]?.display),
      throwOnError: true,
      strict: "ignore",
      output: "htmlAndMathml",
    });
    parsed += 1;
  } catch (error) {
    invalid.push({ asset_id: value.asset_id, error: String(error.message || error).slice(0, 240) });
  }
}

const summary = {
  schema_version: "formula-katex-validation-v1",
  asset_count: files.length,
  parsed_count: parsed,
  provider_counts: Object.fromEntries([...providers.entries()].sort()),
  // Kept for consumers of the first report format. It now means all parsed providers.
  texo_parsed_count: parsed,
  katex_invalid_count: invalid.length,
  suspicious_repetition_count: suspicious.length,
  katex_invalid: invalid,
  suspicious_repetition: suspicious,
};
fs.writeFileSync(outputPath, JSON.stringify(summary, null, 2) + "\n");
console.log(JSON.stringify({
  asset_count: summary.asset_count,
  parsed_count: parsed,
  provider_counts: summary.provider_counts,
  texo_parsed_count: parsed,
  katex_invalid_count: invalid.length,
  suspicious_repetition_count: suspicious.length,
}));
