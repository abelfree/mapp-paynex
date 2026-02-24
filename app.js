const state = {
  me: null,
  tasks: [],
  refreshHandle: null,
};

const tg = window.Telegram?.WebApp;
const deviceStorageKey = "paynex_device_id";

const els = {
  welcomeSub: document.getElementById("welcomeSub"),
  balanceText: document.getElementById("balanceText"),
  adsWatched: document.getElementById("adsWatched"),
  todayAds: document.getElementById("todayAds"),
  referrals: document.getElementById("referrals"),
  taskList: document.getElementById("taskList"),
  withdrawBtn: document.getElementById("withdrawBtn"),
  withdrawOverlay: document.getElementById("withdrawOverlay"),
  closeWithdraw: document.getElementById("closeWithdraw"),
  withdrawForm: document.getElementById("withdrawForm"),
  warningOverlay: document.getElementById("warningOverlay"),
  dismissWarning: document.getElementById("dismissWarning"),
};

function usd(value) {
  return `$${Number(value).toFixed(3)}`;
}

function getDeviceId() {
  let id = window.localStorage.getItem(deviceStorageKey);
  if (id) return id;
  id = `dev_${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
  window.localStorage.setItem(deviceStorageKey, id);
  return id;
}

function getTelegramId() {
  const fromTelegram = Number(tg?.initDataUnsafe?.user?.id || 0);
  if (fromTelegram > 0) return fromTelegram;
  return 1;
}

function toClock(seconds) {
  const h = Math.floor(seconds / 3600)
    .toString()
    .padStart(2, "0");
  const m = Math.floor((seconds % 3600) / 60)
    .toString()
    .padStart(2, "0");
  const s = Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0");
  return `${h}:${m}:${s}`;
}

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

function renderMe() {
  if (!state.me) return;
  els.welcomeSub.textContent = state.me.username;
  els.balanceText.textContent = usd(state.me.balance);
  els.adsWatched.textContent = `${state.me.ads_watched}`;
  els.todayAds.textContent = `${state.me.daily_ads} / ${state.me.daily_limit}`;
  els.referrals.textContent = `${state.me.referrals}`;
}

function taskItem(task) {
  const ready = task.remaining_seconds === 0;
  const inProgress = Boolean(task.active_session_id);
  const buttonText = inProgress ? "Continue" : ready ? "Start" : "Wait";
  const disabled = !ready && !inProgress;

  return `
    <article class="task" data-id="${task.id}">
      <div>
        <p class="task-title">${task.title}</p>
        <p class="reward">Reward: ${usd(task.reward)}</p>
      </div>
      <div class="task-right">
        <div class="timer">${inProgress ? "In Progress" : ready ? "Ready" : toClock(task.remaining_seconds)}</div>
        <button class="claim-btn" data-start ${disabled ? "disabled" : ""}>${buttonText}</button>
      </div>
    </article>
  `;
}

function renderTasks() {
  els.taskList.innerHTML = state.tasks.map(taskItem).join("");
}

function stepCooldowns() {
  let changed = false;
  for (const task of state.tasks) {
    if (task.remaining_seconds > 0 && !task.active_session_id) {
      task.remaining_seconds -= 1;
      changed = true;
    }
  }
  if (changed) renderTasks();
}

async function refreshAll() {
  const [me, tasks] = await Promise.all([api("/api/me"), api("/api/tasks")]);
  state.me = me;
  state.tasks = tasks;
  renderMe();
  renderTasks();
}

async function checkMultipleAccounts() {
  try {
    const payload = {
      telegram_id: getTelegramId(),
      device_id: getDeviceId(),
    };
    const res = await api("/api/account/check", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    els.warningOverlay.hidden = !res.multiple_accounts;
  } catch {
    els.warningOverlay.hidden = true;
  }
}

async function startTask(taskId) {
  try {
    const data = await api(`/api/tasks/${taskId}/start`, { method: "POST" });
    window.location.assign(data.ad_url);
  } catch (error) {
    alert(error.message);
  }
}

els.taskList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-start]");
  if (!button) return;

  const card = button.closest("[data-id]");
  if (!card) return;
  const taskId = Number(card.dataset.id);
  if (Number.isNaN(taskId)) return;
  startTask(taskId);
});

els.withdrawBtn.addEventListener("click", () => {
  els.warningOverlay.hidden = true;
  els.withdrawOverlay.hidden = false;
});

els.closeWithdraw.addEventListener("click", () => {
  els.withdrawOverlay.hidden = true;
});

els.withdrawOverlay.addEventListener("click", (event) => {
  if (event.target === els.withdrawOverlay) {
    els.withdrawOverlay.hidden = true;
  }
});

els.withdrawForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(els.withdrawForm);
  const payload = {
    method: formData.get("method"),
    account: formData.get("account"),
    amount: Number(formData.get("amount")),
  };

  try {
    const data = await api("/api/withdraw", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.me.balance = data.balance;
    renderMe();
    els.withdrawOverlay.hidden = true;
    els.withdrawForm.reset();
    alert(data.message);
  } catch (error) {
    alert(error.message);
  }
});

els.dismissWarning.addEventListener("click", () => {
  els.warningOverlay.hidden = true;
});

refreshAll().catch((error) => {
  alert(`Failed to load app: ${error.message}`);
});
checkMultipleAccounts();

state.refreshHandle = setInterval(stepCooldowns, 1000);
