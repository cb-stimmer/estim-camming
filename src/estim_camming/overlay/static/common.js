// Shared WebSocket client for overlay.html and control.html.
// Every server message is {"event": {...} | null, "state": {...}}.
// Pages call connect(onMessage) and render from `state`; `event` is used for effects.

function connect(onMessage, onStatus) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  let delay = 500;
  function open() {
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => { delay = 500; onStatus && onStatus(true); };
    ws.onmessage = (msg) => {
      const data = JSON.parse(msg.data);
      onMessage(data.state, data.event);
    };
    ws.onclose = () => {
      onStatus && onStatus(false);
      setTimeout(open, delay);
      delay = Math.min(delay * 2, 5000);
    };
  }
  open();
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function tokenRange(item) {
  if (item.max_tokens === null) return `${item.min_tokens}+`;
  if (item.max_tokens === item.min_tokens) return `${item.min_tokens}`;
  return `${item.min_tokens}–${item.max_tokens}`;
}

function levelBars(state) {
  return state.channels.map((ch) => {
    const pct = Math.round((state.levels[ch] || 0) * 100);
    return `<div class="bar"><span class="ch">${escapeHtml(ch)}</span>` +
      `<span class="track"><span class="fill" style="width:${pct}%"></span></span>` +
      `<span class="pct">${pct}%</span></div>`;
  }).join("");
}
