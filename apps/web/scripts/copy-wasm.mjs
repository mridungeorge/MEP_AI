// Copies the web-ifc WASM next to the app's static files so the browser can load it from /wasm/ (the 3D preview). Run before `next dev` and `next build`.
import { copyFileSync, mkdirSync } from "node:fs";
import path from "node:path";

const source = path.join(process.cwd(), "node_modules", "web-ifc", "web-ifc.wasm");
const target = path.join(process.cwd(), "public", "wasm");
mkdirSync(target, { recursive: true });
copyFileSync(source, path.join(target, "web-ifc.wasm"));
console.log("web-ifc.wasm copied to public/wasm");
