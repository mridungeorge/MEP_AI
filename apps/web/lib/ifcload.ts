/** Reads an IFC file in the browser with web-ifc (WASM) and returns its placed geometry. The WASM file is served from /wasm/ (copied by scripts/copy-wasm.mjs). */
import type { PlacedPart } from "./ifcmesh";

export async function loadIfcParts(bytes: ArrayBuffer, onProgress?: (done: number, total: number) => void): Promise<PlacedPart[]> {
  const WebIFC = await import("web-ifc");
  const api = new WebIFC.IfcAPI();
  api.SetWasmPath("/wasm/");
  await api.Init(undefined, true);
  const model = api.OpenModel(new Uint8Array(bytes), { COORDINATE_TO_ORIGIN: true });
  if (model < 0) throw new Error("web-ifc could not open this file");
  const parts: PlacedPart[] = [];
  try {
    api.StreamAllMeshes(model, (mesh, index, total) => {
      const placed = mesh.geometries;
      for (let i = 0; i < placed.size(); i++) {
        const pg = placed.get(i);
        const geo = api.GetGeometry(model, pg.geometryExpressID);
        const vertices = api.GetVertexArray(geo.GetVertexData(), geo.GetVertexDataSize());
        const indices = api.GetIndexArray(geo.GetIndexData(), geo.GetIndexDataSize());
        parts.push({ vertices: vertices.slice(), indices: indices.slice(), transform: pg.flatTransformation.slice(), color: [pg.color.x, pg.color.y, pg.color.z, pg.color.w] });
        geo.delete();
      }
      mesh.delete();
      if (onProgress && index % 50 === 0) onProgress(index, total);
    });
  } finally {
    api.CloseModel(model);
  }
  return parts;
}
