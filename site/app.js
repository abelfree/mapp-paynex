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
  withdrawOverlay.hidden = false;
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
  warningOverlay.hidden = true;
});

withdrawOverlay.addEventListener('click', (event) => {
  if (event.target === withdrawOverlay) {
    withdrawOverlay.hidden = true;
  }
});
