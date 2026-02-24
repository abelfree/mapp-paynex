const params = new URLSearchParams(window.location.search);
const sessionId = params.get("sid");

const els = {
  adTitle: document.getElementById("adTitle"),
  adReward: document.getElementById("adReward"),
  adStatus: document.getElementById("adStatus"),
  showAdBtn: document.getElementById("showAdBtn"),
  checkStatusBtn: document.getElementById("checkStatusBtn"),
  simulateBtn: document.getElementById("simulateBtn"),
};

let session = null;
let monetagLoaded = false;

async function api(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.detail || "Request failed");
  }

  return res.json();
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.onload = () => resolve(true);
    script.onerror = () => reject(new Error("Failed to load provider script"));
    document.head.appendChild(script);
  });
}

async function loadSession() {
  if (!sessionId) {
    throw new Error("Missing session id");
  }

  session = await api(`/api/ad/sessions/${sessionId}`);
  els.adTitle.textContent = session.task.title;
  els.adReward.textContent = `Reward: $${Number(session.task.reward).toFixed(3)}`;

  if (session.credited) {
    els.adStatus.textContent = "Reward already credited. You can go back.";
    els.showAdBtn.disabled = true;
    return;
  }

  if (session.allow_simulate) {
    els.simulateBtn.hidden = false;
  }

  if (!session.provider.enabled) {
    els.adStatus.textContent = "Provider is not configured yet. Ask admin to set Monetag env vars.";
    els.showAdBtn.disabled = true;
    return;
  }

  await loadScript(session.provider.sdk_src);
  monetagLoaded = true;
  els.showAdBtn.disabled = false;
  els.adStatus.textContent = "Ad provider loaded. Click Show Ad.";
}

async function showAd() {
  if (!session || !monetagLoaded) return;

  const fnName = session.provider.show_fn;
  const runner = window[fnName];
  if (typeof runner !== "function") {
    els.adStatus.textContent = `Provider function not found: ${fnName}`;
    return;
  }

  try {
    els.showAdBtn.disabled = true;
    els.adStatus.textContent = "Opening ad...";

    await runner({
      ymid: session.provider.ymid,
      requestVar: session.provider.request_var,
    });

    await api(`/api/ad/sessions/${sessionId}/client-done`, { method: "POST" });
    els.adStatus.textContent = "Ad closed. Waiting for provider postback verification.";
  } catch (error) {
    els.adStatus.textContent = `Ad failed: ${error.message}`;
  } finally {
    els.showAdBtn.disabled = false;
  }
}

async function checkStatus() {
  if (!sessionId) return;
  try {
    const status = await api(`/api/ad/sessions/${sessionId}/status`);
    if (status.credited) {
      els.adStatus.textContent = `Credited. New balance: $${Number(status.balance).toFixed(3)}`;
    } else {
      els.adStatus.textContent = "Not credited yet. Wait for postback then check again.";
    }
  } catch (error) {
    els.adStatus.textContent = error.message;
  }
}

async function simulateValued() {
  try {
    await api(`/api/ad/sessions/${sessionId}/simulate-valued`, { method: "POST" });
    await checkStatus();
  } catch (error) {
    els.adStatus.textContent = error.message;
  }
}

els.showAdBtn.addEventListener("click", showAd);
els.checkStatusBtn.addEventListener("click", checkStatus);
els.simulateBtn.addEventListener("click", simulateValued);

loadSession().catch((error) => {
  els.adStatus.textContent = error.message;
});
