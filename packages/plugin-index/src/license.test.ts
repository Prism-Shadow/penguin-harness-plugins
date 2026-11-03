import { describe, expect, it } from "vitest";
import { checkLicense } from "./license.js";

describe("checkLicense", () => {
  it("accepts single identifiers and flat expressions", () => {
    expect(checkLicense("MIT")).toBeNull();
    expect(checkLicense("MIT OR Apache-2.0")).toBeNull();
    expect(checkLicense("Apache-2.0 AND CC0-1.0")).toBeNull();
  });

  it("rejects unlisted identifiers and syntax it cannot fully read", () => {
    expect(checkLicense("Commercial")).not.toBeNull();
    expect(checkLicense("(MIT OR Apache-2.0)")).not.toBeNull();
    expect(checkLicense("GPL-2.0+")).not.toBeNull();
    expect(checkLicense("Apache-2.0 WITH LLVM-exception")).not.toBeNull();
    expect(checkLicense("MIT OR")).not.toBeNull();
    expect(checkLicense("MIT Apache-2.0")).not.toBeNull();
  });
});
