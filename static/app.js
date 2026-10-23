document.addEventListener('DOMContentLoaded', () => {
  const menu = document.getElementById('menu-toggle');
  menu?.addEventListener('click', () => {
    const open = document.body.classList.toggle('nav-open');
    menu.setAttribute('aria-expanded', String(open));
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      document.body.classList.remove('nav-open');
      menu?.setAttribute('aria-expanded', 'false');
    }
  });
  document.querySelectorAll('button[data-busy-label]').forEach(button => {
    button.form?.addEventListener('submit', () => {
      button.disabled = true;
      button.textContent = button.dataset.busyLabel;
    });
  });
  const dataEl = document.getElementById('chart-data');
  const canvas = document.getElementById('demand-chart');
  if (dataEl && canvas && window.Chart) {
    const data = JSON.parse(dataEl.textContent);
    const context = canvas.getContext('2d');
    const fill = context.createLinearGradient(0, 0, 0, 320);
    fill.addColorStop(0, 'rgba(169, 208, 111, .28)');
    fill.addColorStop(1, 'rgba(169, 208, 111, 0)');
    const datasets = [{label: 'Actual demand', data: data.actual, borderColor: '#779b48', backgroundColor: fill, borderWidth: 2.5, fill: true, tension: .32, pointRadius: 0, pointHoverRadius: 5}];
    if (data.forecast) datasets.push({label: 'Forecast demand', data: data.forecast, borderColor: '#7773bd', borderWidth: 2.5, borderDash: [6, 5], pointRadius: 0, tension: .25});
    new Chart(canvas, {type: 'line', data: {labels: data.labels, datasets}, options: {responsive: true, maintainAspectRatio: false, animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches, interaction: {intersect: false, mode: 'index'}, plugins: {legend: {display: false}, tooltip: {backgroundColor: '#242b24', padding: 12, cornerRadius: 8}}, scales: {x: {grid: {display: false}, ticks: {maxTicksLimit: 9, color: '#92988e', font: {size: 11}}, border: {display: false}}, y: {beginAtZero: true, grid: {color: '#eff1ec'}, ticks: {maxTicksLimit: 5, color: '#92988e', font: {size: 11}}, border: {display: false}}}}});
  }
});
document.addEventListener('htmx:responseError', () => {
  const target = document.getElementById('inventory-results');
  if (target && !document.getElementById('refresh-error')) {
    const message = document.createElement('div');
    message.id = 'refresh-error'; message.className = 'notice error'; message.setAttribute('role', 'alert');
    message.textContent = 'Inventory could not refresh. Check your connection and reload the page.';
    target.prepend(message);
  }
});
