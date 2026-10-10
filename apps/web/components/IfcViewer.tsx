"use client";
import { useEffect, useRef, useState } from "react";
import { type Buffers, type Camera, boundsOf, fitCamera, orbit, viewProjection, zoom } from "@/lib/ifcmesh";

const VERT = `attribute vec3 p; attribute vec3 n; attribute vec4 c; uniform mat4 m; varying vec3 vn; varying vec4 vc;
void main() { vn = n; vc = c; gl_Position = m * vec4(p, 1.0); }`;
const FRAG = `precision mediump float; varying vec3 vn; varying vec4 vc;
void main() { vec3 l = normalize(vec3(0.4, 0.9, 0.3)); float d = 0.35 + 0.65 * abs(dot(normalize(vn), l)); gl_FragColor = vec4(vc.rgb * d, vc.a); }`;

function compile(gl: WebGLRenderingContext, type: number, src: string): WebGLShader {
  const s = gl.createShader(type)!;
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? "shader error");
  return s;
}

/** A small orbit viewer: drag to rotate, wheel to zoom. `layers` are drawn in order (opaque first, then see-through ones). */
export function IfcViewer({ layers, label }: { layers: Buffers[]; label: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const camera = useRef<Camera | null>(null);
  const redraw = useRef<() => void>(() => undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const gl = el.getContext("webgl");
    if (!gl) { setError("This browser cannot show 3D (WebGL is not available)."); return; }
    try {
      const prog = gl.createProgram()!;
      gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VERT));
      gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FRAG));
      gl.linkProgram(prog);
      gl.useProgram(prog);
      const locs = { p: gl.getAttribLocation(prog, "p"), n: gl.getAttribLocation(prog, "n"), c: gl.getAttribLocation(prog, "c"), m: gl.getUniformLocation(prog, "m") };
      const upload = (data: Float32Array) => { const b = gl.createBuffer()!; gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW); return b; };
      const gpu = layers.map((l) => ({ p: upload(l.positions), n: upload(l.normals), c: upload(l.colors), count: l.triangles * 3, clear: l.colors.some((v, i) => i % 4 === 3 && v < 1) }));
      const all = layers.map((l) => boundsOf(l.positions)).filter((b) => b !== null);
      if (all.length === 0) { setError("There is nothing to show in this file."); return; }
      const merged = { min: [0, 1, 2].map((k) => Math.min(...all.map((b) => b!.min[k]))) as [number, number, number], max: [0, 1, 2].map((k) => Math.max(...all.map((b) => b!.max[k]))) as [number, number, number] };
      camera.current = fitCamera(merged);
      gl.enable(gl.DEPTH_TEST);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      const draw = () => {
        const w = el.clientWidth, h = el.clientHeight;
        if (el.width !== w || el.height !== h) { el.width = w; el.height = h; }
        gl.viewport(0, 0, el.width, el.height);
        gl.clearColor(0.96, 0.96, 0.97, 1);
        gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
        gl.uniformMatrix4fv(locs.m, false, viewProjection(camera.current!, el.width / Math.max(el.height, 1)));
        for (const pass of [false, true]) {
          gl.depthMask(!pass);
          for (const g of gpu.filter((x) => x.clear === pass)) {
            for (const [loc, buf, size] of [[locs.p, g.p, 3], [locs.n, g.n, 3], [locs.c, g.c, 4]] as const) {
              gl.bindBuffer(gl.ARRAY_BUFFER, buf);
              gl.enableVertexAttribArray(loc);
              gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
            }
            gl.drawArrays(gl.TRIANGLES, 0, g.count);
          }
        }
        gl.depthMask(true);
      };
      redraw.current = draw;
      draw();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The 3D view could not start.");
    }
  }, [layers]);

  const drag = useRef<{ x: number; y: number } | null>(null);
  return (
    <div>
      {error ? <p role="alert">{error}</p> : (
        <canvas ref={canvas} aria-label={label} role="img" style={{ width: "100%", height: 480, border: "1px solid #888", touchAction: "none", cursor: "grab" }}
          onPointerDown={(e) => { drag.current = { x: e.clientX, y: e.clientY }; e.currentTarget.setPointerCapture(e.pointerId); }}
          onPointerUp={() => { drag.current = null; }}
          onPointerMove={(e) => {
            if (!drag.current || !camera.current) return;
            camera.current = orbit(camera.current, e.clientX - drag.current.x, e.clientY - drag.current.y);
            drag.current = { x: e.clientX, y: e.clientY };
            redraw.current();
          }}
          onWheel={(e) => { if (camera.current) { camera.current = zoom(camera.current, e.deltaY); redraw.current(); } }} />
      )}
      <p style={{ fontSize: 13 }}>Drag to rotate, scroll to zoom. A preview for orientation, not a model for measuring or for compliance.</p>
    </div>
  );
}
