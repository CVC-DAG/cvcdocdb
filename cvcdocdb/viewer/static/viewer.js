/* cvcdocdb graph viewer — read-only front end (no build step, no network
 * requests other than this server's own /api). All data is inserted with
 * textContent, never as HTML. */
(function () {
  "use strict";

  var MAX_RENDERED_NODES = 300;
  var NEIGHBOUR_LIMIT = 50;
  var PALETTE = [
    "#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948",
    "#b07aa1", "#ff9da7", "#9c755f", "#86bcb6", "#d37295", "#8cd17d"
  ];

  var token = new URLSearchParams(window.location.search).get("token");
  // Light layout: graph first (see viewer.css and cvcdocdb.viewer.page).
  var LIGHT = document.documentElement.getAttribute("data-layout") === "light";

  var state = {
    labels: [],           // node types selected in the sidebar (none = all)
    filters: [],          // [{prop, op, value}]
    text: "",
    offset: 0,
    limit: 50,
    total: 0,
    exact: true,
    items: [],
    selectedId: null,
    hiddenTypes: {},      // relation type -> true when unticked
    labelColors: {},
    knownProps: {}
  };

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------

  function $(id) { return document.getElementById(id); }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        if (key === "text") node.textContent = attrs[key];
        else if (key === "className") node.className = attrs[key];
        else if (key.slice(0, 2) === "on") node.addEventListener(key.slice(2), attrs[key]);
        else node.setAttribute(key, attrs[key]);
      });
    }
    (children || []).forEach(function (child) {
      if (child == null) return;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return node;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  // Colours are handed out in order, so every label gets its own: the
  // palette first, then golden-angle hues once it runs out.
  function nthColor(n) {
    if (n < PALETTE.length) return PALETTE[n];
    return "hsl(" + Math.round((n * 137.508) % 360) + ", 55%, 55%)";
  }

  function colorFor(label) {
    if (!state.labelColors[label]) {
      state.labelColors[label] = nthColor(Object.keys(state.labelColors).length);
    }
    return state.labelColors[label];
  }

  // Sorted, so a given store always shows the same colours.
  function assignLabelColors(labels) {
    labels.slice().sort().forEach(colorFor);
  }

  function swatch(label) {
    var s = el("span", { className: "swatch" });
    s.style.background = colorFor(label);
    return s;
  }

  function formatValue(value) {
    if (value === null || value === undefined) return "null";
    if (typeof value === "string") return value;
    return JSON.stringify(value);
  }

  function showError(message) {
    var banner = $("error-banner");
    if (!message) { banner.hidden = true; return; }
    banner.textContent = message;
    banner.hidden = false;
  }

  // In the notebook widget (cvcdocdb/viewer/notebook.py) the page has no
  // server: it asks its parent window, which relays to the Python kernel.
  var BRIDGE = window.CVCDOCDB_BRIDGE === true;
  var BRIDGE_TIMEOUT_MS = 30000;
  var pending = {};
  var nextRequestId = 1;

  if (BRIDGE) {
    window.addEventListener("message", function (event) {
      if (event.source !== window.parent) return;
      var msg = event.data;
      if (msg && msg.type === "cvcdocdb-theme") { applyTheme(msg.theme); return; }
      if (msg && msg.type === "cvcdocdb-refresh") { refresh(); return; }
      if (!msg || msg.type !== "cvcdocdb-api" || !pending[msg.id]) return;
      var request = pending[msg.id];
      delete pending[msg.id];
      clearTimeout(request.timer);
      if (msg.ok) { request.resolve(msg.body); return; }
      var error = new Error(msg.body && msg.body.error ? msg.body.error : "Request failed");
      error.status = msg.status;
      request.reject(error);
    });
  }

  // "dark"/"light" from the widget (following JupyterLab, VS Code...);
  // anything else falls back to the OS preference (see viewer.css).
  function applyTheme(theme) {
    if (theme === "dark" || theme === "light") document.documentElement.setAttribute("data-theme", theme);
    else document.documentElement.removeAttribute("data-theme");
    if (cy) cy.style().selector("node").style("color", getComputedStyle(document.body).color).update();
  }

  function bridgeRequest(path, pairs) {
    return new Promise(function (resolve, reject) {
      var id = nextRequestId++;
      var timer = setTimeout(function () {
        delete pending[id];
        reject(new Error("No answer from the Python kernel: is it still running?"));
      }, BRIDGE_TIMEOUT_MS);
      pending[id] = { resolve: resolve, reject: reject, timer: timer };
      window.parent.postMessage({ type: "cvcdocdb-api", id: id, path: path, params: pairs }, "*");
    });
  }

  function api(path, params) {
    var pairs = (params || []).filter(function (pair) {
      return pair[1] !== null && pair[1] !== undefined && pair[1] !== "";
    }).map(function (pair) { return [pair[0], String(pair[1])]; });
    if (BRIDGE) return bridgeRequest(path, pairs);
    var url = new URL(path, window.location.href);
    pairs.forEach(function (pair) { url.searchParams.append(pair[0], pair[1]); });
    var headers = { "Accept": "application/json" };
    if (token) headers["Authorization"] = "Bearer " + token;
    return fetch(url.pathname + url.search, { headers: headers, credentials: "same-origin" })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (body) {
          if (!response.ok) {
            var msg = body && body.error ? body.error : response.statusText;
            if (response.status === 401) msg = "Unauthorized: open the viewer with ?token=<token> in the URL.";
            var error = new Error(msg);
            error.status = response.status;
            throw error;
          }
          return body;
        });
      });
  }

  // ------------------------------------------------------------------
  // Sidebar: summary
  // ------------------------------------------------------------------

  function loadSummary() {
    return api("api/summary").then(function (summary) {
      $("store-info").textContent = summary.backend + " · " + summary.node_count + " nodes · " +
        summary.relation_count + " relations";
      assignLabelColors(summary.labels.map(function (entry) { return entry.label; }));
      var labels = $("label-list");
      clear(labels);
      labels.appendChild(facet("All nodes", summary.node_count, null, !state.labels.length, function () {
        setLabels([]);
      }));
      summary.labels.forEach(function (entry) {
        labels.appendChild(facet(entry.label, entry.count, entry.label, state.labels.indexOf(entry.label) >= 0,
                                 function () { toggleLabel(entry.label); }));
      });
      var rels = $("relation-list");
      clear(rels);
      if (!summary.relation_types.length) rels.appendChild(el("li", { className: "muted", text: "none" }));
      summary.relation_types.forEach(function (entry) {
        var box = el("input", { type: "checkbox" });
        box.checked = !state.hiddenTypes[entry.type];
        box.addEventListener("change", function () {
          if (box.checked) delete state.hiddenTypes[entry.type];
          else state.hiddenTypes[entry.type] = true;
          applyEdgeVisibility();
        });
        var item = el("li", null, [box, el("span", { className: "name", text: entry.type }),
          el("span", { className: "count", text: String(entry.count) })]);
        item.addEventListener("click", function (event) {
          if (event.target !== box) { box.checked = !box.checked; box.dispatchEvent(new Event("change")); }
        });
        rels.appendChild(item);
      });
    });
  }

  function facet(name, count, label, active, onClick) {
    var item = el("li", { className: active ? "active" : "", onclick: onClick }, [
      label ? swatch(label) : el("span", { className: "swatch" }),
      el("span", { className: "name", text: name, title: name }),
      el("span", { className: "count", text: String(count) })
    ]);
    item.dataset.label = label || "";
    return item;
  }

  /** The selected node types filter both the table and the graph canvas;
   * none selected shows every type. */
  function setLabels(labels) {
    state.labels = labels;
    state.offset = 0;
    Array.prototype.forEach.call($("label-list").children, function (li) {
      var label = li.dataset.label || null;
      li.className = (label === null ? !labels.length : labels.indexOf(label) >= 0) ? "active" : "";
    });
    renderChips();
    applyLabelFilter(true);
    search();
  }

  function toggleLabel(label) {
    var index = state.labels.indexOf(label);
    setLabels(index >= 0
      ? state.labels.filter(function (l) { return l !== label; })
      : state.labels.concat([label]));
  }

  // ------------------------------------------------------------------
  // Filters and results
  // ------------------------------------------------------------------

  var OP_TEXT = { eq: "=", ne: "≠", contains: "contains", gt: ">", gte: "≥", lt: "<", lte: "≤", exists: "exists" };

  function renderChips() {
    var chips = $("filter-chips");
    clear(chips);
    state.labels.forEach(function (label) {
      chips.appendChild(chip([swatch(label), " label: " + label], function () { toggleLabel(label); }));
    });
    if (state.text) {
      chips.appendChild(chip(["text: “" + state.text + "”"], function () {
        state.text = ""; $("search-text").value = ""; state.offset = 0; renderChips(); search();
      }));
    }
    state.filters.forEach(function (f, index) {
      var text = f.prop + " " + OP_TEXT[f.op] + (f.op === "exists" ? "" : " " + f.value);
      chips.appendChild(chip([text], function () {
        state.filters.splice(index, 1); state.offset = 0; renderChips(); search();
      }));
    });
  }

  function chip(content, onRemove) {
    return el("span", { className: "chip" }, content.concat([
      el("button", { type: "button", title: "Remove", "aria-label": "Remove filter", text: "×", onclick: onRemove })
    ]));
  }

  function search() {
    var params = state.labels.map(function (label) { return ["label", label]; })
      .concat([["q", state.text], ["offset", state.offset], ["limit", state.limit]]);
    state.filters.forEach(function (f) { params.push(["f", f.prop + ":" + f.op + ":" + f.value]); });
    return api("api/nodes", params).then(function (result) {
      showError(null);
      state.items = result.items;
      state.total = result.total;
      state.exact = result.total_is_exact;
      state.limit = result.limit;
      renderResults();
    }).catch(function (err) { showError(err.message); });
  }

  function renderResults() {
    var body = $("results-table").querySelector("tbody");
    clear(body);
    state.items.forEach(function (item) {
      rememberProps(item);
      var props = Object.keys(item.properties).slice(0, 8).map(function (key) {
        return [el("b", { text: key }), ": " + formatValue(item.properties[key]) + "  "];
      });
      var row = el("tr", { className: item.id === state.selectedId ? "selected" : "" }, [
        el("td", { text: item.caption, title: "id " + item.id }),
        el("td", null, [el("span", { className: "label-tag" }, [swatch(item.main_label), item.main_label])]),
        el("td", { className: "props" }, [].concat.apply([], props)),
        el("td", null, [el("button", {
          type: "button", className: "secondary", text: "Graph",
          title: "Add to the graph and show its neighbours",
          onclick: function (event) { event.stopPropagation(); addNodes([item], true); expand(item.id); }
        })])
      ]);
      row.addEventListener("click", function () { showDetail(item.id); addNodes([item], true); });
      body.appendChild(row);
    });
    $("results-empty").hidden = state.items.length > 0;
    var from = state.total ? state.offset + 1 : 0;
    var to = state.offset + state.items.length;
    $("page-info").textContent = from + "–" + to + " of " + state.total + (state.exact ? "" : "+");
    $("page-prev").disabled = state.offset === 0;
    $("page-next").disabled = to >= state.total;
    var options = $("known-props");
    clear(options);
    Object.keys(state.knownProps).sort().forEach(function (prop) { options.appendChild(el("option", { value: prop })); });
  }

  function rememberProps(item) {
    Object.keys(item.properties || {}).forEach(function (key) { state.knownProps[key] = true; });
  }

  // ------------------------------------------------------------------
  // Detail panel
  // ------------------------------------------------------------------

  function showDetail(id) {
    state.selectedId = id;
    Array.prototype.forEach.call($("results-table").querySelectorAll("tbody tr"), function (row, index) {
      row.className = state.items[index] && state.items[index].id === id ? "selected" : "";
    });
    cy.$(":selected").unselect();
    var graphNode = cy.getElementById(String(id));
    if (graphNode.nonempty()) graphNode.select();
    var panel = $("detail");
    if (LIGHT) document.body.classList.add("detail-open");
    return api("api/nodes/" + encodeURIComponent(id)).then(function (node) {
      rememberProps(node);
      clear(panel);
      panel.appendChild(el("h2", { text: node.caption }));
      panel.appendChild(el("div", null, node.labels.map(function (label) {
        return el("span", { className: "label-tag" }, [swatch(label), label + "  "]);
      })));
      panel.appendChild(el("div", { className: "muted", text: "id " + node.id }));
      panel.appendChild(el("div", { className: "actions" }, [
        el("button", { type: "button", text: "Expand neighbours", onclick: function () {
          addNodes([node], false); expand(node.id);
        } }),
        el("button", { type: "button", className: "secondary", text: "Focus", onclick: function () {
          var n = cy.getElementById(String(node.id));
          if (n.nonempty()) cy.animate({ center: { eles: n }, zoom: Math.max(cy.zoom(), 1.2) }, { duration: 250 });
        } })
      ]));
      var rows = [];
      if (node.pk) rows.push(["pk", formatValue(node.pk)]);
      Object.keys(node.properties).forEach(function (key) { rows.push([key, formatValue(node.properties[key])]); });
      panel.appendChild(el("h3", { text: "Properties" }));
      if (!rows.length) panel.appendChild(el("p", { className: "muted", text: "No properties." }));
      else panel.appendChild(el("table", null, rows.map(function (r) {
        return el("tr", null, [el("td", { className: "key", text: r[0] }), el("td", { className: "value", text: r[1] })]);
      })));
    }).catch(function (err) { showError(err.message); });
  }

  // ------------------------------------------------------------------
  // Graph
  // ------------------------------------------------------------------

  function accentColor() {
    return getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#2f6fde";
  }

  var cy = cytoscape({
    container: $("cy"),
    wheelSensitivity: 0.3,
    minZoom: 0.05,
    maxZoom: 4,
    style: [
      { selector: "node", style: {
        "background-color": "data(color)", "label": "data(caption)", "font-size": 10,
        "color": getComputedStyle(document.body).color, "text-valign": "bottom", "text-margin-y": 3,
        "width": "data(size)", "height": "data(size)", "text-max-width": 120, "text-wrap": "ellipsis",
        "border-width": 0, "min-zoomed-font-size": 7
      } },
      { selector: "node.lod-hidden", style: { "display": "none" } },
      { selector: "node:selected", style: { "border-width": 3, "border-color": accentColor() } },
      { selector: "node.expanded", style: { "shape": "round-rectangle" } },
      { selector: "edge", style: {
        "width": 1.5, "line-color": "#9aa2b4", "target-arrow-color": "#9aa2b4",
        "target-arrow-shape": "triangle", "curve-style": "bezier", "label": LIGHT ? "" : "data(type)",
        "font-size": 8, "color": "#8a93a6", "text-rotation": "autorotate", "text-background-opacity": 0
      } },
      { selector: "edge.show-label", style: { "label": "data(type)" } },
      { selector: "edge.hidden-type", style: { "display": "none" } },
      { selector: "node.hidden-label", style: { "display": "none" } }
    ],
    layout: { name: "grid" }
  });

  function graphNodeData(node) {
    return { id: String(node.id), nodeId: node.id, caption: node.caption, label: node.main_label,
             color: colorFor(node.main_label) };
  }

  /** Hide the canvas nodes (and so their relations) of the types that aren't
   * selected; with *relayout*, lay out and fit what is left. */
  function applyLabelFilter(relayout) {
    var labels = state.labels;
    cy.batch(function () {
      cy.nodes().forEach(function (node) {
        node.toggleClass("hidden-label", labels.length > 0 && labels.indexOf(node.data("label")) < 0);
      });
    });
    if (relayout && cy.nodes().length) runLayout();
    updateGraphCount(false);
  }

  function nodeShown(node) {
    return !node.hasClass("hidden-label") && !node.hasClass("lod-hidden");
  }

  function visibleEdges() {
    return cy.edges().filter(function (edge) {
      return !edge.hasClass("hidden-type") && nodeShown(edge.source()) && nodeShown(edge.target());
    });
  }

  // ------------------------------------------------------------------
  // Level of detail: with many nodes, zoomed out shows only the most
  // connected ones (drawn bigger); zooming in reveals the rest.
  // ------------------------------------------------------------------

  var LOD_MIN_NODES = 40;        // up to this many, always all of them
  var LOD_BASE_FRACTION = 0.25;  // share shown at the zoom of the last layout
  var lodBaseZoom = null;
  var lodRanked = null;          // filtered canvas nodes, most connected first
  var lodFrame = null;

  function lodEnabled() { return $("lod-toggle").checked; }

  /** Rank the nodes by degree and size them by it (after each layout): the
   * square root keeps hubs clearly bigger without dwarfing everything else. */
  function rankNodes() {
    var nodes = cy.nodes().not(".hidden-label");
    lodRanked = nodes.sort(function (a, b) { return b.degree(false) - a.degree(false); });
    var top = lodRanked.length ? Math.max(1, lodRanked[0].degree(false)) : 1;
    lodRanked.forEach(function (node) {
      node.data("size", 14 + Math.round(30 * Math.sqrt(node.degree(false) / top)));
    });
  }

  /** How many of *total* ranked nodes to show at *ratio* times the base zoom. */
  function lodKeepCount(total, ratio) {
    if (total <= LOD_MIN_NODES) return total;
    return Math.max(LOD_MIN_NODES, Math.ceil(total * Math.min(1, LOD_BASE_FRACTION * ratio)));
  }

  function applyLevelOfDetail() {
    lodFrame = null;
    if (!lodRanked) return;
    var on = lodEnabled() && lodBaseZoom;
    var keep = on ? lodKeepCount(lodRanked.length, cy.zoom() / lodBaseZoom) : lodRanked.length;
    // Always shown: the selected node, its neighbours and the expanded nodes.
    var selected = cy.nodes(":selected");
    var pinned = selected.union(selected.neighborhood().nodes()).union(cy.nodes(".expanded"));
    cy.batch(function () {
      cy.nodes(".hidden-label").removeClass("lod-hidden");
      lodRanked.forEach(function (node, i) {
        node.toggleClass("lod-hidden", i >= keep && !pinned.contains(node));
      });
    });
    updateGraphCount(false);
  }

  function scheduleLevelOfDetail() {
    if (lodFrame === null) lodFrame = window.requestAnimationFrame(applyLevelOfDetail);
  }

  function updateGraphCount(capped) {
    var total = cy.nodes().length;
    var filtered = cy.nodes().not(".hidden-label").length;
    var shown = cy.nodes().filter(nodeShown).length;
    var nodes = shown === total ? String(shown) : shown + " of " + total;
    var more = shown < filtered ? " (zoom in for more)" : "";
    $("graph-count").textContent = nodes + " nodes · " + visibleEdges().length + " relations shown" + more;
    var notice = $("graph-notice");
    if (capped) {
      notice.textContent = "Display limit reached (" + MAX_RENDERED_NODES +
        " nodes): some nodes were not added. Clear the graph or work from the table.";
      notice.hidden = false;
    } else if (cy.nodes().length < MAX_RENDERED_NODES) {
      notice.hidden = true;
    }
  }

  /** Add nodes (summaries) to the canvas; returns whether the cap cut some. */
  function addNodes(nodes, relayout, near) {
    var capped = false;
    var added = [];
    var center = near && near.nonempty() ? near.position() : null;
    nodes.forEach(function (node) {
      if (cy.getElementById(String(node.id)).nonempty()) return;
      if (cy.nodes().length >= MAX_RENDERED_NODES) { capped = true; return; }
      // Default size until the next layout ranks the nodes (rankNodes).
      var spec = { group: "nodes", data: Object.assign({ size: 22 }, graphNodeData(node)) };
      if (center) spec.position = { x: center.x + (Math.random() - 0.5) * 160, y: center.y + (Math.random() - 0.5) * 160 };
      added.push(cy.add(spec));
    });
    if (state.labels.length) {
      added.forEach(function (node) { node.toggleClass("hidden-label", state.labels.indexOf(node.data("label")) < 0); });
    }
    if (added.length && relayout) runLayout();
    updateGraphCount(capped);
    return capped;
  }

  function edgeId(rel) { return "e:" + rel.source + ":" + rel.type + ":" + rel.target; }

  function addEdge(rel) {
    var id = edgeId(rel);
    if (cy.getElementById(id).nonempty()) return;
    if (cy.getElementById(String(rel.source)).empty() || cy.getElementById(String(rel.target)).empty()) return;
    var edge = cy.add({ group: "edges", data: { id: id, source: String(rel.source), target: String(rel.target), type: rel.type } });
    if (state.hiddenTypes[rel.type]) edge.addClass("hidden-type");
  }

  function applyEdgeVisibility() {
    cy.edges().forEach(function (edge) {
      edge.toggleClass("hidden-type", !!state.hiddenTypes[edge.data("type")]);
    });
  }

  var MAX_FIT_ZOOM = 1.5;  // a handful of nodes shouldn't fill the canvas

  function runLayout() {
    // Only what the node-type filter shows (hidden nodes keep their place).
    var shown = cy.elements().not(".hidden-label").filter(function (e) {
      return e.isNode() || (!e.source().hasClass("hidden-label") && !e.target().hasClass("hidden-label"));
    });
    if (!shown.length) return;
    var randomize = shown.nodes().length < 3;
    var layout = shown.layout({ name: "cose", animate: false, randomize: randomize, fit: true, padding: 30,
                                nodeRepulsion: 8000, idealEdgeLength: 80 });
    layout.one("layoutstop", function () {
      if (cy.zoom() > MAX_FIT_ZOOM) { cy.zoom(MAX_FIT_ZOOM); cy.center(); }
      lodBaseZoom = cy.zoom();
      rankNodes();
      applyLevelOfDetail();
    });
    layout.run();
  }

  function expand(id) {
    var direction = $("expand-direction").value;
    var types = Object.keys(state.hiddenTypes).length ? visibleTypes() : [null];
    if (!types.length) { showError("All relation types are unticked: nothing to follow."); return Promise.resolve(); }
    var source = cy.getElementById(String(id));
    return Promise.all(types.map(function (type) {
      return api("api/nodes/" + encodeURIComponent(id) + "/neighbours",
                 [["direction", direction], ["type", type], ["limit", NEIGHBOUR_LIMIT]]);
    })).then(function (results) {
      showError(null);
      var truncated = false;
      results.forEach(function (result) {
        truncated = truncated || result.truncated;
        addNodes(result.relations.map(function (r) { return r.neighbour; }), false, source);
        result.relations.forEach(addEdge);
      });
      source.addClass("expanded");
      runLayout();
      updateGraphCount(false);
      if (truncated) {
        var notice = $("graph-notice");
        notice.textContent = "Only the first " + NEIGHBOUR_LIMIT + " relations of this node are shown.";
        notice.hidden = false;
      }
    }).catch(function (err) { showError(err.message); });
  }

  function visibleTypes() {
    return Array.prototype.map.call($("relation-list").querySelectorAll("li"), function (li) {
      var box = li.querySelector("input");
      var name = li.querySelector(".name");
      return box && box.checked && name ? name.textContent : null;
    }).filter(function (t) { return t !== null; });
  }

  // ------------------------------------------------------------------
  // Refresh: the data changed underneath (Refresh button, or refresh() on
  // the notebook widget). With data-load-all on <html> (the widget's
  // load_all) the canvas shows the whole graph, up to MAX_RENDERED_NODES.
  // ------------------------------------------------------------------

  var LOAD_ALL = document.documentElement.getAttribute("data-load-all") === "true";
  var PAGE_MAX = 200;

  function updateNodes(nodes) {
    nodes.forEach(function (node) {
      var element = cy.getElementById(String(node.id));
      if (element.nonempty()) element.data(graphNodeData(node));
    });
  }

  function fetchAllNodes() {
    var nodes = [];
    function page(offset) {
      return api("api/nodes", [["offset", offset], ["limit", PAGE_MAX]]).then(function (result) {
        nodes = nodes.concat(result.items);
        var more = result.items.length === PAGE_MAX && nodes.length <= MAX_RENDERED_NODES;
        return more ? page(offset + PAGE_MAX) : nodes;
      });
    }
    return page(0);
  }

  function loadWholeGraph() {
    return fetchAllNodes().then(function (nodes) {
      var present = {};
      nodes.forEach(function (node) { present[String(node.id)] = true; });
      cy.nodes().filter(function (n) { return !present[n.id()]; }).remove();
      updateNodes(nodes);
      var capped = addNodes(nodes, false);
      var ids = cy.nodes().map(function (n) { return n.data("nodeId"); });
      // One request for every edge of the canvas, not one per node.
      return api("api/relations", ids.map(function (id) { return ["ids", id]; })).then(function (result) {
        var seen = {};
        result.relations.forEach(function (rel) { seen[edgeId(rel)] = true; addEdge(rel); });
        cy.edges().filter(function (e) { return !seen[e.id()]; }).remove();
        runLayout();
        updateGraphCount(capped || result.truncated);
      });
    });
  }

  /** Drop deleted nodes, update the rest and re-expand the expanded ones. */
  function refreshCanvas() {
    var ids = cy.nodes().map(function (n) { return n.data("nodeId"); });
    return Promise.all(ids.map(function (id) {
      return api("api/nodes/" + encodeURIComponent(id)).catch(function (err) {
        if (err.status === 404) return null;
        throw err;
      });
    })).then(function (nodes) {
      nodes.forEach(function (node, i) {
        if (node) updateNodes([node]);
        else cy.getElementById(String(ids[i])).remove();
      });
      var expanded = cy.nodes(".expanded");
      // Every edge on the canvas comes from expanding a node: rebuild them.
      expanded.connectedEdges().remove();
      return Promise.all(expanded.map(function (n) { return expand(n.data("nodeId")); }));
    }).then(function () { updateGraphCount(false); });
  }

  function refresh() {
    showError(null);
    return loadSummary().then(search).then(LOAD_ALL ? loadWholeGraph : refreshCanvas)
      .catch(function (err) { showError(err.message); });
  }

  cy.on("tap", "node", function (event) { showDetail(event.target.data("nodeId")); });
  // Light layout: relation names only on the selected node's edges.
  cy.on("select", "node", function (event) {
    event.target.connectedEdges().addClass("show-label");
    scheduleLevelOfDetail();
  });
  cy.on("unselect", "node", function (event) {
    event.target.connectedEdges().removeClass("show-label");
    scheduleLevelOfDetail();
  });
  cy.on("zoom", scheduleLevelOfDetail);

  function setTableOpen(open) {
    document.body.classList.toggle("table-open", open);
    $("toggle-table").setAttribute("aria-expanded", String(open));
    cy.resize();
    if (cy.nodes().length) {
      cy.fit(undefined, 30);
      if (cy.zoom() > MAX_FIT_ZOOM) { cy.zoom(MAX_FIT_ZOOM); cy.center(); }
    }
  }
  cy.on("dbltap", "node", function (event) { expand(event.target.data("nodeId")); });

  // ------------------------------------------------------------------
  // Wiring
  // ------------------------------------------------------------------

  $("search-form").addEventListener("submit", function (event) {
    event.preventDefault();
    if (LIGHT) setTableOpen(true);
    state.text = $("search-text").value.trim();
    state.offset = 0;
    renderChips();
    search();
  });

  $("filter-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var prop = $("filter-prop").value.trim();
    var op = $("filter-op").value;
    var value = $("filter-value").value;
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(prop)) { showError("Property names are letters, digits and '_'."); return; }
    state.filters.push({ prop: prop, op: op, value: op === "exists" ? "" : value });
    $("filter-prop").value = ""; $("filter-value").value = "";
    state.offset = 0;
    renderChips();
    search();
  });

  $("filter-op").addEventListener("change", function () {
    $("filter-value").disabled = $("filter-op").value === "exists";
  });

  $("page-prev").addEventListener("click", function () {
    state.offset = Math.max(0, state.offset - state.limit); search();
  });
  $("page-next").addEventListener("click", function () {
    state.offset += state.limit; search();
  });
  $("page-size").addEventListener("change", function () {
    state.limit = parseInt($("page-size").value, 10); state.offset = 0; search();
  });
  $("add-page-to-graph").addEventListener("click", function () { addNodes(state.items, true); });

  $("graph-refresh").addEventListener("click", refresh);
  $("toggle-table").addEventListener("click", function () {
    setTableOpen(!document.body.classList.contains("table-open"));
  });
  $("detail-close").addEventListener("click", function () {
    document.body.classList.remove("detail-open");
    cy.$(":selected").unselect();
  });
  $("graph-fit").addEventListener("click", function () { cy.fit(undefined, 30); });
  $("graph-layout").addEventListener("click", runLayout);
  $("lod-toggle").addEventListener("change", applyLevelOfDetail);
  $("graph-reset").addEventListener("click", function () {
    cy.elements().remove();
    lodRanked = null;
    $("graph-notice").hidden = true;
    updateGraphCount(false);
  });

  loadSummary().then(search).then(function () { if (LOAD_ALL) return loadWholeGraph(); }).catch(function (err) {
    $("store-info").textContent = "Not connected";
    showError(err.message);
  });
  updateGraphCount(false);
})();
