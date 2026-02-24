const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
}

const refs = {
  list: document.getElementById("taskList"),
  balance: document.getElementById("balance"),
  adsWatched: document.getElementById("adsWatched"),
  todayAds: document.getElementById("todayAds"),
  referrals: document.getElementById("referrals"),
  username: document.getElementById("username"),
  withdrawOverlay: document.getElementById("withdrawOverlay"),
  withdrawBtn: document.getElementById("withdrawBtn"),
  closeWithdraw: document.getElementById("closeWithdraw"),
  withdrawForm: document.getElementById("withdrawForm"),
};

const state = {
  telegramId: 0,
  username: "user",
  deviceId: "",
  multipleAccounts: false,
  monetag: null,
  tasks: [],
};

function getDeviceId() {
  const key = "paynex_device_id_v1";
  let v = window.localStorage.getItem(key);
  if (v) return v;
  v = `dev_${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
  window.localStorage.setItem(key, v);
  return v;
}

function getTelegramUser() {
  const id = Number(tg?.initDataUnsafe?.user?.id || 0);
  const first = tg?.initDataUnsafe?.user?.first_name || "abel";
  return { id: id > 0 ? id : 1, name: first };
}

function clock(totalSeconds) {
  const s = Math.max(0, totalSeconds);
  const hh = String(Math.floor(s / 3600)).padStart(2, "0");
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new Error(e.detail || "Request failed");
  }
  return res.json();
}

function renderHeader(payload) {
  refs.username.textContent = payload.username;
  refs.balance.textContent = `$${Number(payload.balance).toFixed(3)}`;
  refs.adsWatched.textContent = String(payload.ads_watched);
  refs.todayAds.textContent = `${payload.daily_ads} / ${payload.daily_limit}`;
  refs.referrals.textContent = String(payload.referrals || 0);
}

function renderTasks() {
  refs.list.innerHTML = "";
  state.tasks.forEach((task) => {
    const row = document.createElement("article");
    row.className = "task";
    row.innerHTML = `
      <div>
        <p class="title">${task.title}</p>
        <p class="reward">Reward: $${Number(task.reward).toFixed(3)}</p>
      </div>
      <button class="timer" data-task-id="${task.id}">${clock(task.remaining_seconds)}</button>
    `;
    refs.list.appendChild(row);
  });
}

function tick() {
  state.tasks.forEach((t) => {
    if (t.remaining_seconds > 0) t.remaining_seconds -= 1;
  });
  document.querySelectorAll(".timer").forEach((btn) => {
    const id = Number(btn.dataset.taskId);
    const t = state.tasks.find((x) => x.id === id);
    if (t) btn.textContent = clock(t.remaining_seconds);
  });
}

async function loadState() {
  const payload = {
    telegram_id: state.telegramId,
    username: state.username,
    device_id: state.deviceId,
  };
  const data = await api("/api/state", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.multipleAccounts = Boolean(data.multiple_accounts);
  state.tasks = data.tasks || [];
  state.monetag = data.monetag || null;
  renderHeader(data);
  renderTasks();
}

async function doMonetagTask(taskId) {
  const start = await api("/api/ads/start", {
    method: "POST",
    body: JSON.stringify({ telegram_id: state.telegramId, task_id: taskId }),
  });

  if (!start.sdk_src || !start.show_fn) {
    if (start.allow_simulate) {
      await api(`/api/ads/simulate/${start.session_id}`, { method: "POST" });
      await loadState();
      return;
    }
    throw new Error("Monetag is not configured");
  }

  await new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = start.sdk_src;
    script.async = true;
    script.onload = () => resolve(true);
    script.onerror = () => reject(new Error("Monetag SDK failed to load"));
    document.head.appendChild(script);
  });

  const fn = window[start.show_fn];
  if (typeof fn !== "function") {
    throw new Error(`Monetag function not found: ${start.show_fn}`);
  }

  await fn({ ymid: start.ymid, requestVar: `task_${taskId}` });

  if (start.allow_simulate) {
    await api(`/api/ads/simulate/${start.session_id}`, { method: "POST" });
  }

  let tries = 0;
  while (tries < 20) {
    tries += 1;
    const status = await api(`/api/ads/status/${start.session_id}`);
    if (status.credited) {
      await loadState();
      return;
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
}

refs.list.addEventListener("click", async (event) => {
  const timer = event.target.closest(".timer");
  if (!timer) return;

  if (state.multipleAccounts) {
    alert("Multiple accounts are not allowed. Please use your original account.");
    return;
  }

  const taskId = Number(timer.dataset.taskId);
  const task = state.tasks.find((t) => t.id === taskId);
  if (!task) return;
  if (task.remaining_seconds > 0) {
    alert("Task is cooling down.");
    return;
  }

  try {
    await doMonetagTask(taskId);
  } catch (e) {
    alert(e.message || "Task failed");
  }
});

refs.withdrawBtn.addEventListener("click", () => {
  if (state.multipleAccounts) {
    alert("Multiple accounts are not allowed. Please use your original account.");
    refs.withdrawOverlay.hidden = true;
    return;
  }
  refs.withdrawOverlay.hidden = false;
});

refs.closeWithdraw.addEventListener("click", () => {
  refs.withdrawOverlay.hidden = true;
});

refs.withdrawOverlay.addEventListener("click", (event) => {
  if (event.target === refs.withdrawOverlay) refs.withdrawOverlay.hidden = true;
});

refs.withdrawForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(refs.withdrawForm);
  const payload = {
    telegram_id: state.telegramId,
    method: formData.get("method") || "",
    account: formData.get("account") || "",
    amount: Number(formData.get("amount") || 0),
  };

  try {
    const out = await api("/api/withdraw", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    refs.balance.textContent = `$${Number(out.balance).toFixed(3)}`;
    refs.withdrawOverlay.hidden = true;
    refs.withdrawForm.reset();
    alert(out.message);
  } catch (e) {
    alert(e.message || "Withdraw failed");
  }
});

async function boot() {
  const u = getTelegramUser();
  state.telegramId = u.id;
  state.username = u.name;
  state.deviceId = getDeviceId();
  await loadState();
  setInterval(tick, 1000);
}

boot().catch((e) => alert(e.message || "Failed to load app"));
