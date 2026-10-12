/* cvcdocdb graph viewer — node repulsion (a small force simulation).
 *
 * Plain functions with no DOM, used by viewer.js while a node is dragged and
 * after each layout, and tested with Node.js (test/test_viewer_physics.py):
 * every pair of nodes repels (and pushes hard when closer than minDistance,
 * plus the nodes' radii), every relation is a spring of springLength, and
 * fixed nodes (the one being dragged) push and pull without moving. */
(function (root) {
  "use strict";

  var DEFAULTS = {
    minDistance: 30,       // gap kept between the borders of two nodes
    repulsion: 3000,       // strength of the long-range push, ~ 1 / d²
    cutoff: 300,           // no push between nodes farther apart than this
    collision: 0.5,        // strength of the push inside minDistance
    springLength: 90,      // rest length of a relation
    springStrength: 0.02,  // pull of a relation per unit of stretch
    damping: 0.6,          // share of the velocity kept from one step to the next
    friction: 0.1,         // net forces weaker than this don't move a node (comes to rest)
    maxSpeed: 30           // cap on how far a node moves in one step
  };

  function settings(options) {
    var merged = {};
    Object.keys(DEFAULTS).forEach(function (key) {
      merged[key] = options && typeof options[key] === "number" ? options[key] : DEFAULTS[key];
    });
    return merged;
  }

  /** One simulation step. *nodes* are {x, y, vx?, vy?, radius?, fixed?, ignore?}
   * (updated in place); *edges* are [i, j] pairs of indices into *nodes*.
   * Returns the largest distance a node moved, to tell when it is at rest. */
  function step(nodes, edges, options) {
    var o = settings(options);
    var n = nodes.length;
    var fx = new Array(n).fill(0);
    var fy = new Array(n).fill(0);
    var cutoff2 = o.cutoff * o.cutoff;
    var i, j;

    for (i = 0; i < n; i++) {
      var a = nodes[i];
      if (a.ignore) continue;
      for (j = i + 1; j < n; j++) {
        var b = nodes[j];
        if (b.ignore) continue;
        var dx = b.x - a.x, dy = b.y - a.y;
        var d2 = dx * dx + dy * dy;
        if (d2 < 1e-6) {
          // Same spot: part them in a fixed, pair-dependent direction.
          var angle = (i * 7 + j * 13) * 2.399963;
          dx = Math.cos(angle); dy = Math.sin(angle); d2 = 1;
        }
        var gap = o.minDistance + (a.radius || 0) + (b.radius || 0);
        if (d2 >= cutoff2 && d2 >= gap * gap) continue;
        var d = Math.sqrt(d2);
        // Fades to zero at the cutoff, so nodes don't jitter on its edge.
        var f = d2 < cutoff2 ? o.repulsion * (1 / d2 - 1 / cutoff2) : 0;
        if (d < gap) f += o.collision * (gap - d);
        var ux = dx / d, uy = dy / d;
        fx[i] -= f * ux; fy[i] -= f * uy;
        fx[j] += f * ux; fy[j] += f * uy;
      }
    }

    edges.forEach(function (edge) {
      var s = nodes[edge[0]], t = nodes[edge[1]];
      if (!s || !t || s.ignore || t.ignore || s === t) return;
      var dx = t.x - s.x, dy = t.y - s.y;
      var d = Math.sqrt(dx * dx + dy * dy);
      if (d < 1e-3) return;
      var f = o.springStrength * (d - o.springLength);
      var ux = dx / d, uy = dy / d;
      fx[edge[0]] += f * ux; fy[edge[0]] += f * uy;
      fx[edge[1]] -= f * ux; fy[edge[1]] -= f * uy;
    });

    var moved = 0;
    for (i = 0; i < n; i++) {
      var node = nodes[i];
      if (node.ignore) continue;
      if (node.fixed) { node.vx = 0; node.vy = 0; continue; }
      // Friction: a nearly balanced node stops instead of creeping on.
      var force = Math.sqrt(fx[i] * fx[i] + fy[i] * fy[i]);
      var keep = force > o.friction ? (force - o.friction) / force : 0;
      var vx = ((node.vx || 0) + fx[i] * keep) * o.damping;
      var vy = ((node.vy || 0) + fy[i] * keep) * o.damping;
      var speed = Math.sqrt(vx * vx + vy * vy);
      if (speed > o.maxSpeed) { vx *= o.maxSpeed / speed; vy *= o.maxSpeed / speed; speed = o.maxSpeed; }
      node.vx = vx; node.vy = vy;
      node.x += vx; node.y += vy;
      if (speed > moved) moved = speed;
    }
    return moved;
  }

  var CvcdocdbPhysics = { DEFAULTS: DEFAULTS, step: step };
  if (typeof module !== "undefined" && module.exports) module.exports = CvcdocdbPhysics;
  else root.CvcdocdbPhysics = CvcdocdbPhysics;
})(typeof window !== "undefined" ? window : this);
