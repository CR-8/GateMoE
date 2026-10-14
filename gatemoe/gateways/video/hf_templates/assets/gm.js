/* GateMoE template helper (works under the HyperFrames runtime AND under the
   plain Playwright fallback renderer, which does not load the HF runtime).
   SECURITY: templates must only put learner/LLM text into the DOM through
   GM.text()/textContent - never innerHTML - so "<script>", "</div>", "&amp;"
   etc. are displayed literally and can never execute or break layout. */
(function () {
  "use strict";
  function declaredDefaults() {
    var out = {};
    var raw = document.documentElement.getAttribute("data-composition-variables");
    if (!raw) return out;
    try {
      JSON.parse(raw).forEach(function (d) { if (d && typeof d.id === "string") out[d.id] = d["default"]; });
    } catch (e) { /* ignore */ }
    return out;
  }
  function vars() {
    if (window.__hyperframes && typeof window.__hyperframes.getVariables === "function") {
      return window.__hyperframes.getVariables() || {};
    }
    var v = declaredDefaults(), o = window.__hfVariables || {};
    for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) v[k] = o[k];
    return v;
  }
  /* Every GateMoE template takes ONE string variable "payload" holding JSON
     (HyperFrames variable types are string/number/color/boolean/enum/font/image -
     there is no array type, so lists travel as a JSON string). */
  function payload() {
    var p = vars().payload;
    if (typeof p === "string") { try { return JSON.parse(p) || {}; } catch (e) { return {}; } }
    return (p && typeof p === "object") ? p : {};
  }
  function text(el, s) { el.textContent = (s === undefined || s === null) ? "" : String(s); return el; }
  function el(tag, cls, s) { var e = document.createElement(tag); if (cls) e.className = cls; if (s !== undefined) text(e, s); return e; }
  function setLang(p) {
    document.documentElement.lang = p.lang || "en";
    document.documentElement.dir = p.dir === "rtl" ? "rtl" : "ltr";
  }
  /* Shrink font-size of `node` until `box` no longer overflows (deterministic, no timers).
     Vertical overflow only by default: GSAP from-states (e.g. x:40 on children) are already applied
     when this runs and translated children inflate scrollWidth, so horizontal checks are opt-in
     (`horiz`=true) and must only be used where nothing inside `box` is transformed. */
  function fit(node, box, startPx, minPx, horiz) {
    var px = startPx;
    node.style.fontSize = px + "px";
    while (px > minPx && (box.scrollHeight > box.clientHeight + 1 || (horiz && box.scrollWidth > box.clientWidth + 1))) {
      px -= 1; node.style.fontSize = px + "px";
    }
    return px;
  }
  function register(tl, id) {
    window.__timelines = window.__timelines || {};
    window.__timelines[id || "main"] = tl;
    window.__gmTimeline = tl; // used by the Playwright fallback renderer
  }
  /* Run `fn` (font-size fitting that MEASURES text) only after the web fonts
     needed for `sampleText` are loaded, otherwise we would measure fallback-font
     metrics. The promise is exposed two ways so frame 0 is never captured early:
       - window.__hf.buildReady.gmLayout : awaited by the HyperFrames runtime init
       - window.__gmReady                : awaited by the Playwright fallback  */
  function layout(fn, sampleText) {
    var stack = getComputedStyle(document.body).fontFamily;
    var t = sampleText || "A";
    var p;
    try {
      p = Promise.all([
        document.fonts.load("400 32px " + stack, t),
        document.fonts.load("700 32px " + stack, t)
      ]).then(function () { return document.fonts.ready; }).then(fn, fn);
    } catch (e) { fn(); p = Promise.resolve(); }
    window.__gmReady = p;
    if (window.__hf && typeof window.__hf === "object") {
      window.__hf.buildReady = window.__hf.buildReady || {};
      window.__hf.buildReady.gmLayout = p;
    }
    return p;
  }
  /* Frame chrome shared by every template (and drawn the same way by the Manim theme):
     lesson name bottom-left, "3 / 9" bottom-right, thin progress line along the bottom edge. */
  function chrome(p) {
    var foot = document.getElementById("footer");
    if (foot) {
      foot.textContent = "";
      foot.appendChild(el("span", "gm-ftext", p.footer || ""));
      var idx = Number(p.index) || 0, tot = Number(p.total) || 0;
      if (tot > 0 && idx > 0) foot.appendChild(el("span", "gm-count", idx + " / " + tot));
    }
    var idx2 = Number(p.index) || 0, tot2 = Number(p.total) || 0, root = document.getElementById("root");
    if (root && tot2 > 0 && idx2 > 0 && !document.getElementById("gm-progress")) {
      var track = el("div"); track.id = "gm-progress";
      var fill = el("div"); fill.style.width = Math.max(0, Math.min(100, 100 * idx2 / tot2)) + "%";
      track.appendChild(fill); root.appendChild(track);
    }
  }
  window.GM = { vars: vars, payload: payload, text: text, el: el, setLang: setLang, fit: fit,
                register: register, layout: layout, chrome: chrome };
})();
