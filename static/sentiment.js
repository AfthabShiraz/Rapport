/*
 * Rapport — real-time sentiment overlay.
 *
 * Three off-the-shelf, in-browser signals, all stamped against the SAME call
 * clock the transcript uses (window.callElapsed -> tSec), so they merge for free:
 *
 *   face  — face-api.js (TinyFaceDetector + faceExpressionNet), ~2.5 Hz
 *   voice — Transformers.js wav2vec2 speech-emotion, every ~3 s of mic audio
 *   text  — Transformers.js DistilBERT-SST2 on each completed utterance
 *
 * Each sample collapses to a valence in [-1, +1]. Samples accrue in `track`
 * and are handed to the transcript on save (window.RapportSentiment.getTrack()).
 * Nothing here touches the WebRTC/Azure call — it only reads the mic + webcam.
 */

// Transformers.js is imported *dynamically* below (not as a top-level static
// import) so that a CDN hiccup loading it can't take down the whole module —
// face-api would die with it and window.RapportSentiment would never be defined.
const TRANSFORMERS_URL = "https://cdn.jsdelivr.net/npm/@xenova/transformers@2.17.2";
let pipeline = null;   // set once Transformers.js loads

// --- config (swap model ids here if one 404s on the hub) ----------------- //
const FACE_MODEL_URL = "https://cdn.jsdelivr.net/npm/@vladmandic/face-api/model";
// Speech-emotion model that actually exists as ONNX for Transformers.js and ships
// model_quantized.onnx (the file v2 loads by default). Labels: SAD/ANGRY/DISGUST/
// FEAR/HAPPY/NEUTRAL. (The previous Xenova/wav2vec2-base-superb-er id did NOT exist
// on the hub, which is why voice produced 0 samples.)
const VOICE_MODEL = "onnx-community/wav2vec2-base-Speech_Emotion_Recognition-ONNX";
// NOTE: live text sentiment is intentionally OFF — language/content sentiment is
// now done post-call by the Azure LLM (more accurate; gets sarcasm/frustration).

const FACE_INTERVAL_MS = 400;   // webcam sampling cadence
const VOICE_WINDOW_S = 3;       // seconds of audio per voice inference
const TARGET_RATE = 16000;      // wav2vec2 expects 16 kHz mono

// --- state --------------------------------------------------------------- //
let track = [];                                  // [{t, kind, role?, valence, scores}]
let latest = { face: null, voice: null, text: null };
let series = { face: [], voice: [], text: [], fused: [] }; // for the timeline
let faceReady = false, voiceReady = false, textReady = false;
let voicePipe = null, textPipe = null;
let running = false, busyVoice = false;
let video = null, faceLoop = null, videoStream = null;
let audioCtx = null, proc = null, voiceTimer = null;
let chunks = [], chunkSamples = 0;

const els = {};
const clamp = (x) => Math.max(-1, Math.min(1, x));
const now = () => (window.callElapsed ? window.callElapsed() : 0);
const stamp = () => Number(now().toFixed(2));

// --- valence mappings ---------------------------------------------------- //
function faceValence(e) {
  return clamp((e.happy || 0) + 0.5 * (e.surprised || 0)
    - (e.angry || 0) - (e.sad || 0) - (e.disgusted || 0) - 0.5 * (e.fearful || 0));
}
function labelValence(scores) { // robust to label spelling across models
  let pos = 0, neg = 0;
  for (const [l, s] of Object.entries(scores)) {
    const k = l.toLowerCase();
    if (/(hap|joy|pos|surpr|excit)/.test(k)) pos += s;
    else if (/(ang|sad|fear|disg|neg|frust)/.test(k)) neg += s;
  }
  return clamp(pos - neg);
}
function fused() {
  // Live overall = face + voice (language sentiment is computed post-call).
  const parts = [];
  if (latest.face) parts.push([0.5, latest.face.valence]);
  if (latest.voice) parts.push([0.5, latest.voice.valence]);
  if (!parts.length) return null;
  const w = parts.reduce((a, [k]) => a + k, 0);
  return clamp(parts.reduce((a, [k, v]) => a + k * v, 0) / w);
}

function record(kind, valence, scores, role) {
  const t = stamp();
  track.push({ t, kind, role, valence: Number(valence.toFixed(3)), scores });
  series[kind].push({ t, v: valence });
  const f = fused();
  if (f !== null) series.fused.push({ t, v: f });
  for (const s of Object.values(series)) if (s.length > 1200) s.shift();
  renderLive();
}

// --- model loading (on page load; the call can start before they finish) - //
async function loadModels() {
  setStatus("Loading sentiment models… (first load downloads weights)");

  // 1) Face — uses the face-api.js global (loaded via its own <script>).
  try {
    const fa = window.faceapi;
    if (!fa) throw new Error("face-api.js global not present (CDN script blocked?)");
    await fa.nets.tinyFaceDetector.loadFromUri(FACE_MODEL_URL);
    await fa.nets.faceExpressionNet.loadFromUri(FACE_MODEL_URL);
    faceReady = true;
  } catch (e) { console.warn("face model failed", e); setStatus("Face model failed: " + e.message); }

  // 2) Transformers.js — dynamic import, isolated so its failure can't kill face.
  try {
    const mod = await import(TRANSFORMERS_URL);
    pipeline = mod.pipeline;
    mod.env.allowLocalModels = false;          // pull weights from the HF hub, not disk
  } catch (e) {
    console.warn("Transformers.js failed to load", e);
    setStatus("Transformers.js failed to load (voice+text off): " + e.message);
  }

  if (pipeline) {
    try { voicePipe = await pipeline("audio-classification", VOICE_MODEL); voiceReady = true; }
    catch (e) { console.warn("voice model failed (continuing without it)", e); setStatus("Voice model failed: " + e.message); }
  }

  const on = [faceReady && "face", voiceReady && "voice"].filter(Boolean);
  if (on.length) setStatus(`Sentiment ready: ${on.join(" · ")}`);
  else setStatus("Sentiment models failed to load — see console for details.");
}

// --- face loop ----------------------------------------------------------- //
function startFaceLoop() {
  const fa = window.faceapi;
  const opts = new fa.TinyFaceDetectorOptions({ inputSize: 224, scoreThreshold: 0.4 });
  faceLoop = setInterval(async () => {
    if (!faceReady || !video || video.readyState < 2) return;
    let det;
    try { det = await fa.detectSingleFace(video, opts).withFaceExpressions(); }
    catch { return; }
    if (!det) return;
    const e = det.expressions;
    const scores = {}; for (const k of Object.keys(e)) scores[k] = Number(e[k].toFixed(3));
    const v = faceValence(e);
    latest.face = { valence: v, scores };
    record("face", v, scores);
  }, FACE_INTERVAL_MS);
}

// --- voice loop ---------------------------------------------------------- //
function startAudio(micStream) {
  audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: TARGET_RATE });
  const src = audioCtx.createMediaStreamSource(micStream);
  proc = audioCtx.createScriptProcessor(4096, 1, 1);
  const mute = audioCtx.createGain(); mute.gain.value = 0; // keep node alive, no echo
  src.connect(proc); proc.connect(mute); mute.connect(audioCtx.destination);
  proc.onaudioprocess = (ev) => {
    chunks.push(new Float32Array(ev.inputBuffer.getChannelData(0)));
    chunkSamples += chunks[chunks.length - 1].length;
    const max = audioCtx.sampleRate * (VOICE_WINDOW_S + 1);
    while (chunkSamples > max) { chunkSamples -= chunks[0].length; chunks.shift(); }
  };
  voiceTimer = setInterval(runVoice, VOICE_WINDOW_S * 1000);
}
function resampleTo16k(buf, rate) {
  if (rate === TARGET_RATE) return buf;
  const ratio = rate / TARGET_RATE, len = Math.floor(buf.length / ratio), out = new Float32Array(len);
  for (let i = 0; i < len; i++) out[i] = buf[Math.floor(i * ratio)];
  return out;
}
async function runVoice() {
  if (!voiceReady || busyVoice || !chunks.length) return;
  busyVoice = true;
  try {
    let total = 0; for (const c of chunks) total += c.length;
    const merged = new Float32Array(total); let o = 0;
    for (const c of chunks) { merged.set(c, o); o += c.length; }
    const data = resampleTo16k(merged, audioCtx.sampleRate);
    if (data.length < TARGET_RATE * 0.5) return; // need >= 0.5 s
    const out = await voicePipe(data);
    const scores = {}; (Array.isArray(out) ? out : [out]).forEach((o) => { scores[o.label] = o.score; });
    const v = labelValence(scores);
    latest.voice = { valence: v, scores };
    record("voice", v, scores);
  } catch (e) { /* skip this window */ }
  finally { busyVoice = false; }
}

// --- text (called per completed utterance from index.html) --------------- //
async function noteUtterance(role, text, _t) {
  if (!textReady || !text) return;
  let out;
  try { out = await textPipe(text); } catch { return; }
  const r = Array.isArray(out) ? out[0] : out;
  const pos = r.label.toUpperCase().startsWith("POS");
  const v = pos ? r.score : -r.score;
  // The customer's reaction is what drives the live/fused "text" signal.
  if (role === "user") latest.text = { valence: v, scores: { [r.label]: r.score } };
  record("text", v, { label: r.label, score: Number(r.score.toFixed(3)) }, role);
}

// --- rendering ----------------------------------------------------------- //
function setStatus(m) { if (els.status) els.status.textContent = m; }
function pct(v) { return Math.round(((v + 1) / 2) * 100); }          // -1..1 -> 0..100
function hue(v) { return v >= 0 ? 140 : 0; }                          // green vs red
function color(v) { return `hsl(${hue(v)} 70% 55%)`; }
function dominant(scores) {
  let best = "", bv = -Infinity;
  for (const [k, s] of Object.entries(scores)) if (s > bv) { bv = s; best = k; }
  return best;
}

function bar(label, valence, sub) {
  return `<div class="srow"><span>${label}</span><span>${sub ?? ""} ${valence === null ? "—" : valence.toFixed(2)}</span></div>
    <div class="sbar"><i style="width:${valence === null ? 50 : pct(valence)}%;background:${valence === null ? "#444" : color(valence)}"></i></div>`;
}

function renderLive() {
  const f = fused();
  if (els.fused) {
    els.fused.innerHTML = `<div class="big" style="color:${f === null ? "#888" : color(f)}">
      ${f === null ? "—" : (f >= 0 ? "+" : "") + f.toFixed(2)}</div>
      <div class="hint">overall customer sentiment</div>`;
  }
  if (els.readouts) {
    els.readouts.innerHTML =
      bar("Face", latest.face ? latest.face.valence : null, latest.face ? dominant(latest.face.scores) : "") +
      bar("Voice", latest.voice ? latest.voice.valence : null, latest.voice ? dominant(latest.voice.scores) : "") +
      `<div class="srow"><span>Text / language</span><span>computed post-call (LLM)</span></div>`;
  }
  drawTimeline();
}

function drawTimeline() {
  const cv = els.canvas; if (!cv) return;
  const ctx = cv.getContext("2d"), W = cv.width, H = cv.height;
  ctx.clearRect(0, 0, W, H);
  const allT = [...series.fused, ...series.face, ...series.voice, ...series.text].map((p) => p.t);
  const maxT = Math.max(30, ...(allT.length ? allT : [0]));
  const x = (t) => (t / maxT) * (W - 4) + 2;
  const y = (v) => H / 2 - v * (H / 2 - 6);
  // zero line
  ctx.strokeStyle = "#262b36"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, H / 2); ctx.lineTo(W, H / 2); ctx.stroke();
  const lines = [["text", "#4caf50", 1.5], ["face", "#e91e63", 1.5], ["voice", "#2196f3", 1.5], ["fused", "#e6e9ef", 2.5]];
  for (const [k, c, w] of lines) {
    const pts = series[k]; if (pts.length < 2) continue;
    ctx.strokeStyle = c; ctx.lineWidth = w; ctx.beginPath();
    pts.forEach((p, i) => { const px = x(p.t), py = y(p.v); i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); });
    ctx.stroke();
  }
}

// --- public API (called from index.html) --------------------------------- //
function reset() {
  track = []; latest = { face: null, voice: null, text: null };
  series = { face: [], voice: [], text: [], fused: [] };
  renderLive();
}

async function start(micStream) {
  if (running) return; running = true;
  try {
    videoStream = await navigator.mediaDevices.getUserMedia({ video: { width: 320, height: 240 } });
    video.srcObject = videoStream;
    await video.play().catch(() => {});
  } catch (e) { setStatus("Webcam blocked — running voice + text only."); }
  startFaceLoop();
  if (micStream) { try { startAudio(micStream); } catch (e) { console.warn("audio tap failed", e); } }
}

function stop() {
  running = false;
  if (faceLoop) { clearInterval(faceLoop); faceLoop = null; }
  if (voiceTimer) { clearInterval(voiceTimer); voiceTimer = null; }
  if (proc) { try { proc.disconnect(); } catch {} proc = null; }
  if (audioCtx) { try { audioCtx.close(); } catch {} audioCtx = null; }
  if (videoStream) { videoStream.getTracks().forEach((t) => t.stop()); videoStream = null; }
  chunks = []; chunkSamples = 0;
}

window.RapportSentiment = { start, stop, reset, noteUtterance, getTrack: () => track };

// --- boot ---------------------------------------------------------------- //
function boot() {
  els.status = document.getElementById("sentstatus");
  els.fused = document.getElementById("fused");
  els.readouts = document.getElementById("readouts");
  els.canvas = document.getElementById("senttimeline");
  video = document.getElementById("cam");
  renderLive();
  loadModels();   // loads face (global) + transformers (dynamic import) independently
}

// The DOM may already be parsed by the time this module evaluates, so don't
// rely solely on DOMContentLoaded (which may have already fired).
if (document.readyState === "loading") {
  window.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
