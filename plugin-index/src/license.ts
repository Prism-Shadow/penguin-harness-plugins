/**
 * License expression checking against the closed list in `vocabulary.ts`.
 *
 * We accept the subset of SPDX expressions that real packages use — single
 * identifiers and flat `OR` / `AND` chains — and nothing else. Parenthesised
 * expressions, `WITH` exceptions and the `+` suffix are refused rather than
 * half-understood: a license this checker cannot fully read is one a human should
 * look at.
 */
import { LICENSES } from "./vocabulary.js";

const ALLOWED = new Set<string>(LICENSES);

/** Returns a problem description, or null when the expression is allowed. */
export function checkLicense(expression: string): string | null {
  if (expression.trim() !== expression || expression === "") {
    return "license must be a non-empty SPDX expression without surrounding whitespace";
  }
  if (/[()+]/.test(expression) || /\bWITH\b/.test(expression)) {
    return `license "${expression}" uses SPDX syntax this index does not accept (parentheses, '+', or WITH)`;
  }

  const tokens = expression.split(/\s+/);
  const operators = new Set(["OR", "AND"]);
  for (const [i, token] of tokens.entries()) {
    const wantsOperator = i % 2 === 1;
    if (wantsOperator) {
      if (!operators.has(token)) {
        return `license "${expression}" must join identifiers with OR / AND`;
      }
      continue;
    }
    if (!ALLOWED.has(token)) {
      return `license "${token}" is not in this index's allowed list (see docs/licensing.md)`;
    }
  }
  if (tokens.length % 2 === 0) return `license "${expression}" ends with an operator`;
  return null;
}
