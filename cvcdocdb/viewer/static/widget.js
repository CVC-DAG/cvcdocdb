/* cvcdocdb graph viewer — anywidget front end (see cvcdocdb/viewer/notebook.py).
 *
 * Shows the viewer page in a sandboxed iframe and relays its API requests
 * (postMessage) to the kernel and the kernel's answers back. Request ids are
 * prefixed with a per-view id, so several views of one widget don't pick up
 * each other's answers. It also tells the page the notebook's theme. */
var MESSAGE_TYPE = "cvcdocdb-api";
var THEME_TYPE = "cvcdocdb-theme";
var REFRESH_TYPE = "cvcdocdb-refresh";

// "dark" or "light" for the notebook around the widget, or null if unknown
// (the page then follows the OS preference).
function hostTheme(el) {
  var body = document.body;
  var jupyterLight = body.getAttribute("data-jp-theme-light");  // JupyterLab, Notebook 7
  if (jupyterLight === "true") return "light";
  if (jupyterLight === "false") return "dark";
  var vscode = body.classList;
  if (vscode.contains("vscode-light") || vscode.contains("vscode-high-contrast-light")) return "light";
  if (vscode.contains("vscode-dark") || vscode.contains("vscode-high-contrast")) return "dark";
  // Anywhere else: how bright the background behind the widget is.
  for (var node = el; node && node.nodeType === 1; node = node.parentElement) {
    var rgba = (getComputedStyle(node).backgroundColor.match(/[\d.]+/g) || []).map(Number);
    if (rgba.length >= 3 && (rgba.length < 4 || rgba[3] > 0)) {
      return 0.2126 * rgba[0] + 0.7152 * rgba[1] + 0.0722 * rgba[2] < 128 ? "dark" : "light";
    }
  }
  return null;
}

function render({ model, el }) {
  var viewId = Math.random().toString(36).slice(2) + ":";
  var frame = document.createElement("iframe");
  // Scripts only: an opaque origin, no access to the notebook page.
  frame.setAttribute("sandbox", "allow-scripts");
  frame.setAttribute("title", "cvcdocdb graph viewer");
  frame.style.width = "100%";
  frame.style.border = "0";
  frame.style.display = "block";

  function setHeight() { frame.style.height = model.get("height") + "px"; }
  function setPage() {
    // Start in the right theme; later changes go through sendTheme().
    var theme = hostTheme(el);
    var attrs = (theme ? " data-theme=\"" + theme + "\"" : "") + (model.get("load_all") ? " data-load-all=\"true\"" : "");
    frame.srcdoc = model.get("_page").replace("<html lang=\"en\">", "<html lang=\"en\"" + attrs + ">");
  }

  var lastTheme;
  function sendTheme() {
    var theme = hostTheme(el);
    if (theme === lastTheme || !frame.contentWindow) return;
    lastTheme = theme;
    frame.contentWindow.postMessage({ type: THEME_TYPE, theme: theme }, "*");
  }

  function fromPage(event) {
    if (event.source !== frame.contentWindow) return;
    var msg = event.data;
    if (!msg || msg.type !== MESSAGE_TYPE) return;
    model.send({ type: MESSAGE_TYPE, id: viewId + msg.id, path: msg.path, params: msg.params });
  }

  function fromKernel(msg) {
    if (msg && msg.type === REFRESH_TYPE && frame.contentWindow) {
      frame.contentWindow.postMessage({ type: REFRESH_TYPE }, "*");
      return;
    }
    if (!msg || msg.type !== MESSAGE_TYPE || typeof msg.id !== "string") return;
    if (msg.id.slice(0, viewId.length) !== viewId || !frame.contentWindow) return;
    var reply = Object.assign({}, msg, { id: Number(msg.id.slice(viewId.length)) });
    // A sandboxed frame has an opaque origin, so "*" is the only target.
    frame.contentWindow.postMessage(reply, "*");
  }

  setHeight();
  setPage();
  el.appendChild(frame);
  window.addEventListener("message", fromPage);
  model.on("msg:custom", fromKernel);
  model.on("change:height", setHeight);
  model.on("change:_page", setPage);
  model.on("change:load_all", setPage);
  // On load (the widget is in the document by then) and on theme switches.
  frame.addEventListener("load", function () { lastTheme = undefined; sendTheme(); });
  var observer = new MutationObserver(sendTheme);
  observer.observe(document.body, { attributes: true, attributeFilter: ["class", "data-jp-theme-light", "data-jp-theme-name"] });

  return function () {
    window.removeEventListener("message", fromPage);
    model.off("msg:custom", fromKernel);
    model.off("change:height", setHeight);
    model.off("change:_page", setPage);
    model.off("change:load_all", setPage);
    observer.disconnect();
  };
}

export default { render };
