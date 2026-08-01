(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let latestEnvelope = null;
  let statusPayload = null;
  let eventPayload = {events: [], cumulative_reward: 0};
  let polling = false;

  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  const fmt = (value, digits = 4) => {
    const n = Number(value);
    return Number.isFinite(n) ? n.toFixed(digits).replace(/\.?0+$/, "") : "—";
  };
  const snapshot = () => latestEnvelope?.snapshot || {};
  const runId = () => snapshot().run_id || statusPayload?.latest?.run_id || "";

  function toast(message, error = false) {
    const node = $("toast");
    node.textContent = message;
    node.className = `visible${error ? " error" : ""}`;
    window.setTimeout(() => { node.className = ""; }, 2800);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      cache: "no-store",
      headers: {"Content-Type": "application/json"},
      ...options,
    });
    const data = await response.json();
    if (!response.ok || data?.ok === false) throw new Error(data?.error || `HTTP ${response.status}`);
    return data;
  }

  function renderHeartbeat() {
    const node = $("heartbeat");
    if (!latestEnvelope?.available) {
      node.className = "status error";
      node.textContent = `NO SNAPSHOT · ${latestEnvelope?.error || "waiting"}`;
      return;
    }
    const stale = latestEnvelope.stale;
    const control = snapshot().control || statusPayload?.control || {};
    const state = control.shutdown ? "SHUTTING DOWN" : (control.paused ? "PAUSED" : "RUNNING");
    node.className = `status ${stale ? "stale" : "live"}`;
    node.textContent = `${stale ? "STALE" : "LIVE"} · ${state} · run ${snapshot().run_id || "?"} · seq ${snapshot().seq ?? snapshot().horizon_step ?? "?"} · heartbeat ${fmt(latestEnvelope.age_s, 1)}s ago`;
  }

  function renderFrames() {
    const host = $("frames");
    host.replaceChildren();
    const info = snapshot().observation_explained?.frame;
    $("frame-error").textContent = info?.error ? `— ${info.error}` : "";
    const frame = info?.frame;
    if (!frame) return;
    let bytes;
    try {
      const binary = atob(frame.base64);
      bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    } catch (error) {
      $("frame-error").textContent = `— ${error.message}`;
      return;
    }
    const [height, width, planes] = [63, 84, 4];
    for (let plane = 0; plane < planes; plane++) {
      const wrap = document.createElement("div");
      wrap.className = "frame";
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d");
      ctx.imageSmoothingEnabled = false;
      const image = ctx.createImageData(width, height);
      for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
          const pixel = y * width + x;
          const source = frame.layout === "HWC" ? pixel * 4 + plane : plane * width * height + pixel;
          const value = bytes[source];
          const target = pixel * 4;
          image.data[target] = value;
          image.data[target + 1] = value;
          image.data[target + 2] = value;
          image.data[target + 3] = 255;
        }
      }
      ctx.putImageData(image, 0, 0);
      wrap.append(canvas, document.createTextNode(`${plane + 1} · ${frame.labels[plane]}`));
      host.append(wrap);
    }
  }

  function findMetric(obj, names) {
    if (!obj || typeof obj !== "object") return undefined;
    for (const name of names) if (obj[name] !== undefined) return obj[name];
    for (const value of Object.values(obj)) {
      if (value && typeof value === "object" && !Array.isArray(value)) {
        const hit = findMetric(value, names);
        if (hit !== undefined) return hit;
      }
    }
    return undefined;
  }

  function renderReward() {
    const snap = snapshot();
    const current = findMetric(snap, ["reward"]);
    const total = findMetric(snap, ["episode_return", "episode_reward", "total_reward", "reward_total"]);
    const framesLeft = Number(findMetric(snap, ["episode_reset_frames_left"]));
    const framesLeftText = Number.isFinite(framesLeft)
      ? `${Math.max(0, Math.round(framesLeft)).toLocaleString()} (${fmt(framesLeft / 60, 1)} emulated seconds)`
      : "—";
    $("reward-summary").innerHTML = [
      ["current", fmt(current)],
      ["episode total", fmt(total)],
      ["frames until reset", framesLeftText],
      ["event cumulative", fmt(eventPayload.cumulative_reward)],
    ].map(([name, value]) => `<span class="metric">${name}: <b>${esc(value)}</b></span>`).join("");
    const contempt = Number(findMetric(snap, ["softlock_contempt", "contempt", "softlock_score"]) || 0);
    $("contempt-bar").style.width = `${Math.max(0, Math.min(100, Math.abs(contempt) * 100))}%`;
    $("contempt-label").textContent = `contempt / softlock (separate, never in event stream): ${fmt(contempt)}`;
    const host = $("events");
    host.replaceChildren();
    for (const event of [...(eventPayload.events || [])].reverse()) {
      const node = document.createElement("div");
      node.className = "event";
      const time = event.time || event.timestamp || `seq ${event.seq ?? "?"}`;
      const name = event.name || event.event || event.type ||
        Object.keys(event.reward_breakdown || {}).join(", ") || "reward";
      node.innerHTML = `<span class="muted">${esc(time)}</span><span>${esc(name)}</span><span class="amount">${fmt(event.display_reward)}</span>`;
      host.append(node);
    }
  }

  function renderInfrastructure() {
    const status = statusPayload || {};
    const process = status.process || {};
    const control = status.control || {};
    const learner = status.learner || {};
    const ld = learner.data || {};
    const epoch = ld.epoch || {};
    const pitch = ld.pitch || {};
    const rows = [
      ["memlog process", process.running ? "running" : (process.owned ? `exited (${process.exit_code})` : "not owned")],
      ["owned PID", process.pid ?? "—"], ["owned run", process.run_id ?? "—"],
      ["control", status.control_error ? status.control_error : JSON.stringify(control)],
      ["heartbeat", latestEnvelope?.available ? `${fmt(latestEnvelope.age_s, 1)} s` : "missing"],
      ["snapshot state", latestEnvelope?.stale ? "STALE" : "live"],
      ["speed", snapshot().speed ?? control.speed_pct ?? "—"],
      ["policy / horizon", `${snapshot().policy_version ?? snapshot().action_presentation?.policy_version ?? "—"} / ${snapshot().horizon ?? snapshot().horizon_step ?? snapshot().n_steps ?? "—"}`],
      ["learner", learner.available ? learner.url : `unavailable: ${learner.error || "unknown"}`],
      ["queue / epoch", `${ld.queue_depth ?? "—"} / ${epoch.epoch ?? epoch.index ?? epoch.current ?? "—"}`],
      ["pitch", `${fmt(pitch.pitch_pct, 2)}% (${pitch.steps_pitched ?? "—"} steps)`],
      ["workers", ld.workers ? `${Object.keys(ld.workers).length}: ${Object.keys(ld.workers).join(", ")}` : "—"],
    ];
    $("infrastructure").innerHTML = rows.map(([key, value]) =>
      `<span class="key">${esc(key)}</span><span>${esc(value)}</span>`).join("");
  }

  function renderActions() {
    const presentation = snapshot().action_presentation || {};
    const metrics = [
      ["chosen", `${presentation.chosen_index ?? "—"} ${presentation.chosen_name || ""}`],
      ["value", presentation.value], ["logprob", presentation.logprob],
      ["entropy", presentation.entropy], ["chosen rank", presentation.chosen_rank],
      ["policy", presentation.policy_version],
    ];
    $("action-metrics").innerHTML = metrics.map(([name, value]) =>
      `<span class="metric">${esc(name)}: <b>${esc(
        typeof value === "number" ? fmt(value) : (value ?? "—")
      )}</b></span>`).join("");
    const rows = [...(presentation.rows || [])];
    if ($("action-sort").value === "score") {
      rows.sort((a, b) => (Number(b.raw_logit) || -Infinity) - (Number(a.raw_logit) || -Infinity));
    } else rows.sort((a, b) => a.index - b.index);
    $("actions").innerHTML = rows.map((row) => {
      const classes = `${row.masked ? "masked" : ""} ${row.chosen ? "chosen" : ""}`;
      return `<tr class="${classes}"><td>${row.index}</td><td>${esc(row.name)}</td><td>${fmt(row.raw_logit, 6)}</td><td>${fmt(row.probability, 7)}</td><td>${row.legal ? "legal" : "MASKED"}${row.chosen ? " · CHOSEN" : ""}</td></tr>`;
    }).join("");
  }

  function renderObservations() {
    const explained = snapshot().observation_explained || {};
    const host = $("observations");
    host.replaceChildren();
    for (const [key, section] of Object.entries(explained)) {
      const block = document.createElement("section");
      block.className = "obs-section";
      const rows = section.rows || [];
      const nonzero = rows.filter((row) => !row.zero).length;
      const heading = document.createElement("div");
      heading.className = "obs-heading";
      heading.innerHTML = `<strong>${esc(key)}</strong> · ${rows.length || section.kind} fields · ${nonzero} nonzero`;
      block.append(heading);
      if (section.note) {
        const note = document.createElement("div");
        note.className = "obs-note";
        note.textContent = section.note;
        block.append(note);
      }
      if (section.error) {
        const note = document.createElement("div");
        note.className = "obs-note";
        note.textContent = section.error;
        block.append(note);
      }
      if (rows.length) {
        const list = document.createElement("div");
        list.className = "obs-rows";
        list.innerHTML = rows.map((row) =>
          `<div class="obs-row${row.padding ? " padding" : ""}"><span class="index">[${row.index}]</span><span>${esc(row.name)}</span><span title="raw: ${esc(row.raw)}">${esc(row.display)}</span><span class="meaning">${esc(row.meaning)}</span></div>`
        ).join("");
        block.append(list);
      }
      host.append(block);
    }
  }

  function render() {
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    renderHeartbeat();
    renderFrames();
    renderReward();
    renderInfrastructure();
    renderActions();
    renderObservations();
    window.requestAnimationFrame(() => window.scrollTo(scrollX, scrollY));
  }

  async function poll() {
    if (polling) return;
    polling = true;
    try {
      const [latest, status, events] = await Promise.all([
        api("/api/latest"), api("/api/status"), api("/api/events?limit=200"),
      ]);
      latestEnvelope = latest;
      statusPayload = status;
      eventPayload = events;
      render();
    } catch (error) {
      $("heartbeat").className = "status error";
      $("heartbeat").textContent = `DASHBOARD ERROR · ${error.message}`;
    } finally {
      polling = false;
    }
  }

  async function post(path, body = {}) {
    try {
      const result = await api(path, {method: "POST", body: JSON.stringify(body)});
      toast("Command accepted");
      await poll();
      return result;
    } catch (error) {
      toast(error.message, true);
    }
  }

  [100, 400, 1600, 6400].forEach((speed) => {
    const button = document.createElement("button");
    button.textContent = `${speed}%`;
    button.addEventListener("click", () => post("/api/control/speed", {run_id: runId(), speed_pct: speed}));
    document.querySelector(".speed-presets").append(button);
  });
  $("start").addEventListener("click", () => post("/api/lifecycle/start"));
  $("stop").addEventListener("click", () => post("/api/lifecycle/stop"));
  $("pause").addEventListener("click", () => post("/api/control/pause", {run_id: runId()}));
  $("resume").addEventListener("click", () => post("/api/control/resume", {run_id: runId()}));
  $("set-speed").addEventListener("click", () => post("/api/control/speed", {
    run_id: runId(), speed_pct: Number($("speed").value),
  }));
  $("action-sort").addEventListener("change", renderActions);
  poll();
  window.setInterval(poll, 1000);
})();
