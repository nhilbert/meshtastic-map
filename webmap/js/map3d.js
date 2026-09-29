// 3D view: laser-scan scene with three.js (from the Mesh Bonn viewer), layer features draped on
// the terrain, raster overlays, the current link with line of sight and Fresnel zone.
import { badgeCanvas } from "./icons.js";
import { $, css, fmt, toUTM, toLonLat } from "./util.js";

function decodeBodies(buf) {
  const dv = new DataView(buf);
  const n = dv.getUint32(0, true);
  let p = 8; const out = [];
  for (let i = 0; i < n; i++) {
    const nv = dv.getUint16(p, true), cls = dv.getUint8(p + 2);
    const base = dv.getInt16(p + 4, true), top = dv.getInt16(p + 6, true);
    p += 8;
    const xy = new Float32Array(nv * 2);
    for (let k = 0; k < nv; k++) { xy[2 * k] = dv.getInt32(p, true) * 0.1; xy[2 * k + 1] = dv.getInt32(p + 4, true) * 0.1; p += 8; }
    out.push({ xy, cls, base: base * 0.1, top: top * 0.1 });
  }
  return out;
}

// Ear clipping for the roof polygons (large polygons fall back to a fan).
function triangulate(xy) {
  const n = xy.length / 2;
  if (n < 3) return [];
  if (n > 140) { const t = []; for (let i = 1; i < n - 1; i++) t.push(0, i, i + 1); return t; }
  let area = 0;
  for (let i = 0, j = n - 1; i < n; j = i++) area += (xy[2 * j] * xy[2 * i + 1] - xy[2 * i] * xy[2 * j + 1]);
  const idx = []; for (let i = 0; i < n; i++) idx.push(i);
  if (area < 0) idx.reverse();
  const tri = [];
  const X = i => xy[2 * idx[i]], Y = i => xy[2 * idx[i] + 1];
  const cross = (ax, ay, bx, by, cx, cy) => (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
  let guard = 0;
  while (idx.length > 3 && guard++ < 4000) {
    let clipped = false;
    for (let i = 0; i < idx.length; i++) {
      const p = (i + idx.length - 1) % idx.length, q = i, r = (i + 1) % idx.length;
      const ax = X(p), ay = Y(p), bx = X(q), by = Y(q), cx = X(r), cy = Y(r);
      if (cross(ax, ay, bx, by, cx, cy) <= 0) continue;
      let ok = true;
      for (let k = 0; k < idx.length; k++) {
        if (k === p || k === q || k === r) continue;
        const px = X(k), py = Y(k);
        if (cross(ax, ay, bx, by, px, py) >= 0 && cross(bx, by, cx, cy, px, py) >= 0 && cross(cx, cy, ax, ay, px, py) >= 0) { ok = false; break; }
      }
      if (!ok) continue;
      tri.push(idx[p], idx[q], idx[r]);
      idx.splice(q, 1); clipped = true; break;
    }
    if (!clipped) break;
  }
  if (idx.length === 3) tri.push(idx[0], idx[1], idx[2]);
  if (tri.length === 0) for (let i = 1; i < n - 1; i++) tri.push(0, i, i + 1);
  return tri;
}

export const LAYERS_3D = [
  { id: "bldg", label: "Gebäude (Laserscan)", on: true, sw: "--bldg" },
  { id: "veg", label: "Bewuchs", on: true, sw: "--veg" },
  { id: "roofs", label: "Dachkanten", on: true, sw: "--ink3" },
  { id: "terrain", label: "Gelände (DGM)", on: true, sw: "--terr" },
  { id: "grid", label: "200-m-Raster", on: false, sw: "--ink3" },
  { id: "fresnel", label: "1. Fresnelzone", on: true, sw: "--accent" },
  { id: "edges", label: "Beugungskanten", on: true, sw: "--warn" },
  { id: "data", label: "Datenebenen", on: true, sw: "--accent" },
];

export class Map3D {
  constructor(canvas, handlers) {
    this.cvs = canvas;
    this.h = handlers;         // { onPick(lat, lon), onFeature(feature) }
    this.vex = 1; this.fresScale = 1;
    this.state = Object.fromEntries(LAYERS_3D.map(l => [l.id, l.on]));
    this.dataLayers = {};      // id -> THREE.Group
    this.link = null;
  }

  async load() {
    const get = (f, bin) => fetch("scene/" + f, { cache: "no-cache" }).then(r => {
      if (!r.ok) throw new Error(`scene/${f}: HTTP ${r.status}`);
      return bin ? r.arrayBuffer() : r.json();
    });
    const [meta, terr, bodies] = await Promise.all([get("scene_meta.json"), get("terrain.i16", 1), get("bodies.bin", 1)]);
    this.meta = meta; this.terr = new Int16Array(terr); this.bodies = decodeBodies(bodies);
    this.init();
  }

  // ------------------------------------------------------------ scene
  init() {
    const m = this.meta;
    const W = m.nx * m.res_terrain, H = m.ny * m.res_terrain;
    this.cx = m.bbox[0] + W / 2; this.cy = m.bbox[1] + H / 2; this.z0 = m.z0;
    const ren = new THREE.WebGLRenderer({ canvas: this.cvs, antialias: true });
    ren.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    const scene = new THREE.Scene();
    const cam = new THREE.PerspectiveCamera(42, 1, 5, 20000);
    const root = new THREE.Group(); scene.add(root);          // carries the vertical exaggeration
    Object.assign(this, { ren, scene, cam, root });
    scene.add(new THREE.HemisphereLight(0xffffff, 0x4a5560, 0.85));
    const sun = new THREE.DirectionalLight(0xffffff, 0.75); sun.position.set(-0.55, 1.0, 0.42).multiplyScalar(1000); scene.add(sun);
    const fill = new THREE.DirectionalLight(0xffffff, 0.22); fill.position.set(0.7, 0.35, -0.6).multiplyScalar(1000); scene.add(fill);
    this.buildTerrain(); this.buildBodies(); this.buildGrid();
    this.linkGroup = new THREE.Group(); root.add(this.linkGroup);
    this.orbit = { tx: 0, ty: 0, tz: 0, dist: 2600, az: -0.62, el: 0.5 };
    this.bindCamera();
    new ResizeObserver(() => this.resize()).observe(this.cvs.parentElement); this.resize();
    this.applyTheme(); this.applyLayers();
    ren.setAnimationLoop(() => { this.updateCam(); ren.render(scene, cam); });
  }

  terrH(ix, iy) {
    const m = this.meta;
    ix = Math.max(0, Math.min(m.nx - 1, ix)); iy = Math.max(0, Math.min(m.ny - 1, iy));
    return m.z0 + this.terr[iy * m.nx + ix] * 0.1;
  }
  terrAt(x, y) {
    const m = this.meta, r = m.res_terrain;
    const fx = (x - m.bbox[0]) / r - 0.5, fy = (y - m.bbox[1]) / r - 0.5;
    const i = Math.floor(fx), j = Math.floor(fy), u = fx - i, v = fy - j;
    return this.terrH(i, j) * (1 - u) * (1 - v) + this.terrH(i + 1, j) * u * (1 - v)
      + this.terrH(i, j + 1) * (1 - u) * v + this.terrH(i + 1, j + 1) * u * v;
  }
  toLocal(x, y, z) { return [x - this.cx, z - this.z0, -(y - this.cy)]; }
  inside(x, y) { const b = this.meta.bbox; return x >= b[0] && x <= b[2] && y >= b[1] && y <= b[3]; }

  buildTerrain() {
    const m = this.meta, nx = m.nx, ny = m.ny, r = m.res_terrain;
    const pos = new Float32Array(nx * ny * 3), col = new Float32Array(nx * ny * 3);
    let zmin = 1e9, zmax = -1e9;
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) { const z = this.terrH(i, j); zmin = Math.min(zmin, z); zmax = Math.max(zmax, z); }
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      const k = (j * nx + i) * 3, z = this.terrH(i, j);
      const L = this.toLocal(m.bbox[0] + (i + 0.5) * r, m.bbox[1] + (j + 0.5) * r, z);
      pos[k] = L[0]; pos[k + 1] = L[1]; pos[k + 2] = L[2];
      col[k] = col[k + 1] = col[k + 2] = 0.55 + 0.45 * (z - zmin) / Math.max(1, zmax - zmin);
    }
    const idx = new Uint32Array((nx - 1) * (ny - 1) * 6); let p = 0;
    for (let j = 0; j < ny - 1; j++) for (let i = 0; i < nx - 1; i++) {
      const a = j * nx + i, b = a + 1, c = a + nx, d = c + 1;
      idx[p++] = a; idx[p++] = c; idx[p++] = b; idx[p++] = b; idx[p++] = c; idx[p++] = d;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.BufferAttribute(col, 3));
    g.setIndex(new THREE.BufferAttribute(idx, 1)); g.computeVertexNormals();
    this.terrMat = new THREE.MeshLambertMaterial({ vertexColors: true });
    this.terrain = new THREE.Mesh(g, this.terrMat); this.root.add(this.terrain);
  }

  buildBodies() {
    const m = this.meta; this.body = {}; this.faceBody = {};
    for (const G of [{ cls: 1, key: "bldg" }, { cls: 2, key: "veg" }]) {
      const list = this.bodies.filter(b => b.cls === G.cls);
      let nv = 0;
      const tris = list.map(b => { const t = triangulate(b.xy); nv += t.length + (b.xy.length / 2) * 6; return t; });
      const pos = new Float32Array(nv * 3), col = new Float32Array(nv * 3), owner = new Int32Array(nv / 3);
      let p = 0, f = 0;
      const push = (x, y, z, sh) => { const L = this.toLocal(x, y, z); pos.set(L, p); col.set(sh, p); p += 3; };
      list.forEach((b, bi) => {
        const zt = m.z0 + b.top, zb = m.z0 + b.base, h = b.top - b.base;
        const s = G.cls === 1 ? Math.min(1, 0.52 + h / 55) : 0.6 + Math.min(0.3, h / 40);
        const roofC = G.cls === 1 ? [s * 0.92, s * 0.97, s] : [s * 0.62, s * 0.86, s * 0.6];
        const wallC = G.cls === 1 ? [s * 0.66, s * 0.71, s * 0.76] : [s * 0.46, s * 0.62, s * 0.45];
        const t = tris[bi], n = b.xy.length / 2;
        for (let k = 0; k < t.length; k += 3) {
          for (const q of [t[k], t[k + 1], t[k + 2]]) push(b.xy[2 * q] + m.bbox[0], b.xy[2 * q + 1] + m.bbox[1], zt, roofC);
          owner[f++] = bi;
        }
        for (let k = 0; k < n; k++) {
          const k2 = (k + 1) % n;
          const ax = b.xy[2 * k] + m.bbox[0], ay = b.xy[2 * k + 1] + m.bbox[1];
          const bx = b.xy[2 * k2] + m.bbox[0], by = b.xy[2 * k2 + 1] + m.bbox[1];
          push(ax, ay, zb, wallC); push(bx, by, zb, wallC); push(bx, by, zt, wallC); owner[f++] = bi;
          push(ax, ay, zb, wallC); push(bx, by, zt, wallC); push(ax, ay, zt, wallC); owner[f++] = bi;
        }
      });
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(pos.subarray(0, p), 3));
      g.setAttribute("color", new THREE.BufferAttribute(col.subarray(0, p), 3));
      g.computeVertexNormals();
      const mat = new THREE.MeshLambertMaterial({ vertexColors: true, flatShading: true, transparent: G.cls === 2, opacity: G.cls === 2 ? 0.82 : 1 });
      const mesh = new THREE.Mesh(g, mat);
      this.body[G.key] = mesh; this.faceBody[G.key] = { owner, list }; this.root.add(mesh);
    }
    const segs = [];
    for (const b of this.bodies) {
      if (b.cls !== 1) continue;
      const n = b.xy.length / 2, zt = m.z0 + b.top;
      for (let k = 0; k < n; k++) {
        const k2 = (k + 1) % n;
        segs.push(...this.toLocal(b.xy[2 * k] + m.bbox[0], b.xy[2 * k + 1] + m.bbox[1], zt));
        segs.push(...this.toLocal(b.xy[2 * k2] + m.bbox[0], b.xy[2 * k2 + 1] + m.bbox[1], zt));
      }
    }
    const eg = new THREE.BufferGeometry(); eg.setAttribute("position", new THREE.Float32BufferAttribute(segs, 3));
    this.edgeMat = new THREE.LineBasicMaterial({ transparent: true, opacity: 0.5 });
    this.roofs = new THREE.LineSegments(eg, this.edgeMat); this.root.add(this.roofs);
  }

  buildGrid() {
    const m = this.meta, step = 200, segs = [];
    const x0 = Math.ceil(m.bbox[0] / step) * step, x1 = m.bbox[0] + m.nx * m.res_terrain;
    const y0 = Math.ceil(m.bbox[1] / step) * step, y1 = m.bbox[1] + m.ny * m.res_terrain;
    const seg = (xa, ya, xb, yb) => { segs.push(...this.toLocal(xa, ya, this.terrAt(xa, ya) + 0.4), ...this.toLocal(xb, yb, this.terrAt(xb, yb) + 0.4)); };
    for (let x = x0; x <= x1; x += step) for (let y = y0; y < y1; y += 40) seg(x, y, x, Math.min(y + 40, y1));
    for (let y = y0; y <= y1; y += step) for (let x = x0; x < x1; x += 40) seg(x, y, Math.min(x + 40, x1), y);
    const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.Float32BufferAttribute(segs, 3));
    this.gridMat = new THREE.LineBasicMaterial({ transparent: true, opacity: 0.35 });
    this.grid = new THREE.LineSegments(g, this.gridMat); this.root.add(this.grid);
  }

  // ------------------------------------------------------------ data layers
  clear(id) {
    const g = this.dataLayers[id];
    if (!g) return;
    this.root.remove(g);
    g.traverse(o => { o.geometry && o.geometry.dispose(); if (o.material) { o.material.map && o.material.map.dispose(); o.material.dispose(); } });
    delete this.dataLayers[id];
  }

  show(id, payload) {
    if (!this.meta) return;
    this.clear(id);
    const g = new THREE.Group(); g.visible = this.state.data;
    const raster = payload.type === "raster" ? payload : payload.raster;
    if (raster && raster.image) g.add(this.drape(raster));
    for (const f of payload.features || []) {
      const obj = this.featureObject(f);
      if (obj) g.add(obj);
    }
    this.dataLayers[id] = g; this.root.add(g);
  }

  featureObject(f) {
    const p = f.properties || {}, s = p._style || {}, geom = f.geometry;
    if (!geom) return null;
    if (geom.type === "Point") {
      const [x, y] = toUTM(...geom.coordinates);
      if (!this.inside(x, y)) return null;
      if (p._icon) return this.badgeObject(f, x, y);
      const r = Math.max(3, (s.radius || 6) * 0.9);
      const mesh = new THREE.Mesh(new THREE.SphereGeometry(r, 12, 8),
        new THREE.MeshLambertMaterial({ color: new THREE.Color(s.fillColor || s.color || "#3388ff") }));
      mesh.position.set(...this.toLocal(x, y, this.terrAt(x, y) + (p._z ?? 2) + r));
      mesh.userData.feature = f;
      return mesh;
    }
    const lines = geom.type === "LineString" ? [geom.coordinates]
      : geom.type === "Polygon" ? geom.coordinates : geom.type === "MultiLineString" ? geom.coordinates : [];
    if (!lines.length) return null;
    const pts = [];
    for (const ln of lines) {
      for (let i = 0; i < ln.length - 1; i++) {
        const [xa, ya] = toUTM(...ln[i]), [xb, yb] = toUTM(...ln[i + 1]);
        const n = Math.max(1, Math.ceil(Math.hypot(xb - xa, yb - ya) / 10));
        for (let k = 0; k < n; k++) {
          const t0 = k / n, t1 = (k + 1) / n;
          const x0 = xa + (xb - xa) * t0, y0 = ya + (yb - ya) * t0, x1 = xa + (xb - xa) * t1, y1 = ya + (yb - ya) * t1;
          if (!this.inside(x0, y0) || !this.inside(x1, y1)) continue;
          pts.push(...this.toLocal(x0, y0, this.terrAt(x0, y0) + 1.5), ...this.toLocal(x1, y1, this.terrAt(x1, y1) + 1.5));
        }
      }
    }
    const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    return new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: new THREE.Color(s.color || "#3388ff"), transparent: true, opacity: s.opacity ?? 0.9 }));
  }

  // Badge marker: a dot at the point and the label as a sprite above it, joined by a stem.
  badgeObject(f, x, y) {
    const icon = f.properties._icon, z0 = this.terrAt(x, y) + (f.properties._z ?? 2);
    const g = new THREE.Group();
    const dot = new THREE.Mesh(new THREE.SphereGeometry(2.5, 10, 6), new THREE.MeshBasicMaterial({ color: new THREE.Color(icon.color || "#3388ff") }));
    dot.position.set(...this.toLocal(x, y, z0)); dot.userData.feature = f; g.add(dot);
    const lift = 22;   // label height above the point [m], clears most roofs nearby
    const stem = new THREE.BufferGeometry();
    stem.setAttribute("position", new THREE.Float32BufferAttribute([...this.toLocal(x, y, z0), ...this.toLocal(x, y, z0 + lift)], 3));
    g.add(new THREE.Line(stem, new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.6 })));
    const { canvas, aspect } = badgeCanvas(icon);
    const tex = new THREE.CanvasTexture(canvas); tex.minFilter = THREE.LinearFilter;
    // Fixed screen size (not shrinking with distance) and always drawn on top of buildings.
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
      map: tex, transparent: true, opacity: icon.faded ? 0.55 : 1, sizeAttenuation: false, depthTest: false,
    }));
    const hgt = 0.045;   // fraction of the view height
    sprite.scale.set(hgt * aspect, hgt, 1);
    sprite.renderOrder = 10;
    sprite.center.set(0.5, 0);   // anchor at the bottom (the tail tip)
    sprite.position.set(...this.toLocal(x, y, z0 + lift));
    sprite.userData.feature = f; g.add(sprite);
    return g;
  }

  // Raster (lat/lon aligned) as a textured mesh following the terrain.
  drape(raster) {
    const [[s, w], [n, e]] = raster.bounds, N = 96;
    const pos = [], uv = [], idx = [];
    for (let j = 0; j <= N; j++) for (let i = 0; i <= N; i++) {
      const lon = w + (e - w) * i / N, lat = s + (n - s) * j / N;
      const [x, y] = toUTM(lon, lat);
      const cx = Math.min(Math.max(x, this.meta.bbox[0]), this.meta.bbox[2]), cy = Math.min(Math.max(y, this.meta.bbox[1]), this.meta.bbox[3]);
      pos.push(...this.toLocal(x, y, this.terrAt(cx, cy) + 1.0)); uv.push(i / N, j / N);
    }
    for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) {
      const a = j * (N + 1) + i, b = a + 1, c = a + N + 1, d = c + 1;
      idx.push(a, b, c, b, d, c);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2)); g.setIndex(idx);
    const tex = new THREE.TextureLoader().load(raster.image);
    tex.magFilter = THREE.NearestFilter;
    return new THREE.Mesh(g, new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false, side: THREE.DoubleSide }));
  }

  // ------------------------------------------------------------ link
  setLink(res) {
    this.link = res;
    if (!this.linkGroup) return;   // scene not loaded yet; drawn after load()
    this.clearGroup(this.linkGroup);
    if (!res) return;
    const L = res.profile, G = res.geometry, A = L.a_utm, B = L.b_utm, N = L.d.length;
    const za = G.z_a_nhn, zb = G.z_b_nhn;
    const pt = t => { const u = t / L.L; return this.toLocal(A[0] + (B[0] - A[0]) * u, A[1] + (B[1] - A[1]) * u, za + (zb - za) * u); };
    const vv = [], cc = [];
    const cOk = new THREE.Color(css("--ok")), cWarn = new THREE.Color(css("--warn")), cBad = new THREE.Color(css("--bad"));
    for (let i = 0; i < N - 1; i++) {
      const cl = G.clear_r1[i]; const c = (cl === null) ? cOk : (cl > 0.6 ? cOk : cl > -0.6 ? cWarn : cBad);
      vv.push(...pt(L.d[i]), ...pt(L.d[i + 1])); cc.push(c.r, c.g, c.b, c.r, c.g, c.b);
    }
    const lg = new THREE.BufferGeometry();
    lg.setAttribute("position", new THREE.Float32BufferAttribute(vv, 3));
    lg.setAttribute("color", new THREE.Float32BufferAttribute(cc, 3));
    this.linkGroup.add(new THREE.LineSegments(lg, new THREE.LineBasicMaterial({ vertexColors: true })));
    // first Fresnel zone as a tube
    const k = Math.max(1, Math.floor(N / 220)), ring = 14;
    const ax = new THREE.Vector3(...pt(L.L)).sub(new THREE.Vector3(...pt(0))).normalize();
    const up = Math.abs(ax.y) > 0.9 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0);
    const e1 = new THREE.Vector3().crossVectors(ax, up).normalize(), e2 = new THREE.Vector3().crossVectors(ax, e1).normalize();
    const fp = [], fi = []; let rows = 0;
    for (let i = 0; i < N; i += k) {
      const c = new THREE.Vector3(...pt(L.d[i])), r = (G.r1_m[i] || 0) * this.fresScale;
      for (let s = 0; s < ring; s++) {
        const a = s / ring * Math.PI * 2;
        fp.push(c.x + (e1.x * Math.cos(a) + e2.x * Math.sin(a)) * r, c.y + (e1.y * Math.cos(a) + e2.y * Math.sin(a)) * r, c.z + (e1.z * Math.cos(a) + e2.z * Math.sin(a)) * r);
      }
      rows++;
    }
    for (let i = 0; i < rows - 1; i++) for (let s = 0; s < ring; s++) {
      const a = i * ring + s, b = i * ring + (s + 1) % ring; fi.push(a, a + ring, b, b, a + ring, b + ring);
    }
    const fg = new THREE.BufferGeometry(); fg.setAttribute("position", new THREE.Float32BufferAttribute(fp, 3)); fg.setIndex(fi);
    this.fresnel = new THREE.Mesh(fg, new THREE.MeshBasicMaterial({ color: new THREE.Color(css("--accent")), transparent: true, opacity: 0.13, side: THREE.DoubleSide, depthWrite: false }));
    this.linkGroup.add(this.fresnel);
    // masts at A and B
    for (const [P, hg, ztop] of [[A, G.h_a_m, za], [B, G.h_b_m, zb]]) {
      const mg = new THREE.BufferGeometry();
      mg.setAttribute("position", new THREE.Float32BufferAttribute([...this.toLocal(P[0], P[1], ztop - hg), ...this.toLocal(P[0], P[1], ztop + 4)], 3));
      this.linkGroup.add(new THREE.Line(mg, new THREE.LineBasicMaterial({ color: new THREE.Color(css("--accent")) })));
      const s = new THREE.Mesh(new THREE.OctahedronGeometry(6), new THREE.MeshBasicMaterial({ color: new THREE.Color(css("--accent")) }));
      s.position.set(...this.toLocal(P[0], P[1], ztop)); this.linkGroup.add(s);
    }
    // strongest diffraction edges
    this.edgeLines = [];
    for (const e of G.edges.slice(0, 4)) {
      const u = e.d_m / L.L, x = A[0] + (B[0] - A[0]) * u, y = A[1] + (B[1] - A[1]) * u;
      const g2 = new THREE.BufferGeometry();
      g2.setAttribute("position", new THREE.Float32BufferAttribute([...this.toLocal(x, y, e.z_nhn), ...this.toLocal(x, y, e.z_nhn + 18)], 3));
      const ln = new THREE.Line(g2, new THREE.LineBasicMaterial({ color: new THREE.Color(css("--warn")) }));
      this.edgeLines.push(ln); this.linkGroup.add(ln);
    }
    this.applyLayers();
  }
  clearGroup(g) { while (g.children.length) { const c = g.children.pop(); c.geometry && c.geometry.dispose(); } }

  frameLink() {
    if (!this.link || !this.meta) return;
    const L = this.link.profile, G = this.link.geometry;
    const loc = this.toLocal((L.a_utm[0] + L.b_utm[0]) / 2, (L.a_utm[1] + L.b_utm[1]) / 2, (G.z_a_nhn + G.z_b_nhn) / 2);
    const az = Math.atan2(L.b_utm[0] - L.a_utm[0], -(L.b_utm[1] - L.a_utm[1]));
    this.flyTo({ tx: loc[0], ty: loc[1] * this.vex, tz: loc[2], dist: Math.max(250, L.L * 1.2), az: az + Math.PI / 2, el: 0.32 });
  }
  overview() { if (this.meta) this.flyTo({ tx: 0, ty: 0, tz: 0, dist: 3200, az: -0.62, el: 0.55 }); }
  focusOn(lat, lon) {
    const [x, y] = toUTM(lon, lat);
    if (!this.inside(x, y)) return;
    const loc = this.toLocal(x, y, this.terrAt(x, y));
    this.flyTo({ tx: loc[0], ty: loc[1] * this.vex, tz: loc[2], dist: Math.min(this.orbit.dist, 900) });
  }

  // ------------------------------------------------------------ camera, picking
  bindCamera() {
    const cvs = this.cvs, o = () => this.orbit;
    let drag = null;
    cvs.addEventListener("pointerdown", e => { cvs.setPointerCapture(e.pointerId); drag = { x: e.clientX, y: e.clientY, x0: e.clientX, y0: e.clientY, pan: e.shiftKey || e.button === 2 || e.button === 1 }; });
    cvs.addEventListener("pointermove", e => {
      if (!drag) { this.hover(e); return; }
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.x = e.clientX; drag.y = e.clientY;
      const ob = o();
      if (drag.pan) {
        // Grab the ground: move the target against the drag so the point under the cursor
        // follows it. s = metres per pixel at the target; sideways along the screen's right
        // vector, up/down along the view direction, stretched by the ground's foreshortening.
        const s = 2 * ob.dist * Math.tan(THREE.MathUtils.degToRad(this.cam.fov / 2)) / (cvs.clientHeight || 1);
        const fwd = dy * s / Math.max(Math.sin(ob.el), 0.2);
        ob.tx -= dx * s * Math.cos(ob.az) + fwd * Math.sin(ob.az);
        ob.tz -= -dx * s * Math.sin(ob.az) + fwd * Math.cos(ob.az);
      } else { ob.az -= dx * 0.005; ob.el = Math.max(0.05, Math.min(1.52, ob.el + dy * 0.005)); }
    });
    cvs.addEventListener("pointerup", e => {
      if (drag && Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) < 4 && e.button === 0) this.click(e);
      drag = null; try { cvs.releasePointerCapture(e.pointerId); } catch (_) { }
    });
    cvs.addEventListener("pointercancel", () => { drag = null; });
    cvs.addEventListener("contextmenu", e => e.preventDefault());
    cvs.addEventListener("wheel", e => { e.preventDefault(); o().dist = Math.max(60, Math.min(9000, o().dist * Math.exp(e.deltaY * 0.0011))); }, { passive: false });
  }
  raycast(e) {
    const r = this.cvs.getBoundingClientRect();
    const ndc = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    const ray = new THREE.Raycaster(); ray.setFromCamera(ndc, this.cam);
    const targets = [this.terrain];
    if (this.body.bldg.visible) targets.push(this.body.bldg);
    if (this.body.veg.visible) targets.push(this.body.veg);
    for (const g of Object.values(this.dataLayers)) if (g.visible) g.traverse(c => c.userData.feature && targets.push(c));
    return ray.intersectObjects(targets, false)[0];
  }
  hitUTM(hit) { const p = hit.point.clone(); this.root.worldToLocal(p); return [p.x + this.cx, -p.z + this.cy, p.y + this.z0]; }
  click(e) {
    const hit = this.raycast(e);
    if (!hit) return;
    if (hit.object.userData.feature) { this.h.onFeature(hit.object.userData.feature); return; }
    const [x, y] = this.hitUTM(hit), [lon, lat] = toLonLat(x, y);
    this.h.onPick(lat, lon);
  }
  hover(e) {
    const hit = this.raycast(e), out = $("#readout");
    if (!hit) { out.textContent = "—"; return; }
    if (hit.object.userData.feature) { out.textContent = hit.object.userData.feature.properties._title || "Objekt"; return; }
    const [E, N, Z] = this.hitUTM(hit), gz = this.terrAt(E, N);
    let extra = "";
    for (const key of ["bldg", "veg"]) {
      if (hit.object === this.body[key]) {
        const fb = this.faceBody[key], b = fb.list[fb.owner[hit.faceIndex]];
        if (b) extra = `\n${key === "bldg" ? "Gebäude" : "Bewuchs"}  ${fmt(b.top - b.base, 1)} m ü. Grund`;
      }
    }
    out.textContent = `E ${E.toFixed(0)}  N ${N.toFixed(0)}  (EPSG:25832)\nGelände ${gz.toFixed(1)} m NHN   Treffer ${Z.toFixed(1)} m NHN${extra}`;
  }
  updateCam() {
    const o = this.orbit, c = this.cam;
    if (this.fly) {
      const t = Math.min(1, (performance.now() - this.fly.t0) / this.fly.dur), k = t * t * (3 - 2 * t);
      for (const p of ["tx", "ty", "tz", "dist", "az", "el"]) o[p] = this.fly.a[p] + (this.fly.b[p] - this.fly.a[p]) * k;
      if (t >= 1) this.fly = null;
    }
    const ce = Math.cos(o.el), se = Math.sin(o.el);
    c.position.set(o.tx + o.dist * ce * Math.sin(o.az), o.ty + o.dist * se, o.tz + o.dist * ce * Math.cos(o.az));
    c.lookAt(o.tx, o.ty, o.tz);
  }
  flyTo(t, dur) {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) { Object.assign(this.orbit, t); return; }
    this.fly = { a: { ...this.orbit }, b: { ...this.orbit, ...t }, t0: performance.now(), dur: dur || 900 };
  }
  resize() {
    const w = this.cvs.parentElement.clientWidth, h = this.cvs.parentElement.clientHeight;
    if (!w || !h || !this.ren) return;
    this.ren.setSize(w, h, false); this.cam.aspect = w / h; this.cam.updateProjectionMatrix();
  }

  // ------------------------------------------------------------ settings
  setLayer(id, on) { this.state[id] = on; this.applyLayers(); }
  applyLayers() {
    if (!this.body) return;
    const s = this.state;
    this.body.bldg.visible = s.bldg; this.body.veg.visible = s.veg;
    this.roofs.visible = s.roofs && s.bldg; this.terrain.visible = s.terrain; this.grid.visible = s.grid;
    if (this.fresnel) this.fresnel.visible = s.fresnel;
    (this.edgeLines || []).forEach(l => { l.visible = s.edges; });
    Object.values(this.dataLayers).forEach(g => { g.visible = s.data; });
  }
  setVex(v) { this.vex = v; if (this.root) this.root.scale.y = v; }
  setFresnel(v) { this.fresScale = v; if (this.link) this.setLink(this.link); }
  applyTheme() {
    if (!this.ren) return;
    const bg = new THREE.Color(css("--sky-a"));
    this.ren.setClearColor(bg, 1); this.scene.fog = new THREE.Fog(bg, 2500, 9000);
    this.edgeMat.color = new THREE.Color(css("--ink3")); this.gridMat.color = new THREE.Color(css("--line"));
    this.terrMat.color = new THREE.Color(css("--terr"));
    this.body.bldg.material.color = new THREE.Color(css("--bldg")); this.body.veg.material.color = new THREE.Color(css("--veg"));
    if (this.link) this.setLink(this.link);
  }
}
