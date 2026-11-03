import { describe, expect, it } from "vitest";
import { checkAuthor } from "./author.js";

describe("checkAuthor", () => {
  it("accepts a name with or without a contact", () => {
    for (const author of [
      "Ada Lovelace",
      "Ada <@ada-l>",
      "Ada <https://example.com/ada>",
      "Ada <ada@example.com>",
    ]) {
      expect(checkAuthor(author), author).toBeNull();
    }
  });

  it("rejects malformed contacts", () => {
    for (const author of [
      "Ada <@-ada>",
      "Ada <@ada->",
      "Ada <@ada--l>",
      "Ada <ada>",
      "Ada <ada@example>",
      "Ada <http://example.com>",
      "Ada <@ada",
      "<@ada>",
      " Ada ",
    ]) {
      expect(checkAuthor(author), author).not.toBeNull();
    }
  });
});
