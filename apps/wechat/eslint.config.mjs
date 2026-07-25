import eslint from "@eslint/js";
import tseslint from "typescript-eslint";

const typeCheckedFiles = ["miniprogram/**/*.ts", "tests/**/*.ts", "vitest.config.ts"];

const nodeGlobals = {
  AbortController: "readonly",
  Buffer: "readonly",
  FormData: "readonly",
  TextDecoder: "readonly",
  TextEncoder: "readonly",
  URL: "readonly",
  URLSearchParams: "readonly",
  clearImmediate: "readonly",
  clearInterval: "readonly",
  clearTimeout: "readonly",
  console: "readonly",
  fetch: "readonly",
  process: "readonly",
  queueMicrotask: "readonly",
  setImmediate: "readonly",
  setInterval: "readonly",
  setTimeout: "readonly",
  structuredClone: "readonly",
};

const typeCheckedConfigs = tseslint.configs.strictTypeChecked.map((config) => ({
  ...config,
  files: typeCheckedFiles,
}));

export default tseslint.config(
  {
    ignores: ["design/**", "miniprogram/api/generated/**", "miniprogram_npm/**", "node_modules/**"],
  },
  eslint.configs.recommended,
  ...typeCheckedConfigs,
  {
    files: typeCheckedFiles,
    languageOptions: {
      parserOptions: {
        projectService: {
          allowDefaultProject: ["vitest.config.ts"],
        },
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/no-floating-promises": "error",
    },
  },
  {
    files: ["**/*.{js,mjs,cjs}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: nodeGlobals,
    },
    rules: {
      "no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
          destructuredArrayIgnorePattern: "^_",
        },
      ],
    },
  },
  {
    files: ["cloudfunctions/**/*.js"],
    languageOptions: {
      sourceType: "commonjs",
      globals: {
        ...nodeGlobals,
        __dirname: "readonly",
        __filename: "readonly",
        exports: "writable",
        module: "readonly",
        require: "readonly",
      },
    },
  },
  {
    files: ["e2e/*.mjs"],
    languageOptions: {
      globals: {
        getApp: "readonly",
        getCurrentPages: "readonly",
        wx: "readonly",
      },
    },
  },
);
