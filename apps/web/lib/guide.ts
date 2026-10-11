/** A tiny Markdown reader for the user guide: headings, paragraphs, bullet and numbered lists, fenced code and tables (kept as preformatted text). The output is plain data; the screen renders it as React text, so nothing is ever treated as HTML. */
export type GuideBlock =
  | { type: "heading"; level: 1 | 2 | 3; text: string }
  | { type: "para"; text: string }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "pre"; text: string };

export type GuideEntry = { slug: string; title: string };

/** Drops link, bold and code markers, keeping the words. */
export function inlineText(s: string): string {
  return s.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/\*\*([^*]+)\*\*/g, "$1").replace(/`([^`]*)`/g, "$1");
}

export function parseGuide(md: string): GuideBlock[] {
  const blocks: GuideBlock[] = [];
  const lines = md.replace(/\r\n?/g, "\n").split("\n");
  let para: string[] = [];
  const flush = () => { if (para.length) { blocks.push({ type: "para", text: inlineText(para.join(" ")) }); para = []; } };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim() === "") { flush(); continue; }
    if (line.startsWith("```")) {
      flush();
      const code: string[] = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) code.push(lines[i]);
      blocks.push({ type: "pre", text: code.join("\n") });
      continue;
    }
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) { flush(); blocks.push({ type: "heading", level: h[1].length as 1 | 2 | 3, text: inlineText(h[2].trim()) }); continue; }
    if (line.trimStart().startsWith("|")) {
      flush();
      const rows: string[] = [line];
      while (i + 1 < lines.length && lines[i + 1].trimStart().startsWith("|")) rows.push(lines[++i]);
      blocks.push({ type: "pre", text: rows.join("\n") });
      continue;
    }
    const li = /^\s*(?:([-*])|(\d+)[.)])\s+(.*)$/.exec(line);
    if (li) {
      flush();
      const ordered = li[2] !== undefined;
      const items: string[] = [inlineText(li[3])];
      while (i + 1 < lines.length) {
        const next = /^\s*(?:([-*])|(\d+)[.)])\s+(.*)$/.exec(lines[i + 1]);
        if (!next || (next[2] !== undefined) !== ordered) break;
        items.push(inlineText(next[3]));
        i++;
      }
      blocks.push({ type: "list", ordered, items });
      continue;
    }
    para.push(line.trim());
  }
  flush();
  return blocks;
}

/** Only index.json slugs of this shape are ever fetched. */
export const SLUG = /^[a-z0-9][a-z0-9-]{0,80}$/;
