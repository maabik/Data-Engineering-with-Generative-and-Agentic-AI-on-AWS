// Conventional Commits for data work. The "scope-enum" enforces that scopes
// match real top-level concerns of this chapter codebase, so commits are
// trivially groupable in changelogs.
module.exports = {
  extends: ["@commitlint/config-conventional"],
  rules: {
    "type-enum": [
      2,
      "always",
      ["feat", "fix", "chore", "docs", "refactor", "perf", "test", "ci", "build", "revert"],
    ],
    "scope-enum": [
      2,
      "always",
      [
        "terraform",
        "cdk",
        "glue",
        "sfn",
        "dags",
        "agent",
        "mcp",
        "prompts",
        "evals",
        "tests",
        "ci",
        "labs",
        "docs",
      ],
    ],
    "scope-empty": [2, "never"],
    "subject-case": [2, "always", "lower-case"],
    "header-max-length": [2, "always", 100],
  },
};
