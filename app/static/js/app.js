document.addEventListener('DOMContentLoaded', () => {
  // Auto-select user's local timezone if not already set
  const tzSelect = document.getElementById('tz');
  if (tzSelect && !tzSelect.value) {
    try {
      const userTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (userTz) {
        let matched = false;
        for (let i = 0; i < tzSelect.options.length; i++) {
          if (tzSelect.options[i].value === userTz) {
            tzSelect.selectedIndex = i;
            matched = true;
            break;
          }
        }
        if (!matched) {
          const opt = document.createElement('option');
          opt.value = userTz;
          opt.textContent = `${userTz} (Local)`;
          opt.selected = true;
          tzSelect.appendChild(opt);
        }
      }
    } catch (e) {
      console.warn('Could not determine local timezone:', e);
    }
  }

  // Copy button logic
  const copyBtn = document.getElementById('copyBtn');
  if (copyBtn) {
    copyBtn.addEventListener('click', () => {
      const targetSelector = copyBtn.getAttribute('data-copy');
      const targetEl = document.querySelector(targetSelector);
      if (targetEl) {
        const text = targetEl.innerText || targetEl.textContent;
        navigator.clipboard.writeText(text).then(() => {
          const orig = copyBtn.textContent;
          copyBtn.textContent = 'Copiato ✓';
          setTimeout(() => {
            copyBtn.textContent = orig;
          }, 1800);
        }).catch(() => {
          alert('Impossibile copiare automaticamente. Seleziona e copia il link manualmente.');
        });
      }
    });
  }
});
