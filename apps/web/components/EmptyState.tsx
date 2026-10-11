/** Shown where a list can be empty: says what is missing and what to do next. */
export function EmptyState({ title, next, href, linkText }: { title: string; next: string; href?: string; linkText?: string }) {
  return (
    <div role="status" data-testid="empty-state" style={{ padding: 12, border: "1px dashed #94a3b8" }}>
      <strong>{title}</strong>
      <p style={{ margin: "4px 0 0" }}>{next}{href && linkText ? <> <a href={href}>{linkText}</a></> : null}</p>
    </div>
  );
}
