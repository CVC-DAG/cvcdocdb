/* cvcdocdb graph viewer — level of detail.
 *
 * With many nodes, the canvas zoomed out shows only the most connected ones
 * (drawn bigger) and zooming in reveals the rest. Plain functions with no
 * DOM, used by viewer.js and tested with Node.js (test/test_viewer_lod.py). */
(function (root) {
  "use strict";

  var MIN_NODES = 40;        // up to this many, always all of them
  var BASE_FRACTION = 0.25;  // share shown at the zoom of the last layout
  var MIN_SIZE = 14;         // size of a node with no relations
  var MAX_SIZE = 44;         // size of the most connected node

  /** How many of *total* ranked nodes to show at *ratio* times the base zoom. */
  function keepCount(total, ratio) {
    if (total <= MIN_NODES) return total;
    return Math.max(MIN_NODES, Math.ceil(total * Math.min(1, BASE_FRACTION * ratio)));
  }

  /** *nodes* ({degree}) sorted most connected first; ties keep their order. */
  function rank(nodes) {
    return nodes.slice().sort(function (a, b) { return b.degree - a.degree; });
  }

  /** Size for a node of *degree* when the most connected has *topDegree*:
   * the square root keeps hubs clearly bigger without dwarfing the rest. */
  function nodeSize(degree, topDegree) {
    var top = Math.max(1, topDegree);
    return MIN_SIZE + Math.round((MAX_SIZE - MIN_SIZE) * Math.sqrt(degree / top));
  }

  /** The ids of *rankedIds* to hide when keeping *keep*, except the *pinned* (a Set). */
  function hiddenIds(rankedIds, keep, pinned) {
    return rankedIds.filter(function (id, i) { return i >= keep && !pinned.has(id); });
  }

  var CvcdocdbLod = {
    MIN_NODES: MIN_NODES, BASE_FRACTION: BASE_FRACTION, MIN_SIZE: MIN_SIZE, MAX_SIZE: MAX_SIZE,
    keepCount: keepCount, rank: rank, nodeSize: nodeSize, hiddenIds: hiddenIds
  };
  if (typeof module !== "undefined" && module.exports) module.exports = CvcdocdbLod;
  else root.CvcdocdbLod = CvcdocdbLod;
})(typeof window !== "undefined" ? window : this);
