import { describe, expect, it } from "vitest";
import type { ErrorEvent } from "@sentry/browser";
import { scrubEvent, scrubString } from "./sentry";

describe("browser error reports carry nothing personal", () => {
  it("removes addresses and token-shaped strings from text", () => {
    expect(scrubString("failed for jo@firm.example with eyJhbGciOiJIUzI1NiJ9abc.eyJzdWIiOiIxMjM0NTY3ODkwIn0abc.sig")).not.toMatch(/jo@firm|eyJ/);
  });
  it("drops the user, request data, query strings and fragments", () => {
    const event = {
      type: undefined, user: { email: "jo@firm.example", id: "1" }, server_name: "host",
      request: { url: "https://app.example/share?x=1#TOKENTOKENTOKENTOKEN", headers: { Authorization: "Bearer abc" }, data: "secret" },
      message: "boom jo@firm.example", exception: { values: [{ type: "Error", value: "bad for a@b.co", stacktrace: { frames: [{ vars: { a: 1 } }] } }] },
      breadcrumbs: [{ message: "clicked jo@firm.example", data: { url: "/x?token=1" } }],
    } as unknown as ErrorEvent;
    const out = scrubEvent(event);
    expect(JSON.stringify(out)).not.toMatch(/jo@firm|TOKEN|Bearer|secret|a@b\.co|token=1/);
    expect(out.user).toBeUndefined();
    expect(out.request).toEqual({ url: "https://app.example/share" });
  });
});
