(function () {
  "use strict";
  // Library proxy: rewrite scholarly links through the reader's chosen proxy.
  var sel = document.getElementById("proxy-select");
  var KEY = "sco-proxy";
  function get() { try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; } }
  function set(v) { try { localStorage.setItem(KEY, v); } catch (e) {} }
  window.SCO_applyProxy = function (root) {
    var prefix = get();
    (root || document).querySelectorAll("a.scholarly").forEach(function (a) {
      if (!a.dataset.orig) a.dataset.orig = a.getAttribute("href");
      a.href = prefix ? prefix + encodeURIComponent(a.dataset.orig) : a.dataset.orig;
    });
  };
  if (sel) {
    var list = [];
    try { list = JSON.parse(sel.dataset.proxies || "[]"); } catch (e) {}
    list.forEach(function (p) { var o = document.createElement("option"); o.value = p.prefix; o.textContent = p.label; sel.appendChild(o); });
    var o = document.createElement("option"); o.value = "__custom"; o.textContent = "Other (paste prefix)…"; sel.appendChild(o);
    var cur = get();
    if (cur && !list.some(function (p) { return p.prefix === cur; })) {
      var c = document.createElement("option"); c.value = cur; c.textContent = "Custom proxy"; sel.insertBefore(c, o);
    }
    sel.value = cur;
    sel.addEventListener("change", function () {
      var v = sel.value;
      if (v === "__custom") {
        v = (window.prompt("Paste your library's proxy prefix (ending in url=)") || "").trim();
        if (!v) { sel.value = get(); return; }
      }
      set(v); window.SCO_applyProxy();
    });
  }
  window.SCO_applyProxy();
})();
