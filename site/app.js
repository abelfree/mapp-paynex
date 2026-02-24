const tasks = [
  { title: 'Web Visit 15s', reward: '$0.100', time: '23:31:17' },
  { title: 'Web Visit 30s', reward: '$0.100', time: '23:31:47' },
  { title: 'visit website 30 sec', reward: '$0.100', time: '23:31:42' },
  { title: 'Visit website 50 sec', reward: '$0.100', time: '23:33:17' },
  { title: 'Watch Short Video', reward: '$0.100', time: '23:34:04' },
  { title: 'Join telegram channel', reward: '$0.100', time: '23:35:02' },
  { title: 'Visit Website 1 Min', reward: '$0.150', time: '23:15:07' },
];

const taskList = document.getElementById('taskList');
const withdrawOverlay = document.getElementById('withdrawOverlay');
const warningOverlay = document.getElementById('warningOverlay');
const warningSeenKey = 'paynex_warning_seen_v2';
const accountKey = 'paynex_seen_accounts_v2';
const tg = window.Telegram?.WebApp;

function closeWarning() {
  warningOverlay.hidden = true;
  window.sessionStorage.setItem(warningSeenKey, '1');
}

function openWithdraw() {
  warningOverlay.hidden = true;
  withdrawOverlay.hidden = false;
}

for (const item of tasks) {
  const card = document.createElement('article');
  card.className = 'task';
  card.innerHTML = `
    <div>
      <p class="task-title">${item.title}</p>
      <p class="reward">Reward: ${item.reward}</p>
    </div>
    <div class="timer">${item.time}</div>
  `;
  taskList.appendChild(card);
}

document.getElementById('withdrawBtn').addEventListener('click', () => {
  openWithdraw();
});

document.getElementById('closeWithdraw').addEventListener('click', () => {
  withdrawOverlay.hidden = true;
});

document.getElementById('withdrawForm').addEventListener('submit', (event) => {
  event.preventDefault();
  withdrawOverlay.hidden = true;
  alert('Withdrawal request submitted.');
});

document.getElementById('dismissWarning').addEventListener('click', () => {
  closeWarning();
});

withdrawOverlay.addEventListener('click', (event) => {
  if (event.target === withdrawOverlay) {
    withdrawOverlay.hidden = true;
  }
});

if (window.sessionStorage.getItem(warningSeenKey) === '1') {
  warningOverlay.hidden = true;
}

function getTelegramId() {
  const fromTelegram = Number(tg?.initDataUnsafe?.user?.id || 0);
  if (fromTelegram > 0) return fromTelegram;
  const demo = Number(window.localStorage.getItem('demoTelegramId') || 1);
  return demo > 0 ? demo : 1;
}

function checkMultipleAccountsLocal() {
  const telegramId = getTelegramId();
  const raw = window.localStorage.getItem(accountKey);
  const seen = raw ? JSON.parse(raw) : [];
  if (!seen.includes(telegramId)) {
    seen.push(telegramId);
    window.localStorage.setItem(accountKey, JSON.stringify(seen));
  }
  const isMultiple = seen.length > 1;
  warningOverlay.hidden = !isMultiple;
}

checkMultipleAccountsLocal();
