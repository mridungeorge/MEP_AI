"use client";
import { formatPoints, getPath, parsePoints } from "@/lib/specpath";
import type { FormField } from "@/lib/types";

type Props = {
  fields: FormField[];
  spec: unknown;
  onChange: (path: string, value: unknown) => void;
  firmDefaults: Record<string, unknown>;
  /** the fields the designer has not filled and the server still asks for (shown with their question) */
  missing: Record<string, string>;
  indexPath?: string;
};

/** Concrete path of a field inside a list item: "rooms[].name" -> "rooms[2].name". */
function at(path: string, indexPath: string | undefined): string {
  if (!indexPath) return path;
  return path.replace(/^(.*?)\[\]/, `$1[${indexPath}]`);
}

function Label({ f }: { f: FormField }) {
  return (
    <span>
      {f.label}{f.required ? <abbr title="required" aria-label="required"> *</abbr> : null}
      {"unit" in f && f.unit ? <small> ({f.unit})</small> : null}
    </span>
  );
}

function Hint({ f, path, props }: { f: FormField; path: string; props: Props }) {
  const q = props.missing[path];
  const def = f.firm_default ? props.firmDefaults[f.path] : undefined;
  return (
    <>
      {q && <small role="note" data-testid="missing-question" style={{ color: "#b45309", display: "block" }}>{q}</small>}
      {def !== undefined && <small style={{ display: "block" }}>Firm default: {String(def)} (used if you leave this empty)</small>}
      {f.help && <small style={{ display: "block", color: "#555" }}>{f.help}</small>}
    </>
  );
}

function One({ f, props }: { f: FormField; props: Props }) {
  const path = at(f.path, props.indexPath);
  const value = getPath(props.spec, path);
  const set = (v: unknown) => props.onChange(path, v);
  const common = { "aria-label": f.label, "data-path": path } as const;
  switch (f.kind) {
    case "number":
    case "integer":
      return (
        <label style={{ display: "block", margin: "6px 0" }}>
          <Label f={f} />{" "}
          <input {...common} type="number" step={f.kind === "integer" ? (f.step ?? 1) : "any"} min={f.min ?? undefined} max={f.max ?? undefined}
                 value={typeof value === "number" ? value : ""} placeholder={f.default !== null ? String(f.default) : ""}
                 onChange={(e) => set(e.target.value === "" ? undefined : Number(e.target.value))} />
          <Hint f={f} path={path} props={props} />
        </label>
      );
    case "text":
      return (
        <label style={{ display: "block", margin: "6px 0" }}>
          <Label f={f} />{" "}
          <input {...common} type="text" maxLength={f.max_length ?? undefined} value={typeof value === "string" ? value : ""}
                 onChange={(e) => set(e.target.value)} />
          <Hint f={f} path={path} props={props} />
        </label>
      );
    case "enum":
      return (
        <label style={{ display: "block", margin: "6px 0" }}>
          <Label f={f} />{" "}
          <select {...common} value={typeof value === "string" ? value : ""} onChange={(e) => set(e.target.value || undefined)}>
            <option value="">choose…</option>
            {f.options.map((o) => <option key={o} value={o}>{o.replaceAll("_", " ")}</option>)}
          </select>
          <Hint f={f} path={path} props={props} />
        </label>
      );
    case "points":
      return (
        <label style={{ display: "block", margin: "6px 0" }}>
          <Label f={f} /> one corner per line: x, y
          <textarea key={formatPoints(value)} {...common} rows={5} defaultValue={formatPoints(value)}
                    onBlur={(e) => { const p = parsePoints(e.target.value); set(p === null ? undefined : p); }} />
          <Hint f={f} path={path} props={props} />
        </label>
      );
    case "group":
      return (
        <fieldset style={{ margin: "8px 0" }}>
          <legend>{f.label}{f.required ? " *" : ""}</legend>
          <SpecForm {...props} fields={f.fields} />
        </fieldset>
      );
    case "conditional": {
      const chosen = getPath(props.spec, f.on);
      const fields = typeof chosen === "string" ? f.cases[chosen] : undefined;
      return (
        <fieldset style={{ margin: "8px 0" }}>
          <legend>{f.label}</legend>
          {fields ? <SpecForm {...props} fields={fields} /> : <small>Choose the {f.on} first.</small>}
        </fieldset>
      );
    }
    case "choice": {
      const key = at(f.key, props.indexPath);
      const chosen = getPath(props.spec, key);
      const fields = typeof chosen === "string" ? f.cases[chosen] : undefined;
      return (
        <fieldset style={{ margin: "8px 0" }}>
          <legend>{f.label} *</legend>
          <label>Shape{" "}
            <select aria-label={`${f.label} shape`} value={typeof chosen === "string" ? chosen : ""}
                    onChange={(e) => props.onChange(at(f.path, props.indexPath), e.target.value ? { type: e.target.value } : undefined)}>
              <option value="">choose…</option>
              {Object.keys(f.cases).map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          {props.missing[key] && <small style={{ color: "#b45309" }}>{props.missing[key]}</small>}
          {fields && <SpecForm {...props} fields={fields} />}
        </fieldset>
      );
    }
    case "list": {
      const base = f.path.replace(/\[\]$/, "");
      const items = getPath(props.spec, at(base, props.indexPath));
      const list = Array.isArray(items) ? items : [];
      return (
        <section aria-label={f.label} style={{ margin: "8px 0" }}>
          <h3>{f.label}{f.required ? " *" : ""}</h3>
          {props.missing[at(base, props.indexPath)] && <small style={{ color: "#b45309" }}>{props.missing[at(base, props.indexPath)]}</small>}
          {list.map((_, i) => (
            <fieldset key={i} data-testid="list-item" style={{ margin: "8px 0" }}>
              <legend>{f.label.replace(/s$/, "")} {i + 1}</legend>
              <SpecForm {...props} fields={f.item} indexPath={String(i)} />
              <button type="button" onClick={() => props.onChange(at(base, props.indexPath), list.filter((__, j) => j !== i))}>Remove</button>
            </fieldset>
          ))}
          <button type="button" disabled={f.max_items !== null && list.length >= f.max_items}
                  onClick={() => props.onChange(at(base, props.indexPath), [...list, {}])}>Add {f.label.toLowerCase().replace(/s$/, "")}</button>
        </section>
      );
    }
  }
}

/** The wizard: the spec card drawn as a form. Required fields are starred, every number shows its unit and limits, and a field with a firm
 *  default says so. The form holds the designer's values only; the server fills defaults and refuses a card that is not complete. */
export function SpecForm(props: Props) {
  return <>{props.fields.map((f) => <One key={f.path} f={f} props={props} />)}</>;
}
