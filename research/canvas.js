"use strict";
const D = JSON.parse(document.getElementById("atlas-data").textContent);
// dataset.json leaves out fields that follow from others. Rebuild them here,
// only where missing, so D and the dataset download are the full records.
const REASONS = {
  reference:
    "Reference material retained for its narrower scope or release limitation.",
  both: "An assembled system and a focused research contribution; the same project appears in both contexts.",
  single:
    "Independently identifiable project. Its role is explained by the inspected sources.",
};
const assemblyOf = new Map(
  D.canvas.assemblyGroups.flatMap((g) => g.projects.map((id) => [id, g.id])),
);
for (const p of D.projects) {
  p.identity ??= "project";
  p.referenceOnly ??= p.views.length === 0;
  p.assemblyGroup ??= assemblyOf.get(p.id) ?? null;
  p.classificationReason ??= p.referenceOnly
    ? REASONS.reference
    : p.views.length > 1
      ? REASONS.both
      : REASONS.single;
  if (p.sources[0]?.url !== p.repo)
    p.sources.unshift({
      label: "Project repository / documentation",
      url: p.repo,
    });
}
for (const e of D.edges)
  e.id ??= [e.a, e.b, e.type.toLowerCase().replace(/[^a-z0-9]+/g, "-")].join(
    "--",
  );
const P = new Map(D.projects.map((p) => [p.id, p])),
  E = new Map(D.edges.map((e) => [e.id, e]));
const $ = (s) => document.querySelector(s),
  $$ = (s) => [...document.querySelectorAll(s)],
  esc = (s) =>
    String(s ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
const colors = {
  Systems: "#245bd7",
  Perception: "#187b79",
  Mapping: "#2e7f98",
  Planning: "#a75c25",
  Control: "#536782",
  Learning: "#735398",
  Simulation: "#647949",
};
const TYPES = {
  Uses: {
    color: "#245bd7",
    dash: "",
    class: "uses",
    description:
      "A documented project is incorporated in the assembly. The note identifies the branch, fork or configuration where established.",
  },
  Optional: {
    color: "#187b79",
    dash: "8 6",
    class: "optional",
    description:
      "A selectable module or integration path. It is not required by every configuration.",
  },
  "Code reuse": {
    color: "#365b9a",
    dash: "13 5 3 5",
    class: "code",
    description:
      "Code or a package is copied or adapted from another project. This does not imply using the whole upstream system.",
  },
  "Research lineage": {
    color: "#a75c25",
    dash: "13 7",
    class: "lineage",
    description:
      "An acknowledged intellectual or research relationship. It is not a claim of runtime integration.",
  },
  Evaluation: {
    color: "#735398",
    dash: "2 6",
    class: "evaluation",
    description:
      "A benchmark, training or experimental relationship. It does not establish software integration into the flying system.",
  },
};
const S = {
  mode: "assembled",
  scene: "landscape",
  selected: null,
  context: "cerlab",
  group: "navigation",
  root: null,
  index: innerWidth > 1100,
  types: new Set(["Uses", "Optional", "Code reuse"]),
  expanded: new Set(),
  assemblyExpansions: {},
  cam: { x: 0, y: 0, k: 1 },
  memories: {},
  history: [],
  compare: [],
  search: "",
  searchIndex: 0,
  sheet: null,
  libraryQuery: "",
  libraryScope: "all",
  libraryPage: 0,
  auditTopic: "All topics",
  auditVerdict: "All verdicts",
};
let SC = {
    nodes: [],
    edges: [],
    frames: [],
    zones: [],
    bounds: { x: 0, y: 0, w: 1120, h: 700 },
  },
  suppressClick = false,
  resizeTimer;
try {
  S.compare = JSON.parse(localStorage.getItem("drone-atlas-comparison") || "[]")
    .filter((id) => P.has(id))
    .slice(0, 4);
} catch (e) {}
const outgoing = (id) => D.edges.filter((e) => e.a === id),
  related = (id) => D.edges.filter((e) => e.a === id || e.b === id),
  other = (e, id) => (e.a === id ? e.b : e.a);
const projectButton = (id, label) =>
  P.has(id)
    ? `<button class="appearance" data-project="${id}">${esc(label || P.get(id).name)}</button>`
    : "";
const link = (url, label, cls = "") =>
  `<a class="${cls}" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`;
function badge(value) {
  const cl = /Not runnable|Archived/.test(value)
    ? "bad"
    : /Restricted|Legacy|gaps|license|Commercial/.test(value)
      ? "warn"
      : /Established/.test(value)
        ? "green"
        : "";
  return `<span class="tag ${cl}">${esc(value)}</span>`;
}
function sourceList(sources) {
  return `<ul class="source-list">${sources.map((s) => `<li>${link(s.url, s.label)}<small>${esc(s.url.replace("https://", ""))}</small></li>`).join("")}</ul>`;
}
function toast(text) {
  $("#toast").textContent = text;
  $("#toast").hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ($("#toast").hidden = true), 3000);
}
function activeEdges() {
  return D.edges.filter(
    (e) => S.types.has(e.type) && e.scope !== "project origin",
  );
}
function snapshot() {
  return {
    mode: S.mode,
    scene: S.scene,
    selected: S.selected,
    context: S.context,
    group: S.group,
    root: S.root,
    cam: { ...S.cam },
    expanded: [...S.expanded],
  };
}
function remember() {
  S.history.push(snapshot());
  if (S.history.length > 35) S.history.shift();
  $("#back-button").disabled = false;
}
function goBack() {
  const s = S.history.pop();
  if (!s) return;
  Object.assign(S, s);
  S.expanded = new Set(s.expanded);
  draw();
  syncHash();
  $("#back-button").disabled = S.history.length === 0;
}
function saveCompare() {
  try {
    localStorage.setItem("drone-atlas-comparison", JSON.stringify(S.compare));
  } catch (e) {}
  $("#compare-count").textContent = S.compare.length;
}
function toggleCompare(id) {
  if (S.compare.includes(id)) S.compare = S.compare.filter((x) => x !== id);
  else if (S.compare.length < 4) S.compare.push(id);
  else {
    toast("Keep up to four projects in comparison. Remove one to add another.");
    return;
  }
  saveCompare();
  if (S.sheet === "compare") renderComparison();
  else renderInspector();
  toast(
    S.compare.includes(id)
      ? P.get(id).name + " added to comparison."
      : "Removed from comparison.",
  );
}
function compareButton(id) {
  const on = S.compare.includes(id);
  return `<button class="btn ${on ? "active" : ""}" data-compare="${id}" aria-pressed="${on}">${on ? "✓ In comparison" : "+ Compare"}</button>`;
}
function usableRect() {
  const r = $("#workspace").getBoundingClientRect(),
    mobile = innerWidth <= 580;
  let l =
      S.index && !mobile
        ? $("#index").getBoundingClientRect().right - r.left + 18
        : 24,
    rr =
      S.selected && !mobile
        ? $("#inspector").getBoundingClientRect().width + 42
        : 24;
  if (innerWidth <= 850 && S.selected && !mobile) l = 24;
  return {
    x: l,
    y: mobile ? 63 : 56,
    w: Math.max(220, r.width - l - rr),
    h: Math.max(
      240,
      r.height -
        (mobile && S.selected
          ? Math.min(r.height * 0.7, 500) + 90
          : mobile
            ? 150
            : 155),
    ),
  };
}
function transform() {
  const c = S.cam;
  $("#world").style.transform = `translate(${c.x}px,${c.y}px) scale(${c.k})`;
  $("#workspace").style.setProperty("--zoom", c.k);
  $("#workspace").classList.toggle("far", c.k < 0.27);
  $("#zoom-label").textContent = Math.round(c.k * 100) + "%";
  $("#canvas").style.backgroundSize = 24 * c.k + "px " + 24 * c.k + "px";
  $("#canvas").style.backgroundPosition = `${c.x}px ${c.y}px`;
}
function fitRect(rect, { all = false } = {}) {
  const u = usableRect();
  let k = Math.min(u.w / (rect.w + 60), u.h / (rect.h + 60), 1.12);
  k = Math.max(0.12, k);
  S.cam = {
    x: u.x + u.w / 2 - (rect.x + rect.w / 2) * k,
    y: u.y + u.h / 2 - (rect.y + rect.h / 2) * k,
    k,
  };
  transform();
}
function zoomAt(factor, x, y) {
  const w = $("#workspace").getBoundingClientRect();
  x ??= w.width / 2;
  y ??= w.height / 2;
  const c = S.cam,
    k = Math.max(0.12, Math.min(2.3, c.k * factor)),
    wx = (x - c.x) / c.k,
    wy = (y - c.y) / c.k;
  S.cam = { x: x - wx * k, y: y - wy * k, k };
  transform();
}
function currentRect() {
  if (S.scene === "neighborhood") return SC.frames[0] || SC.bounds;
  if (S.mode === "assembled")
    return (
      SC.frames.find((f) => f.id === S.context) || SC.frames[0] || SC.bounds
    );
  return SC.zones.find((z) => z.id === S.group) || SC.zones[0] || SC.bounds;
}
function fitCurrent() {
  fitRect(currentRect());
  if (currentRect().h > 1400)
    toast(
      "This graph extends beyond the viewport at readable scale. Pan or use Overview.",
    );
}
function introduceGroup() {
  const z = currentRect(),
    u = usableRect();
  const k = Math.max(0.45, Math.min(0.9, u.w / (z.w + 60)));
  S.cam = { x: u.x + 20 - z.x * k, y: u.y + 25 - z.y * k, k };
  transform();
}
function centerNode(n) {
  if (!n) return;
  const u = usableRect();
  S.cam.x = u.x + u.w / 2 - (n.x + n.w / 2) * S.cam.k;
  S.cam.y = u.y + u.h * 0.38 - (n.y + n.h / 2) * S.cam.k;
  transform();
}
function sceneBounds() {
  const all = [...SC.frames, ...SC.zones, ...SC.nodes];
  if (!all.length) return { x: 0, y: 0, w: 1100, h: 700 };
  let minX = Math.min(...all.map((n) => n.x)),
    minY = Math.min(...all.map((n) => n.y)),
    maxX = Math.max(...all.map((n) => n.x + n.w)),
    maxY = Math.max(...all.map((n) => n.y + n.h));
  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
}
function graphMembers(root, expanded, incoming = false) {
  const edges = activeEdges(),
    ids = new Set([root]);
  for (const e of edges)
    if (e.a === root || (incoming && e.b === root)) ids.add(other(e, root));
  let changed = true;
  while (changed) {
    changed = false;
    for (const id of expanded) {
      if (!ids.has(id)) continue;
      for (const e of edges)
        if (e.a === id || (incoming && e.b === id)) {
          const o = other(e, id);
          if (!ids.has(o)) {
            ids.add(o);
            changed = true;
          }
        }
    }
  }
  return {
    ids: [...ids],
    edges: edges.filter((e) => ids.has(e.a) && ids.has(e.b)),
  };
}
function addCluster(root, x, y, expanded = new Set(), incoming = false) {
  const p = P.get(root),
    m = graphMembers(root, expanded, incoming),
    rest = m.ids.filter((id) => id !== root);
  const rank = {
    Perception: 0,
    Mapping: 1,
    Planning: 2,
    Control: 3,
    Learning: 4,
    Simulation: 5,
    Systems: 6,
  };
  rest.sort(
    (a, b) =>
      rank[P.get(a).layer] - rank[P.get(b).layer] ||
      P.get(a).name.localeCompare(P.get(b).name),
  );
  const cols = innerWidth <= 580 ? 2 : 4,
    w = innerWidth <= 580 ? 610 : 1150,
    rows = Math.ceil(rest.length / cols),
    h = Math.max(610, 375 + rows * 145 + 95);
  const keyPrefix = root + ":";
  const frame = { id: root, x, y, w, h, root, ids: m.ids };
  SC.frames.push(frame);
  SC.nodes.push({
    key: keyPrefix + root,
    id: root,
    cluster: root,
    x: x + (w - 360) / 2,
    y: y + 140,
    w: 360,
    h: 136,
    root: true,
  });
  rest.forEach((id, i) => {
    let col = i % cols,
      row = Math.floor(i / cols);
    const inRow = Math.min(cols, rest.length - row * cols),
      start = (w - (inRow * 242 + (inRow - 1) * 30)) / 2;
    SC.nodes.push({
      key: keyPrefix + id,
      id,
      cluster: root,
      x: x + start + col * 272,
      y: y + 370 + row * 145,
      w: 242,
      h: 104,
      root: false,
    });
  });
  for (const e of m.edges)
    SC.edges.push({
      edge: e,
      a: keyPrefix + e.a,
      b: keyPrefix + e.b,
      cluster: root,
    });
  return frame;
}
function buildScene() {
  SC = { nodes: [], edges: [], frames: [], zones: [], bounds: null };
  if (S.scene === "neighborhood") {
    addCluster(S.root, 0, 0, S.expanded, true);
  } else if (S.mode === "assembled") {
    let y = 0;
    for (const group of D.canvas.assemblyGroups) {
      const totalRows = Math.ceil(group.projects.length / 3),
        zone = {
          id: group.id,
          x: 0,
          y,
          w: 3770,
          h: 0,
          title: group.title,
          description: group.description,
        };
      SC.zones.push(zone);
      let rowY = y + 120;
      for (let r = 0; r < totalRows; r++) {
        let maxH = 0;
        group.projects.slice(r * 3, r * 3 + 3).forEach((id, c) => {
          const f = addCluster(
            id,
            c * 1290,
            rowY,
            new Set(S.assemblyExpansions[id] || []),
          );
          maxH = Math.max(maxH, f.h);
        });
        rowY += maxH + 125;
      }
      zone.h = rowY - y - 30;
      y = rowY + 100;
    }
  } else {
    let colHeights = [0, 0];
    D.canvas.problemGroups.forEach((g, i) => {
      const ps = D.projects.filter(
        (p) => p.views.includes("focused") && p.problemGroup === g.id,
      );
      if (!ps.length) return;
      const col = i % 2,
        x = col * 1490,
        y = colHeights[col],
        rows = Math.ceil(ps.length / 4);
      const z = {
        id: g.id,
        x,
        y,
        w: 1280,
        h: 150 + rows * 245 + 55,
        title: g.title,
        description: g.description,
      };
      SC.zones.push(z);
      ps.forEach((p, j) =>
        SC.nodes.push({
          key: "focused:" + p.id,
          id: p.id,
          cluster: null,
          x: x + (j % 4) * 326,
          y: y + 155 + Math.floor(j / 4) * 245,
          w: 295,
          h: 200,
          focused: true,
        }),
      );
      colHeights[col] += z.h + 150;
    });
  }
  SC.bounds = sceneBounds();
}
function nodeHTML(n) {
  const p = P.get(n.id),
    hybrid = p.views.length === 2;
  return `<button class="node ${n.root ? "root" : ""} ${n.focused ? "focused-node" : ""} ${S.selected === n.id ? "is-selected" : ""}" data-instance="${esc(n.key)}" data-project="${n.id}" data-context="${n.cluster || ""}" aria-label="${esc(p.name + ". " + p.problem)}" style="left:${n.x}px;top:${n.y}px;width:${n.w}px;height:${n.h}px;--node-color:${colors[p.layer]}"><span class="node-title">${esc(p.name)}</span><span class="node-meta">${esc(n.root ? p.role : n.focused ? p.problem : p.role)}</span>${n.root || n.focused ? `<span class="node-badge"><span class="dot"></span>${esc(n.root ? p.status : hybrid ? "Also an assembled system" : p.layer)}</span>` : ""}</button>`;
}
function frameHTML(f) {
  const p = P.get(f.root),
    empty = f.ids.length === 1;
  return `<section class="cluster ${S.context === f.id ? "in-context" : ""}" data-cluster="${f.id}" style="left:${f.x}px;top:${f.y}px;width:${f.w}px;height:${f.h}px" aria-label="${esc(p.name)} assembly"><div class="cluster-head"><p>${esc(p.problem)}</p><span class="label">${S.scene === "neighborhood" ? "Project neighborhood" : "Documented assembly"}</span></div><button class="overview-chip" data-focus="${p.id}"><strong>${esc(p.name)}</strong><p>${esc(p.problem)}</p><small>${f.ids.length - 1} connected projects · ${esc(p.status)}</small></button>${empty ? `<p class="cluster-empty">No separate integration dependencies have been established in this map.<br>Inspect the release and source boundaries.</p>` : ""}<div class="cluster-foot"><span>${f.ids.length} project${f.ids.length === 1 ? "" : "s"} in this view. ${S.scene === "neighborhood" ? "Includes relationships to and from this project." : "Internal packages remain within their parent."}</span><button data-focus="${p.id}">Explore this ${S.scene === "neighborhood" ? "neighborhood" : "assembly"}</button></div></section>`;
}
function port(a, b, offset = 0) {
  let ax = a.x + a.w / 2,
    ay = a.y + a.h / 2,
    bx = b.x + b.w / 2,
    by = b.y + b.h / 2,
    dx = bx - ax,
    dy = by - ay,
    t = Math.min(
      a.w / 2 / Math.abs(dx || 0.0001),
      a.h / 2 / Math.abs(dy || 0.0001),
    );
  return { x: ax + dx * t, y: ay + dy * t };
}
function edgeHTML(item, i, map) {
  const a = map.get(item.a),
    b = map.get(item.b);
  if (!a || !b) return "";
  const start = port(a, b),
    end = port(b, a),
    e = item.edge,
    t = TYPES[e.type],
    dx = end.x - start.x,
    dy = end.y - start.y;
  let path;
  if (Math.abs(dy) > 45) {
    const mid = (start.y + end.y) / 2;
    path = `M${start.x},${start.y} C${start.x},${mid} ${end.x},${mid} ${end.x},${end.y}`;
  } else {
    const bend = 70 + Math.abs(dx) * 0.12;
    path = `M${start.x},${start.y} C${start.x},${start.y - bend} ${end.x},${end.y - bend} ${end.x},${end.y}`;
  }
  return `<g class="edge" tabindex="0" role="button" data-edge="${e.id}" data-a="${e.a}" data-b="${e.b}" aria-label="${esc(P.get(e.a).name + " " + e.type.toLowerCase() + " " + P.get(e.b).name + ". Inspect evidence.")}" ><title>${esc(e.type + ": " + e.note)}</title><path class="hit" d="${path}"/><path class="line" stroke="${t.color}" stroke-dasharray="${t.dash}" marker-end="url(#arrow-${t.class})" d="${path}"/></g>`;
}
function draw() {
  buildScene();
  const frame = $("#frames"),
    nodes = $("#nodes"),
    wires = $("#wires");
  frame.innerHTML =
    SC.zones
      .map(
        (z) =>
          `<div class="zone-title" style="left:${z.x + 15}px;top:${z.y + 12}px;width:${z.w - 30}px"><h2>${esc(z.title)}</h2><p>${esc(z.description)}</p></div>`,
      )
      .join("") + SC.frames.map(frameHTML).join("");
  nodes.innerHTML = SC.nodes.map(nodeHTML).join("");
  const m = new Map(SC.nodes.map((n) => [n.key, n]));
  wires.setAttribute("width", Math.max(1200, SC.bounds.x + SC.bounds.w + 100));
  wires.setAttribute("height", Math.max(800, SC.bounds.y + SC.bounds.h + 100));
  wires.innerHTML =
    `<defs>${Object.values(TYPES)
      .map(
        (t) =>
          `<marker id="arrow-${t.class}" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" markerUnits="userSpaceOnUse"><path d="M1,1 L9,5 L1,9" fill="none" stroke="${t.color}" stroke-width="1.6"/></marker>`,
      )
      .join("")}</defs>` + SC.edges.map((e, i) => edgeHTML(e, i, m)).join("");
  $("#world").style.width = SC.bounds.w + "px";
  $("#world").style.height = SC.bounds.h + "px";
  renderChrome();
  renderInspector();
  transform();
  saveCompare();
}
function renderChrome() {
  document.title =
    (S.mode === "assembled" ? "Assembled systems" : "Focused components") +
    " | Drone autonomy atlas";
  $$("[data-mode]").forEach((b) =>
    b.setAttribute("aria-pressed", b.dataset.mode === S.mode),
  );
  $("#index").hidden = !S.index;
  $("#index-reopen").hidden = S.index;
  $("#back-button").disabled = !S.history.length;
  renderIndex();
  const context =
    S.scene === "neighborhood"
      ? P.get(S.root).name
      : S.mode === "assembled"
        ? "Explore assemblies"
        : "Explore contributions";
  $("#caption").innerHTML =
    `<strong>${esc(context)}</strong><span>${S.scene === "neighborhood" ? "Select a project, then unfold its connections." : S.mode === "assembled" ? "Each cluster starts with the problem the system tackles." : "Each point is independently identifiable work. Connections unfold on request."}</span>`;
  $("#caption").style.left =
    innerWidth <= 580
      ? "155px"
      : S.index
        ? innerWidth > 1650
          ? "284px"
          : innerWidth > 1150
            ? "263px"
            : "235px"
        : "190px";
  $("#breadcrumbs").innerHTML =
    `${S.scene === "neighborhood" ? '<button data-action="landscape">Back to landscape</button><span>/</span>' : ""}<span>${esc(S.scene === "neighborhood" ? P.get(S.root).name : S.mode === "assembled" ? P.get(S.context)?.name || "Assembled systems" : D.canvas.problemGroups.find((g) => g.id === S.group)?.title || "Focused components")}</span>`;
  renderLegend();
}
function renderIndex() {
  const groups =
    S.mode === "assembled" ? D.canvas.assemblyGroups : D.canvas.problemGroups;
  $("#index").innerHTML =
    `<header><button class="icon-button" data-action="index" aria-label="Close problem index">×</button><h1>${S.mode === "assembled" ? "What has been assembled?" : "What problems are being solved?"}</h1><p>${S.mode === "assembled" ? "Discover each system’s purpose and the projects incorporated into it." : "Discover a contribution, then reveal its dependencies and where it is used."}</p></header><div class="index-body">${groups
      .map((g) => {
        const ps =
          S.mode === "assembled"
            ? g.projects.map((id) => P.get(id))
            : D.projects.filter(
                (p) => p.views.includes("focused") && p.problemGroup === g.id,
              );
        if (!ps.length) return "";
        return `<details ${S.group === g.id ? "open" : ""} data-index-group="${g.id}"><summary>${esc(g.title)}</summary><p class="group-note">${esc(g.description)}</p><button class="jump-group" data-group="${g.id}">Locate this group</button>${ps.map((p) => `<button class="project-link ${S.selected === p.id || (S.context === p.id && S.mode === "assembled") ? "current" : ""}" data-${S.mode === "assembled" ? "focus" : "locate"}="${p.id}">${esc(p.name)}${S.mode === "assembled" ? `<small>${esc(p.role)}</small>` : ""}</button>`).join("")}</details>`;
      })
      .join(
        "",
      )}</div><footer>${S.mode === "assembled" ? D.canvas.assemblyGroups.reduce((n, g) => n + g.projects.length, 0) : D.projects.filter((p) => p.views.includes("focused")).length} ${S.mode === "assembled" ? "assemblies" : "focused projects"} in this mode.<br><button data-action="library">Browse every record, including references</button></footer>`;
}
function renderLegend() {
  $("#legend").innerHTML =
    `<button class="icon-button close-legend" data-action="legend" aria-label="Close connection types">×</button><h2>What does a line mean?</h2>${Object.entries(
      TYPES,
    )
      .map(
        ([name, t]) =>
          `<label><input type="checkbox" data-type="${name}" ${S.types.has(name) ? "checked" : ""}><span class="line-sample ${t.class}"></span>${name}</label><p>${esc(t.description)}</p>`,
      )
      .join(
        "",
      )}<p class="footnote">Arrows point from the project to the work it incorporates, draws from or evaluates. Every line has evidence. Position does not imply compatibility.</p>`;
}
function appearances(id) {
  return D.canvas.assemblyGroups
    .flatMap((g) => g.projects)
    .filter(
      (root) =>
        root !== id && graphMembers(root, new Set(), false).ids.includes(id),
    );
}
function renderInspector() {
  const panel = $("#inspector");
  if (!S.selected || !P.has(S.selected)) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const p = P.get(S.selected),
    rs = related(p.id),
    apps = appearances(p.id),
    within =
      S.context &&
      S.context !== p.id &&
      SC.frames.some((f) => f.id === S.context && f.ids.includes(p.id));
  const sourceHTML = sourceList(p.sources);
  panel.innerHTML = `<header class="inspect-header"><button class="icon-button close-inspector" data-action="close-inspector" aria-label="Close project details">×</button><div class="inspect-eyebrow"><span class="category-dot" style="background:${colors[p.layer]}"></span>${esc(p.role)}</div><h2>${esc(p.name)}</h2><p class="inspect-problem">${esc(p.problem)}</p><div class="inspect-actions">${link(p.repo, "Open project ↗", "btn primary")}${compareButton(p.id)}</div></header><div class="inspect-body"><div class="section"><h3>What this work contributes</h3><p>${esc(p.summary)}</p><div class="facts">${badge(p.status)}${badge(p.platform)}</div>${p.views.length === 2 ? '<p class="small muted">This project has both an assembled system and a focused contribution. Both appearances share this record.</p>' : ""}${p.referenceOnly ? '<p class="small muted">Retained as reference material. It is not presented as a released assembled solution.</p>' : ""}</div><div class="section"><h3>Explore its connections</h3><div class="inspect-actions" style="margin-top:8px">${!p.referenceOnly ? `<button class="btn" data-expand="${p.id}">${within ? "Unfold dependencies here" : S.scene === "neighborhood" ? "Unfold this project" : "Reveal project connections"}</button>` : ""}${p.views.includes("assembled") ? `<button class="btn" data-focus="${p.id}">Locate its assembly</button>` : ""}${p.views.includes("focused") && S.mode === "assembled" ? `<button class="btn" data-focused="${p.id}">Find its focused contribution</button>` : ""}</div>${apps.length ? `<p class="small muted" style="margin-top:12px">Also incorporated in these mapped assemblies:</p><div class="appearances">${apps.map((id) => `<button class="appearance" data-appearance="${id}" data-target="${p.id}">${esc(P.get(id).name)}</button>`).join("")}</div>` : `<p class="small muted" style="margin-top:12px">${rs.length ? rs.length + " documented relationships; some are research or evaluation links." : "No individual project relationships were established in this atlas. Inspect its sources for more detail."}</p>`}</div><div class="section"><h3>Assumptions and inputs</h3><p>${esc(p.needs)}</p></div><div class="section boundary"><h3>What the evidence does not establish</h3><p>${esc(p.limit)}</p></div>${p.assemblyNote ? `<div class="section"><h3>How to read this assembly</h3><p>${esc(p.assemblyNote)}</p></div>` : ""}<div class="section"><h3>Reuse and licensing</h3><p>${esc(p.license)}</p></div><details><summary>Documented relationships (${rs.length})</summary>${rs.length ? rs.map((e) => relationHTML(e, p.id)).join("") : '<p class="muted small">No individually mapped connections. This does not establish that the project has no dependencies.</p>'}</details>${p.internalNotes ? `<details><summary>Inside this repository</summary>${p.internalNotes.map((n) => `<p><strong>${esc(n.name)}</strong></p><p>${esc(n.description)}</p><p>${link(n.url, "Inspect internal package")}</p>`).join("")}</details>` : ""}<details id="project-sources"><summary>Sources and inspection depth</summary><p class="small muted" style="margin-bottom:9px">${esc(p.evidence)}.${p.commit ? " Inspected revision " + p.commit.slice(0, 8) + " (" + p.commitDate + ")." : ""}</p>${sourceHTML}${p.additionalLinks?.length ? `<h3 style="margin-top:18px">Additional links from the assembly guide</h3>${sourceList(p.additionalLinks.map((x) => ({ label: x.name, url: x.url })))}` : ""}</details>${
    D.claims.some((c) => c.projects.includes(p.id))
      ? `<details><summary>Related conversation checks</summary><div class="appearances">${D.claims
          .filter((c) => c.projects.includes(p.id))
          .map(
            (c) =>
              `<button class="appearance" data-claim="${c.id}">${c.id}: ${esc(c.verdict)}</button>`,
          )
          .join("")}</div></details>`
      : ""
  }</div><footer class="inspect-footer">One project record across every appearance. Source review; flight results were not independently reproduced.</footer>`;
}
function relationHTML(e, id) {
  const direction =
    e.a === id
      ? e.type
      : e.type === "Uses"
        ? "Used by"
        : e.type === "Optional"
          ? "Optional for"
          : e.type === "Code reuse"
            ? "Code reused by"
            : e.type === "Research lineage"
              ? "Research connection from"
              : "Evaluation connection from";
  return `<div class="relation-row"><div class="relation-type"><span class="line-sample ${TYPES[e.type].class}"></span> ${esc(direction)}</div><button class="text-project" data-project="${other(e, id)}">${esc(P.get(other(e, id)).name)}</button><p>${esc(e.note)}</p>${link(e.source, "Inspect relationship evidence")}</div>`;
}
function switchMode(mode, id = null) {
  if (mode === S.mode && !id) {
    return;
  }
  remember();
  S.memories[S.mode] = snapshot();
  S.mode = mode;
  const memory = S.memories[mode];
  S.scene = "landscape";
  S.root = null;
  S.selected = null;
  S.group = mode === "assembled" ? "navigation" : "position";
  S.context = mode === "assembled" ? "cerlab" : null;
  S.expanded = new Set();
  if (memory) {
    S.group = memory.group;
    S.context = memory.context;
    S.cam = { ...memory.cam };
  }
  if (innerWidth <= 580) S.index = false;
  draw();
  if (id) {
    locate(id, { record: false });
    return;
  }
  if (!memory) {
    if (mode === "focused") introduceGroup();
    else fitCurrent();
  }
  syncHash();
}
function selectProject(
  id,
  context = null,
  { record = true, reveal = false } = {},
) {
  if (!P.has(id)) return;
  if (record) remember();
  S.selected = id;
  if (context) {
    S.context = context;
    S.group = P.get(context)?.assemblyGroup || S.group;
  } else if (S.mode === "focused") {
    S.group = P.get(id).problemGroup;
  }
  if (innerWidth <= 850) S.index = false;
  renderChrome();
  renderInspector();
  $$(".node").forEach((el) =>
    el.classList.toggle("is-selected", el.dataset.project === id),
  );
  if (reveal) {
    const n = SC.nodes.find(
      (n) => n.id === id && (!context || n.cluster === context),
    );
    centerNode(n);
  }
  syncHash();
}
function locate(id, { record = true } = {}) {
  const p = P.get(id);
  if (!p) return;
  if (record) remember();
  if (p.referenceOnly) {
    selectProject(id, null, { record: false });
    return;
  }
  if (p.views.includes("assembled") && S.mode === "assembled") {
    focusAssembly(id, { record: false });
    return;
  }
  if (!p.views.includes("focused")) {
    focusAssembly(id, { record: false });
    return;
  }
  S.mode = "focused";
  S.scene = "landscape";
  S.root = null;
  S.selected = id;
  S.context = null;
  S.group = p.problemGroup;
  if (innerWidth <= 850) S.index = false;
  draw();
  S.cam.k = Math.max(0.76, Math.min(1.15, S.cam.k));
  centerNode(SC.nodes.find((n) => n.id === id));
  syncHash();
}
function focusAssembly(id, { record = true, target = null } = {}) {
  if (!P.get(id)?.views.includes("assembled")) {
    revealConnections(id);
    return;
  }
  if (record) remember();
  S.mode = "assembled";
  S.scene = "landscape";
  S.context = id;
  S.group = P.get(id).assemblyGroup;
  S.root = null;
  S.selected = target || id;
  if (innerWidth <= 850) S.index = false;
  draw();
  fitCurrent();
  syncHash();
}
function revealConnections(id) {
  remember();
  if (
    S.mode === "assembled" &&
    S.scene === "landscape" &&
    SC.frames.some((f) => f.id === S.context && f.ids.includes(id))
  ) {
    const current = new Set(S.assemblyExpansions[S.context] || []);
    if (current.has(id)) {
      current.delete(id);
      toast("Collapsed the extra dependency level.");
    } else {
      current.add(id);
      toast("Expanded documented project dependencies.");
    }
    S.assemblyExpansions[S.context] = [...current];
    S.selected = id;
    draw();
    fitCurrent();
    return;
  }
  if (S.scene === "neighborhood") {
    S.expanded.add(id);
    S.selected = id;
    draw();
    fitCurrent();
    return;
  }
  S.mode = "focused";
  S.scene = "neighborhood";
  S.root = id;
  S.context = id;
  S.selected = id;
  S.expanded = new Set();
  if (innerWidth <= 850) S.index = false;
  draw();
  fitCurrent();
  syncHash();
}
function backLandscape() {
  remember();
  S.scene = "landscape";
  S.root = null;
  S.expanded = new Set();
  S.context = S.mode === "assembled" ? "cerlab" : null;
  S.group =
    S.mode === "assembled"
      ? "navigation"
      : P.get(S.selected)?.problemGroup || "position";
  S.selected = null;
  draw();
  fitCurrent();
  syncHash();
}
function focusGroup(id) {
  remember();
  S.scene = "landscape";
  S.root = null;
  S.selected = null;
  S.group = id;
  if (S.mode === "assembled")
    S.context = D.canvas.assemblyGroups.find((g) => g.id === id)?.projects[0];
  if (innerWidth <= 580) S.index = false;
  draw();
  const z = SC.zones.find((z) => z.id === id);
  if (z) {
    if (S.mode === "focused") introduceGroup();
    else fitRect(z);
  }
  syncHash();
}
function syncHash() {
  const parts = [S.mode];
  if (S.scene === "neighborhood") parts.push("connections", S.root);
  else if (S.selected) parts.push(S.selected);
  else if (S.mode === "assembled" && S.context) parts.push(S.context);
  history.replaceState(null, "", "#" + parts.join("/"));
}
function fromHash() {
  const [mode, part, id] = location.hash.slice(1).split("/");
  if (["assembled", "focused"].includes(mode)) S.mode = mode;
  else if (mode === "map" && P.has(part)) {
    S.mode = P.get(part).views.includes("assembled") ? "assembled" : "focused";
    S.selected = part;
    S.context = part;
  } else if (mode === "audit") {
    openAudit();
    return;
  }
  if (part === "connections" && P.has(id)) {
    S.scene = "neighborhood";
    S.root = id;
    S.selected = id;
    S.context = id;
  } else if (P.has(part)) {
    S.selected = part;
    S.group = P.get(part).problemGroup;
    if (S.mode === "assembled" && P.get(part).views.includes("assembled")) {
      S.context = part;
      S.group = P.get(part).assemblyGroup;
    } else if (
      !P.get(part).views.includes("focused") &&
      !P.get(part).referenceOnly
    ) {
      S.mode = "assembled";
      S.context = part;
      S.group = P.get(part).assemblyGroup;
    }
  } else {
    S.context = S.mode === "assembled" ? "cerlab" : null;
    S.group = S.mode === "assembled" ? "navigation" : "position";
  }
  draw();
  if (
    S.mode === "focused" &&
    S.selected &&
    S.scene === "landscape" &&
    P.get(S.selected).views.includes("focused")
  ) {
    S.cam.k = 0.92;
    centerNode(SC.nodes.find((n) => n.id === S.selected));
  } else if (S.mode === "focused" && S.scene === "landscape") introduceGroup();
  else fitCurrent();
}
function openSheet(kind, title, subtitle = "", wide = false) {
  S.sheet = kind;
  $("#sheet").classList.toggle("wide", wide);
  $("#sheet-title").textContent = title;
  $("#sheet-subtitle").textContent = subtitle;
  $("#sheet-subtitle").hidden = !subtitle;
  $("#references").hidden = true;
  if (!$("#sheet").open) $("#sheet").showModal();
  $("#sheet-content").scrollTop = 0;
}
function closeSheet() {
  if ($("#sheet").open) $("#sheet").close();
  S.sheet = null;
}
function showEdge(id) {
  const e = E.get(id);
  if (!e) return;
  openSheet(
    "edge",
    "What this connection establishes",
    P.get(e.a).name + " · " + e.type + " · " + P.get(e.b).name,
  );
  $("#sheet-content").innerHTML =
    `<div class="explanation"><p><span class="line-sample ${TYPES[e.type].class}"></span> <strong>${esc(e.type)}</strong></p><p>${esc(e.note)}</p><p class="muted">${esc(TYPES[e.type].description)}</p>${link(e.source, "Open supporting source ↗", "btn primary")}<h3>Explore either project</h3><div class="appearances">${projectButton(e.a)}${projectButton(e.b)}</div></div>`;
}
function openHelp() {
  openSheet(
    "help",
    "Explore projects, discover the problems.",
    "The canvas has two starting points. The same project can appear in more than one context.",
  );
  $("#sheet-content").innerHTML =
    `<div class="explanation"><h3>Assembled systems</h3><p>Each cluster introduces what a system tackles and the independently published projects incorporated into it. Select a project to read its assumptions, contribution and sources. “Unfold dependencies here” adds the next documented level.</p><h3>Focused components</h3><p>Start from a problem area or an individual contribution. Reveal its connections to see dependencies and other projects that use it. A focused contribution can itself combine independent projects.</p><h3>Move through the landscape</h3><p>Drag empty space or a node to pan. Scroll to zoom around the pointer; pinch works on touchscreens. Use Fit for the current area, Overview for the landscape, and ↶ to return to an earlier exploration.</p><p>Keyboard: Tab reaches projects and controls; Enter opens them. Focus the canvas and use arrow keys to pan, + / − to zoom, F to fit and Home for the overview. Press / anywhere outside a text field to search every record.</p><h3>Keep the distinctions visible</h3><p>Solid lines show integration. Optional modules, copied code, research ancestry and evaluation each have a separate meaning. Open Connection types to reveal additional relationships. Select any line to inspect its source.</p><p>Comparison opens a temporary sheet for selected projects. The conversation audit and research methodology live in the ••• menu. This file works offline; external evidence needs a connection.</p></div>`;
}
function openMethod() {
  openSheet(
    "method",
    "Evidence and scope",
    "Original research snapshot: 8 September 2026. Project-level classification and assembly-source review: 9 September 2026.",
  );
  const m = D.method;
  $("#sheet-content").innerHTML =
    `<div class="method-grid"><section class="method-section"><h3>What a node represents</h3><p>${esc(D.canvas.identityRule)}</p><p>A separate repository can be a component, a bundle, or an assembled system. Packaging alone does not determine its scope. Some research projects have both a focused contribution and an integrated demonstration.</p><p>Internal packages are explained inside the parent’s details. A copied package produces a code-reuse connection to the upstream project, with the reused portion named explicitly.</p><h3 style="margin-top:24px">What a graph establishes</h3><p>${esc(D.canvas.connectionRule)}</p><p>The diagram introduces what authors assembled; it does not score suitability against an assumed user specification, guarantee interoperability, or enumerate every transitive build dependency.</p></section><section class="method-section"><h3>Search and inspection</h3><p>The initial drone-focused research used ${m.exaSearches} saved Exa search requests (${m.deepSearches} deep), yielding ${m.resultRows} result rows and ${m.uniqueDiscoveryURLs} unique discovery URLs. Built-in web searches added manufacturer documentation, project sources and papers.</p><p>${m.clonedRepos} repositories were cloned for source/tree inspection; ${m.readmes} READMEs were saved in the initial inventory. This rewrite additionally inspected parent manifests and separate component repositories to clarify project identities and assembly connections.</p><p>${D.projects.length} project records and ${D.edges.length} typed relationships are preserved. Reference-only entries remain searchable, with their release and scope boundaries visible.</p><details><summary>Original discovery queries</summary><ol class="query-list">${m.queries.map((q) => `<li>${esc(q.query)} <span class="muted">(${esc(q.type)})</span></li>`).join("")}</ol></details></section><section class="method-section"><h3>Evidence boundaries</h3><p>No drone project was built and no model, simulation flight or real flight was independently reproduced. A source tree establishes what is released; a paper or demo establishes reported behavior under its test conditions.</p><p>Inspection depth varies and is stated in each project. A parent repository table can establish a separately published dependency without establishing the internals of that dependency. Current default branches may differ from the branches an assembly pins.</p><p>License statements refer to inspected root terms where available; dependencies, models and assets can have different terms. A public repository is not automatically unrestricted reusable software.</p></section><section class="method-section"><h3>Read the relationship, not just the line</h3><dl>${Object.entries(
      TYPES,
    )
      .map(
        ([k, t]) =>
          `<dt><span class="line-sample ${t.class}"></span> ${esc(k)}</dt><dd>${esc(t.description)}</dd>`,
      )
      .join(
        "",
      )}</dl><p style="margin-top:20px">All data, fonts and interface code are embedded. No network requests are required to use the atlas. Source links open separately.</p><div class="inspect-actions"><button class="btn" data-action="download">Download dataset</button>${link(D.conversation, "Original conversation", "btn")}</div></section></div>`;
}
function verdict(v) {
  const c =
    v === "Supported"
      ? "good"
      : /Contradicted|Outdated|Misleading/.test(v)
        ? "bad"
        : /Supported with limits|Overstated|Incomplete/.test(v)
          ? "warn"
          : "";
  return `<span class="verdict ${c}">${esc(v)}</span>`;
}
function openAudit(id = null) {
  if (id) {
    S.auditTopic = "All topics";
    S.auditVerdict = "All verdicts";
  }
  openSheet(
    "audit",
    "Conversation audit",
    "Drone-related assertions from the shared conversation. Repeated claims are consolidated; wording below is a paraphrase.",
  );
  renderAudit();
  if (id) {
    const e = $("#claim-" + id);
    if (e) {
      e.open = true;
      e.scrollIntoView({ block: "center" });
    }
  }
}
function renderAudit() {
  const topics = ["All topics", ...new Set(D.claims.map((c) => c.topic))],
    verdicts = ["All verdicts", ...new Set(D.claims.map((c) => c.verdict))],
    cs = D.claims.filter(
      (c) =>
        (S.auditTopic === "All topics" || c.topic === S.auditTopic) &&
        (S.auditVerdict === "All verdicts" || c.verdict === S.auditVerdict),
    );
  $("#sheet-content").innerHTML =
    `<div class="sheet-toolbar"><label>Topic<select id="audit-topic">${topics.map((t) => `<option ${S.auditTopic === t ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label><label>Verdict<select id="audit-verdict">${verdicts.map((t) => `<option ${S.auditVerdict === t ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label><button class="btn" data-action="audit-expand">Expand visible checks</button><span class="small muted">${cs.length} checks</span></div>${cs.length ? cs.map((c) => `<details class="claim" id="claim-${c.id}"><summary><span class="claim-id">${c.id}</span><h3>${esc(c.claim)}</h3>${verdict(c.verdict)}</summary><div class="claim-body"><p>${esc(c.finding)}</p>${c.why ? `<p class="small muted">${esc(c.why)}</p>` : ""}<div class="claim-links">${c.sources.map((s) => link(s.url, s.label)).join("")}</div><div class="claim-projects">${c.projects.map((id) => projectButton(id)).join("")}</div></div></details>`).join("") : '<div class="empty"><h3>No checks match both filters.</h3><p>Choose all topics or all verdicts to broaden the view.</p></div>'}`;
}
function openComparison() {
  openSheet(
    "compare",
    "Compare what the work tackles",
    "An optional side-by-side reading of project scope, assumptions and evidence. No suitability scores.",
    true,
  );
  renderComparison();
}
function renderComparison() {
  const ps = S.compare.map((id) => P.get(id)),
    rows = [
      ["Problem tackled", (p) => esc(p.problem)],
      ["Contribution", (p) => esc(p.summary)],
      ["Context / inputs", (p) => esc(p.needs)],
      ["Evidence boundary", (p) => esc(p.limit)],
      ["Release status", (p) => badge(p.status)],
      ["Platform", (p) => esc(p.platform)],
      ["Reuse / license", (p) => esc(p.license)],
      ["Inspection", (p) => esc(p.evidence)],
      ["Sources", (p) => sourceList(p.sources)],
    ];
  $("#sheet-content").innerHTML =
    `<div class="sheet-toolbar"><select id="compare-add" class="compare-select" aria-label="Add a project to comparison"><option value="">Add a project…</option>${D.projects
      .filter((p) => !S.compare.includes(p.id))
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((p) => `<option value="${p.id}">${esc(p.name)}</option>`)
      .join(
        "",
      )}</select><button class="btn" data-action="compare-clear" ${ps.length ? "" : "disabled"}>Clear</button><button class="btn" data-action="compare-download" ${ps.length ? "" : "disabled"}>Download comparison</button><span class="small muted">${ps.length} of 4 projects</span></div>${ps.length ? `<div class="compare-scroll"><table class="compare-table"><thead><tr><th scope="col">Read side by side</th>${ps.map((p) => `<th scope="col"><button class="remove" data-compare="${p.id}" aria-label="Remove ${esc(p.name)}">×</button><span class="small muted">${esc(p.role)}</span><h3>${esc(p.name)}</h3><button class="btn small" data-project="${p.id}">Explore project</button></th>`).join("")}</tr></thead><tbody>${rows.map(([title, fn]) => `<tr><th scope="row">${title}</th>${ps.map((p) => `<td>${fn(p)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : '<div class="empty"><h3>Bring a few projects together.</h3><p>Add a project here or use + Compare in any project’s details. The comparison remains available while you explore.</p></div>'}`;
}
function openLibrary() {
  openSheet(
    "library",
    "All project records",
    "Searchable reference access, including restricted releases, hardware references, adjacent-domain work and descriptions without implementation.",
  );
  renderLibrary();
}
function libraryProjects() {
  const q = S.libraryQuery.toLowerCase().trim();
  return D.projects
    .filter(
      (p) =>
        (!q ||
          [p.name, p.problem, p.summary, p.platform, p.license]
            .join(" ")
            .toLowerCase()
            .includes(q)) &&
        (S.libraryScope === "all" ||
          (S.libraryScope === "reference" && p.referenceOnly) ||
          p.views.includes(S.libraryScope)),
    )
    .sort((a, b) => a.name.localeCompare(b.name));
}
function renderLibrary() {
  const ps = libraryProjects(),
    size = 18,
    pages = Math.max(1, Math.ceil(ps.length / size));
  S.libraryPage = Math.min(S.libraryPage, pages - 1);
  $("#sheet-content").innerHTML =
    `<div class="sheet-toolbar"><input id="library-query" type="search" aria-label="Filter all project records" placeholder="Name, problem, platform…" value="${esc(S.libraryQuery)}"><select id="library-scope" aria-label="Project scope">${[
      ["all", "Every record"],
      ["assembled", "Assembled systems"],
      ["focused", "Focused components"],
      ["reference", "Reference-only records"],
    ]
      .map(
        ([v, l]) =>
          `<option value="${v}" ${S.libraryScope === v ? "selected" : ""}>${l}</option>`,
      )
      .join(
        "",
      )}</select><span class="small muted">${ps.length} records</span></div>${
      ps.length
        ? ps
            .slice(S.libraryPage * size, (S.libraryPage + 1) * size)
            .map(
              (p) =>
                `<article class="library-row"><div><button class="text-project" data-project="${p.id}">${esc(p.name)}</button><small>${esc(p.role)}</small></div><div><p>${esc(p.problem)}</p><small>${esc(p.platform)} · ${esc(p.status)}</small></div><button class="btn small" data-compare="${p.id}">${S.compare.includes(p.id) ? "✓ In comparison" : "+ Compare"}</button></article>`,
            )
            .join("")
        : '<div class="empty"><h3>No records match.</h3><p>Try a broader problem or choose Every record.</p></div>'
    }<div class="pagination"><span>${ps.length ? S.libraryPage * size + 1 : 0}–${Math.min(ps.length, (S.libraryPage + 1) * size)} of ${ps.length}</span><div><button class="btn small" data-library-page="-1" ${S.libraryPage === 0 ? "disabled" : ""}>Previous</button><button class="btn small" data-library-page="1" ${S.libraryPage === pages - 1 ? "disabled" : ""}>Next</button></div></div>`;
}
function openFinder() {
  S.search = "";
  S.searchIndex = 0;
  $("#finder-input").value = "";
  $("#references").hidden = true;
  renderFinder();
  $("#finder").showModal();
  $("#finder-input").focus();
}
function searchProjects(q) {
  q = q.toLowerCase().trim();
  if (!q)
    return [
      "cerlab",
      "fastdrone",
      "mrs",
      "ntnu",
      "super",
      "ego",
      "vins",
      "flightbench",
    ].map((id) => P.get(id));
  return D.projects
    .filter((p) =>
      [p.name, p.role, p.problem, p.summary, p.needs, p.platform, p.repo]
        .join(" ")
        .toLowerCase()
        .includes(q),
    )
    .sort(
      (a, b) =>
        (b.name.toLowerCase().includes(q) ? 1 : 0) -
          (a.name.toLowerCase().includes(q) ? 1 : 0) ||
        a.name.localeCompare(b.name),
    )
    .slice(0, 30);
}
function renderFinder() {
  const ps = searchProjects(S.search);
  S.searchIndex = Math.max(0, Math.min(S.searchIndex, ps.length - 1));
  $("#finder-results").innerHTML = ps.length
    ? ps
        .map(
          (p, i) =>
            `<button class="result ${i === S.searchIndex ? "active" : ""}" data-result="${p.id}" aria-current="${i === S.searchIndex}"><strong>${esc(p.name)}</strong><p>${esc(p.problem)}</p><small>${p.referenceOnly ? "Reference record" : p.views.length === 2 ? "Assembled system and focused contribution" : p.views.includes("assembled") ? "Assembled system" : "Focused component"} · ${esc(p.status)}</small></button>`,
        )
        .join("")
    : '<div class="empty"><h3>No project found.</h3><p>Try a sensor such as “LiDAR”, a problem such as “exploration”, or a project name.</p></div>';
}
function download(data, name) {
  const a = document.createElement("a"),
    url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    );
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  toast("Download prepared: " + name);
}
function toggleIndex() {
  S.index = !S.index;
  renderChrome();
  if (innerWidth <= 580 && S.index) {
    S.selected = null;
    renderInspector();
  }
}
function closeInspector() {
  S.selected = null;
  renderChrome();
  renderInspector();
  $$(".node").forEach((n) => n.classList.remove("is-selected"));
  syncHash();
}
function handleAction(target) {
  const el = target.closest("button,[data-edge]");
  if (!el) return;
  if (el.dataset.result) {
    $("#finder").close();
    locate(el.dataset.result);
    return;
  }
  if (el.dataset.edge) {
    showEdge(el.dataset.edge);
    return;
  }
  if (el.dataset.mode) {
    switchMode(el.dataset.mode);
    return;
  }
  if (el.dataset.project) {
    if ($("#sheet").open) closeSheet();
    if (el.dataset.instance)
      selectProject(el.dataset.project, el.dataset.context || null);
    else locate(el.dataset.project);
    return;
  }
  if (el.dataset.focus) {
    focusAssembly(el.dataset.focus);
    return;
  }
  if (el.dataset.locate) {
    locate(el.dataset.locate);
    return;
  }
  if (el.dataset.focused) {
    switchMode("focused", el.dataset.focused);
    return;
  }
  if (el.dataset.expand) {
    revealConnections(el.dataset.expand);
    return;
  }
  if (el.dataset.appearance) {
    focusAssembly(el.dataset.appearance, { target: el.dataset.target });
    return;
  }
  if (el.dataset.compare) {
    toggleCompare(el.dataset.compare);
    if (S.sheet === "library") renderLibrary();
    return;
  }
  if (el.dataset.group) {
    focusGroup(el.dataset.group);
    return;
  }
  if (el.dataset.claim) {
    openAudit(el.dataset.claim);
    return;
  }
  if (el.dataset.libraryPage) {
    S.libraryPage += Number(el.dataset.libraryPage);
    renderLibrary();
    $("#sheet-content").scrollTop = 0;
    return;
  }
  switch (el.dataset.action) {
    case "search":
      openFinder();
      break;
    case "references":
      $("#references").hidden = !$("#references").hidden;
      el.setAttribute("aria-expanded", !$("#references").hidden);
      break;
    case "index":
      toggleIndex();
      break;
    case "close-inspector":
      closeInspector();
      break;
    case "legend":
      $("#legend").hidden = !$("#legend").hidden;
      $(".legend-trigger").setAttribute("aria-expanded", !$("#legend").hidden);
      break;
    case "back":
      goBack();
      break;
    case "zoom-in":
      zoomAt(1.22);
      break;
    case "zoom-out":
      zoomAt(1 / 1.22);
      break;
    case "zoom-reset":
      zoomAt(1 / S.cam.k);
      break;
    case "fit":
      fitCurrent();
      break;
    case "overview":
      remember();
      fitRect(SC.bounds, { all: true });
      break;
    case "landscape":
      backLandscape();
      break;
    case "compare":
      openComparison();
      break;
    case "compare-clear":
      S.compare = [];
      saveCompare();
      renderComparison();
      break;
    case "compare-download":
      download(
        {
          snapshot: D.date,
          projects: S.compare.map((id) => P.get(id)),
          relationships: D.edges.filter(
            (e) => S.compare.includes(e.a) || S.compare.includes(e.b),
          ),
        },
        "drone-project-comparison.json",
      );
      break;
    case "audit":
      openAudit();
      break;
    case "audit-expand": {
      const cs = $$(".claim"),
        value = cs.some((c) => !c.open);
      cs.forEach((c) => (c.open = value));
      el.textContent = value
        ? "Collapse visible checks"
        : "Expand visible checks";
      break;
    }
    case "method":
      openMethod();
      break;
    case "help":
      openHelp();
      break;
    case "library":
      openLibrary();
      break;
    case "download":
      download(D, "drone-autonomy-dataset.json");
      break;
  }
}
// The canvas transform is independent of selection and does not depend on force physics.
const pointers = new Map();
let gesture = null;
$("#canvas").addEventListener("pointerdown", (e) => {
  if (e.button !== 0) return;
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  const r = $("#workspace").getBoundingClientRect();
  if (pointers.size === 2) {
    const [a, b] = [...pointers.values()];
    gesture = {
      kind: "pinch",
      distance: Math.hypot(a.x - b.x, a.y - b.y),
      mid: { x: (a.x + b.x) / 2 - r.left, y: (a.y + b.y) / 2 - r.top },
      cam: { ...S.cam },
    };
    suppressClick = true;
    $("#canvas").setPointerCapture(e.pointerId);
  } else {
    gesture = {
      kind: "pan",
      x: e.clientX,
      y: e.clientY,
      cam: { ...S.cam },
      moved: false,
      pointer: e.pointerId,
    };
  }
});
$("#canvas").addEventListener("pointermove", (e) => {
  if (!pointers.has(e.pointerId) || !gesture) return;
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (gesture.kind === "pinch" && pointers.size >= 2) {
    const [a, b] = [...pointers.values()],
      r = $("#workspace").getBoundingClientRect(),
      mid = { x: (a.x + b.x) / 2 - r.left, y: (a.y + b.y) / 2 - r.top },
      dist = Math.hypot(a.x - b.x, a.y - b.y),
      k = Math.max(
        0.12,
        Math.min(2.3, (gesture.cam.k * dist) / Math.max(1, gesture.distance)),
      );
    const wx = (gesture.mid.x - gesture.cam.x) / gesture.cam.k,
      wy = (gesture.mid.y - gesture.cam.y) / gesture.cam.k;
    S.cam = { x: mid.x - wx * k, y: mid.y - wy * k, k };
    transform();
    e.preventDefault();
    return;
  }
  if (gesture.kind === "pan") {
    const dx = e.clientX - gesture.x,
      dy = e.clientY - gesture.y;
    if (Math.hypot(dx, dy) > 5) {
      gesture.moved = true;
      suppressClick = true;
      $("#canvas").setPointerCapture(e.pointerId);
      $("#canvas").classList.add("dragging");
      $("#pan-hint").hidden = true;
    }
    if (gesture.moved) {
      S.cam.x = gesture.cam.x + dx;
      S.cam.y = gesture.cam.y + dy;
      transform();
      e.preventDefault();
    }
  }
});
function releasePointer(e) {
  pointers.delete(e.pointerId);
  if ($("#canvas").hasPointerCapture(e.pointerId))
    $("#canvas").releasePointerCapture(e.pointerId);
  if (pointers.size === 1) {
    const [id, p] = [...pointers.entries()][0];
    gesture = {
      kind: "pan",
      x: p.x,
      y: p.y,
      cam: { ...S.cam },
      moved: true,
      pointer: id,
    };
  } else if (!pointers.size) {
    gesture = null;
    $("#canvas").classList.remove("dragging");
    setTimeout(() => (suppressClick = false), 60);
  }
}
$("#canvas").addEventListener("pointerup", releasePointer);
$("#canvas").addEventListener("pointercancel", releasePointer);
$("#canvas").addEventListener(
  "wheel",
  (e) => {
    e.preventDefault();
    const r = $("#workspace").getBoundingClientRect();
    if (e.shiftKey && !e.ctrlKey) {
      S.cam.x -= e.deltaY;
      S.cam.y -= e.deltaX;
      transform();
    } else {
      const delta = e.deltaMode === 1 ? e.deltaY * 18 : e.deltaY;
      zoomAt(
        Math.exp(-Math.max(-140, Math.min(140, delta)) * 0.003),
        e.clientX - r.left,
        e.clientY - r.top,
      );
    }
    $("#pan-hint").hidden = true;
  },
  { passive: false },
);
let hoverId = null;
$("#canvas").addEventListener("pointerover", (e) => {
  if (gesture) return;
  const n = e.target.closest("[data-instance]");
  if (!n || hoverId === n.dataset.project) return;
  hoverId = n.dataset.project;
  $$(".edge").forEach((edge) =>
    edge.classList.toggle(
      "highlight",
      edge.dataset.a === hoverId || edge.dataset.b === hoverId,
    ),
  );
});
$("#canvas").addEventListener("pointerleave", () => {
  hoverId = null;
  $$(".edge.highlight").forEach((e) => e.classList.remove("highlight"));
});
document.addEventListener("click", (e) => {
  if (suppressClick && e.target.closest("#canvas")) {
    e.preventDefault();
    return;
  }
  handleAction(e.target);
  if (
    !e.target.closest("#references") &&
    !e.target.closest("[data-action=references]")
  )
    $("#references").hidden = true;
});
document.addEventListener("dblclick", (e) => {
  const n = e.target.closest("[data-instance]");
  if (!n) return;
  if (n.dataset.context) focusAssembly(n.dataset.context);
  else revealConnections(n.dataset.project);
});
document.addEventListener("change", (e) => {
  const t = e.target;
  if (t.dataset.type) {
    t.checked ? S.types.add(t.dataset.type) : S.types.delete(t.dataset.type);
    draw();
    fitCurrent();
  } else if (t.id === "audit-topic") {
    S.auditTopic = t.value;
    renderAudit();
  } else if (t.id === "audit-verdict") {
    S.auditVerdict = t.value;
    renderAudit();
  } else if (t.id === "compare-add" && t.value) toggleCompare(t.value);
  else if (t.id === "library-scope") {
    S.libraryScope = t.value;
    S.libraryPage = 0;
    renderLibrary();
  }
});
let libraryTimer;
document.addEventListener("input", (e) => {
  if (e.target.id === "finder-input") {
    S.search = e.target.value;
    S.searchIndex = 0;
    renderFinder();
  } else if (e.target.id === "library-query") {
    S.libraryQuery = e.target.value;
    S.libraryPage = 0;
    clearTimeout(libraryTimer);
    libraryTimer = setTimeout(() => {
      renderLibrary();
      $("#library-query")?.focus();
    }, 160);
  }
});
document.addEventListener("keydown", (e) => {
  if ($("#finder").open) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      S.searchIndex += e.key === "ArrowDown" ? 1 : -1;
      renderFinder();
      $("#finder-results .active")?.scrollIntoView({ block: "nearest" });
    }
    if (e.key === "Enter") {
      e.preventDefault();
      $("#finder-results .active")?.click();
    }
    return;
  }
  if (e.target.closest("input,select,textarea")) return;
  if (e.key === "/" && !$("#sheet").open) {
    e.preventDefault();
    openFinder();
    return;
  }
  if ((e.key === "Enter" || e.key === " ") && e.target.matches("g.edge")) {
    e.preventDefault();
    showEdge(e.target.dataset.edge);
    return;
  }
  if (e.key === "Escape") {
    $("#references").hidden = true;
    $("#legend").hidden = true;
    if (!$("#sheet").open && S.selected) closeInspector();
    return;
  }
  if (e.target === $("#canvas") && !$("#sheet").open) {
    const moves = {
      ArrowLeft: [90, 0],
      ArrowRight: [-90, 0],
      ArrowUp: [0, 90],
      ArrowDown: [0, -90],
    };
    if (moves[e.key]) {
      e.preventDefault();
      S.cam.x += moves[e.key][0];
      S.cam.y += moves[e.key][1];
      transform();
    } else if (["+", "=", "-"].includes(e.key)) {
      e.preventDefault();
      zoomAt(e.key === "-" ? 1 / 1.2 : 1.2);
    } else if (e.key.toLowerCase() === "f") {
      e.preventDefault();
      fitCurrent();
    } else if (e.key === "Home") {
      e.preventDefault();
      fitRect(SC.bounds, { all: true });
    }
  }
});
$(".close-dialog").addEventListener("click", closeSheet);
$(".close-finder").addEventListener("click", () => $("#finder").close());
$("#sheet").addEventListener("close", () => (S.sheet = null));
for (const dialog of [$("#finder"), $("#sheet")])
  dialog.addEventListener("click", (e) => {
    if (e.target !== dialog) return;
    const r = dialog.getBoundingClientRect();
    if (
      e.clientX < r.left ||
      e.clientX > r.right ||
      e.clientY < r.top ||
      e.clientY > r.bottom
    )
      dialog.close();
  });
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (innerWidth <= 580) S.index = false;
    draw();
    fitCurrent();
  }, 120);
});
window.addEventListener("hashchange", fromHash);
fromHash();
saveCompare();
