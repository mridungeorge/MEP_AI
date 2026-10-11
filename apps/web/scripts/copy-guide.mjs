// Copies docs/user-guide/*.md into public/guide/ and writes index.json (slug + title, in reading order) so the /help page can fetch them. Run before `next dev` and `next build`.
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

const ORDER = [
  "index", "getting-started", "gate-1-confirm-inputs", "running-rules-and-reading-results", "revisions-and-diffs",
  "review-and-sign-off", "share-link-for-certifiers", "drafting-skills", "services-clashes-and-quantities",
  "fix-hypotheses-and-performance-solutions", "commissioning-and-nsw-declaration-draft", "limits-and-what-draft-rules-mean", "glossary",
];
const source = path.resolve(process.cwd(), "..", "..", "docs", "user-guide");
const target = path.join(process.cwd(), "public", "guide");

if (!existsSync(source)) {
  // e.g. a Docker build context that holds only apps/web: keep a copy already in public/guide, else write an empty index
  if (!existsSync(path.join(target, "index.json"))) {
    mkdirSync(target, { recursive: true });
    writeFileSync(path.join(target, "index.json"), "[]\n");
  }
  console.log("docs/user-guide not found; guide not refreshed");
} else {
  const slugs = readdirSync(source).filter((f) => /^[a-z0-9][a-z0-9-]*\.md$/.test(f)).map((f) => f.slice(0, -3));
  const rank = (s) => (ORDER.includes(s) ? ORDER.indexOf(s) : ORDER.length);
  slugs.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  rmSync(target, { recursive: true, force: true });
  mkdirSync(target, { recursive: true });
  const index = slugs.map((slug) => {
    const text = readFileSync(path.join(source, `${slug}.md`), "utf8");
    writeFileSync(path.join(target, `${slug}.md`), text);
    return { slug, title: /^#\s+(.+)$/m.exec(text)?.[1].trim() ?? slug };
  });
  writeFileSync(path.join(target, "index.json"), `${JSON.stringify(index, null, 2)}\n`);
  console.log(`${index.length} guide pages copied to public/guide`);
}
