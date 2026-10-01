(function () {
  "use strict";
  var q = document.getElementById("s-q"), kind = document.getElementById("s-kind"),
      anchor = document.getElementById("s-anchor"), per = document.getElementById("s-period"),
      list = document.getElementById("s-results"), count = document.getElementById("s-count"),
      empty = document.getElementById("s-empty"), more = document.getElementById("s-more");
  var all = [], matches = [], shown = 0, PAGE = 50, cites = {};
  var params = new URLSearchParams(location.search);

  function norm(s) { return (s || "").toString().toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, ""); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }

  function render(it) {
    var li = el("li", "work");
    var a = el("a", "work-title scholarly", it.title);
    a.href = it.url; a.target = "_blank"; a.rel = "noopener";
    li.appendChild(a);
    var meta = el("div", "work-meta mono");
    var by = (it.authors || []).slice(0, 3).join(", ") + ((it.authors || []).length > 3 ? " et al." : "");
    [by, it.venue || it.source, it.year || (it.date || "").slice(0, 4), it.period_label].forEach(function (x) {
      if (x) meta.appendChild(el("span", "", String(x)));
    });
    li.appendChild(meta);
    if (it.note) li.appendChild(el("p", "work-note", it.note));
    if ((it.anchors || []).length) li.appendChild(el("p", "work-cites mono", "Cites: " + it.anchors.map(function (k) { return cites[k] || k; }).join("; ")));
    return li;
  }

  function page() {
    var frag = document.createDocumentFragment();
    matches.slice(shown, shown + PAGE).forEach(function (it) { frag.appendChild(render(it)); });
    list.appendChild(frag);
    if (window.SCO_applyProxy) window.SCO_applyProxy(list);
    shown = Math.min(matches.length, shown + PAGE);
    more.hidden = shown >= matches.length;
  }

  function run() {
    var words = norm(q.value).split(/\s+/).filter(Boolean);
    matches = all.filter(function (it) {
      if (kind.value && it.kind !== kind.value) return false;
      if (anchor.value && (it.anchors || []).indexOf(anchor.value) < 0) return false;
      if (per.value && it.period !== per.value) return false;
      return words.every(function (w) { return it._t.indexOf(w) > -1; });
    });
    list.innerHTML = ""; shown = 0;
    count.textContent = matches.length + " of " + all.length + " works";
    empty.hidden = matches.length > 0;
    page();
  }

  Promise.all([fetch("items.json").then(function (r) { return r.json(); }),
               fetch("anchors.json").then(function (r) { return r.json(); })]).then(function (res) {
    cites = res[1];
    all = res[0].map(function (it) {
      it._t = norm([it.title, it.venue, it.source, (it.authors || []).join(" "), it.note, (it.terms || []).join(" ")].join(" "));
      return it;
    }).sort(function (a, b) { return String(b.year || b.date || "").localeCompare(String(a.year || a.date || "")); });
    if (params.get("anchor")) anchor.value = params.get("anchor");
    if (params.get("q")) q.value = params.get("q");
    run();
  }).catch(function () { count.textContent = "Could not load the archive index."; });

  var t;
  q.addEventListener("input", function () { clearTimeout(t); t = setTimeout(run, 150); });
  [kind, anchor, per].forEach(function (s) { s.addEventListener("change", run); });
  more.addEventListener("click", page);
})();
