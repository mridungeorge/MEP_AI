"use client";
import { useEffect, useState, type ReactNode } from "react";
import { SLUG, parseGuide, type GuideBlock, type GuideEntry } from "@/lib/guide";

function renderBlock(b: GuideBlock, i: number): ReactNode {
  switch (b.type) {
    case "heading": {
      const text = b.text;
      return b.level === 1 ? <h2 key={i}>{text}</h2> : b.level === 2 ? <h3 key={i}>{text}</h3> : <h4 key={i}>{text}</h4>;
    }
    case "list": {
      const items = b.items.map((t, n) => <li key={n}>{t}</li>);
      return b.ordered ? <ol key={i}>{items}</ol> : <ul key={i}>{items}</ul>;
    }
    case "pre": return <pre key={i} style={{ overflowX: "auto" }}>{b.text}</pre>;
    default: return <p key={i}>{b.text}</p>;
  }
}

/** The user guide, copied into /guide at build time and shown as escaped text (no HTML is ever injected). */
export function HelpScreen() {
  const [entries, setEntries] = useState<GuideEntry[] | null>(null);
  const [slug, setSlug] = useState<string | null>(null);
  const [blocks, setBlocks] = useState<GuideBlock[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/guide/index.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("The guide is not available."))))
      .then((list: GuideEntry[]) => {
        const ok = list.filter((e) => typeof e.slug === "string" && typeof e.title === "string" && SLUG.test(e.slug));
        setEntries(ok);
        setSlug(ok[0]?.slug ?? null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (!slug || !SLUG.test(slug)) return;
    let live = true;
    fetch(`/guide/${slug}.md`)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error("That page is not available."))))
      .then((t) => { if (live) { setBlocks(parseGuide(t)); setError(null); } })
      .catch((e: unknown) => { if (live) setError(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [slug]);

  return (
    <main>
      <h1>Help</h1>
      <p role="note">Every rule in this application is a draft and is not engineer-approved. Nothing here is a compliance result.</p>
      {error && <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>}
      {entries && entries.length === 0 && <p>The guide has not been built into this deployment.</p>}
      <div style={{ display: "flex", gap: 24, alignItems: "flex-start", flexWrap: "wrap" }}>
        <nav aria-label="Guide pages">
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {entries?.map((e) => (
              <li key={e.slug}>
                <button type="button" aria-current={e.slug === slug ? "page" : undefined}
                        style={{ background: "none", border: 0, padding: "4px 0", cursor: "pointer", textAlign: "left", fontWeight: e.slug === slug ? 700 : 400 }}
                        onClick={() => setSlug(e.slug)}>{e.title}</button>
              </li>
            ))}
          </ul>
        </nav>
        <article aria-label="Guide page" style={{ flex: 1, minWidth: 280, maxWidth: 780 }}>{blocks.map(renderBlock)}</article>
      </div>
    </main>
  );
}
