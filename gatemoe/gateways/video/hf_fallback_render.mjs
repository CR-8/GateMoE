#!/usr/bin/env node
// GateMoE fallback renderer: renders a GateMoE template WITHOUT the HyperFrames CLI.
// Uses puppeteer-core (already installed as a hyperframes dependency - no browser download),
// a loopback-only static file server, deterministic GSAP seeking (window.__gmTimeline), and
// pipes PNG frames into ffmpeg.  Frames after the timeline ends are reused (static hold).
//
//   node hf_fallback_render.mjs --html work/job/index.html --vars work/job/vars.json --node-modules <hf>/node_modules \
//        --out out.mp4 --fps 24 --duration 4 --browser /usr/bin/chromium [--crf 20] [--width 1280 --height 720]
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { spawn } from "node:child_process";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const here = path.dirname(new URL(import.meta.url).pathname);

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, arr) => {
  if (a.startsWith("--")) acc.push([a.slice(2), arr[i + 1] && !arr[i + 1].startsWith("--") ? arr[i + 1] : "1"]);
  return acc;
}, []));
// puppeteer-core ships with hyperframes: pass that project's node_modules with --node-modules
const nodeModules = args["node-modules"] || process.env.GATEMOE_NODE_MODULES || path.join(here, "..", "node_modules");
const puppeteer = require(path.join(nodeModules, "puppeteer-core"));
const fps = Number(args.fps || 24), duration = Number(args.duration || 4);
const W = Number(args.width || 1280), H = Number(args.height || 720);
const crf = String(args.crf || 20);
const htmlPath = path.resolve(args.html), rootDir = path.dirname(htmlPath);
const vars = args.vars ? JSON.parse(await readFile(args.vars, "utf8")) : {};
const browserPath = args.browser || process.env.HYPERFRAMES_BROWSER_PATH || "/usr/bin/chromium";

const MIME = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
  ".ttf": "font/ttf", ".otf": "font/otf", ".woff2": "font/woff2", ".png": "image/png", ".jpg": "image/jpeg",
  ".svg": "image/svg+xml", ".json": "application/json" };
const server = createServer(async (req, res) => {
  try {
    const rel = decodeURIComponent(new URL(req.url, "http://x").pathname);
    const file = path.resolve(rootDir, "." + rel);               // follows the assets symlink
    if (!file.startsWith(rootDir + path.sep) && file !== rootDir) { res.writeHead(403).end(); return; }
    const p = (await stat(file)).isDirectory() ? path.join(file, "index.html") : file;
    res.writeHead(200, { "content-type": MIME[path.extname(p)] || "application/octet-stream" });
    res.end(await readFile(p));
  } catch { res.writeHead(404).end(); }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const port = server.address().port;

const t0 = Date.now();
const browser = await puppeteer.launch({
  executablePath: browserPath,
  headless: true,
  args: ["--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--mute-audio", "--font-render-hinting=none",
         "--force-color-profile=srgb", "--disable-dev-shm-usage", "--no-first-run", "--no-zygote"],
  defaultViewport: { width: W, height: H, deviceScaleFactor: 1 },
});
const page = await browser.newPage();
page.on("console", (m) => { if (m.type() === "error") console.error("[page]", m.text()); });
page.on("pageerror", (e) => console.error("[pageerror]", e.message));
// Same contract as HyperFrames: variables arrive as a JS object via JSON.parse, never as HTML.
await page.evaluateOnNewDocument((json) => { window.__hfVariables = JSON.parse(json); }, JSON.stringify(vars));
await page.goto(`http://127.0.0.1:${port}/${path.basename(htmlPath)}`, { waitUntil: "load", timeout: 60000 });
await page.evaluate(async () => { await (window.__gmReady || Promise.resolve()); await document.fonts.ready; });
const tlDur = await page.evaluate(() => (window.__gmTimeline ? window.__gmTimeline.duration() : 0));
const tSetup = Date.now();

const nFrames = Math.round(duration * fps);
const ff = spawn("ffmpeg", ["-v", "error", "-y", "-f", "image2pipe", "-framerate", String(fps), "-c:v", "png", "-i", "-",
  "-c:v", "libx264", "-preset", "veryfast", "-crf", crf, "-pix_fmt", "yuv420p", "-movflags", "+faststart",
  "-r", String(fps), path.resolve(args.out)], { stdio: ["pipe", "inherit", "inherit"] });
const ffDone = new Promise((r, j) => ff.on("close", (c) => (c === 0 ? r() : j(new Error("ffmpeg exit " + c)))));
const write = (buf) => new Promise((r) => (ff.stdin.write(buf) ? r() : ff.stdin.once("drain", r)));

let last = null, shots = 0;
for (let i = 0; i < nFrames; i++) {
  const t = i / fps;
  if (last && t > tlDur + 1 / fps) { await write(last); continue; }   // animation finished: hold last frame
  // suppressEvents=false so onUpdate-driven effects (count-ups) render exactly as in HyperFrames
  await page.evaluate((tt) => { const tl = window.__gmTimeline; if (tl) tl.seek(tt, false); }, t);
  last = await page.screenshot({ type: "png", optimizeForSpeed: true, captureBeyondViewport: false });
  shots++;
  await write(last);
}
ff.stdin.end();
await ffDone;
await browser.close();
server.close();
const tEnd = Date.now();
console.log(JSON.stringify({ out: args.out, frames: nFrames, screenshots: shots, timeline_s: tlDur,
  setup_ms: tSetup - t0, capture_encode_ms: tEnd - tSetup, total_ms: tEnd - t0 }));
