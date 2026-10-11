import { describe, expect, it } from "vitest";
import { SLUG, inlineText, parseGuide } from "./guide";

describe("parseGuide", () => {
  it("reads headings, paragraphs and joins wrapped lines", () => {
    expect(parseGuide("# Title\n\nOne line\nsecond line\n\n## Next")).toEqual([
      { type: "heading", level: 1, text: "Title" },
      { type: "para", text: "One line second line" },
      { type: "heading", level: 2, text: "Next" },
    ]);
  });

  it("groups bullet and numbered lists separately", () => {
    expect(parseGuide("- a\n- b\n1. c\n2. d")).toEqual([
      { type: "list", ordered: false, items: ["a", "b"] },
      { type: "list", ordered: true, items: ["c", "d"] },
    ]);
  });

  it("keeps fenced code and tables as preformatted text", () => {
    expect(parseGuide("```\nx < y\n```\n| a | b |\n|---|---|\n| 1 | 2 |")).toEqual([
      { type: "pre", text: "x < y" },
      { type: "pre", text: "| a | b |\n|---|---|\n| 1 | 2 |" },
    ]);
  });

  it("never turns markup into anything but text", () => {
    const blocks = parseGuide("<script>alert(1)</script> [link](javascript:void) **bold** `code`");
    expect(blocks).toEqual([{ type: "para", text: "<script>alert(1)</script> link bold code" }]);
  });

  it("handles an empty document and CRLF", () => {
    expect(parseGuide("")).toEqual([]);
    expect(parseGuide("# A\r\n\r\ntext")).toHaveLength(2);
  });
});

describe("helpers", () => {
  it("strips link, bold and code markers", () => {
    expect(inlineText("see [Gate 1](gate-1.md) and **this** `x`")).toBe("see Gate 1 and this x");
  });
  it("accepts only plain slugs", () => {
    expect(SLUG.test("gate-1-confirm-inputs")).toBe(true);
    expect(SLUG.test("../etc/passwd")).toBe(false);
    expect(SLUG.test("a/b")).toBe(false);
  });
});
