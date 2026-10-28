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
        const text = targetEl.value !== undefined ? targetEl.value : (targetEl.innerText || targetEl.textContent);
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

  // Form submission: client-side Base64 generation without exposing passwords in GET query params
  const cfgForm = document.getElementById('cfg');
  if (cfgForm) {
    cfgForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const epUrlInput = document.getElementById('epUrl');
      const epPassInput = document.getElementById('epPass');
      const tzSelect = document.getElementById('tz');

      const epUrl = (epUrlInput ? epUrlInput.value : '').trim();
      const epPass = (epPassInput ? epPassInput.value : '').trim();
      const tz = (tzSelect ? tzSelect.value : 'Europe/Rome').trim() || 'Europe/Rome';

      if (!epUrl) {
        if (epUrlInput) epUrlInput.focus();
        return;
      }

      const rawConfig = `${epUrl}|${epPass}|${tz}`;
      const encodedConfig = btoa(unescape(encodeURIComponent(rawConfig)));
      const baseUrl = window.location.origin;
      const installUrl = `${baseUrl}/${encodedConfig}/manifest.json`;
      const stremioUrl = installUrl.replace(/^https?:\/\//, 'stremio://');

      const installBlock = document.getElementById('installBlock');
      const stremioBtn = document.getElementById('stremioBtn');
      const installUrlInput = document.getElementById('installUrlInput');
      const statusBadge = document.querySelector('.status');

      if (installBlock && stremioBtn && installUrlInput) {
        stremioBtn.href = stremioUrl;
        installUrlInput.value = installUrl;
        installBlock.style.display = 'block';
        installBlock.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }

      if (statusBadge) {
        statusBadge.className = 'status ok';
        statusBadge.innerHTML = '<span class="dot"></span> Playback proxy collegato con successo';
      }
    });
  }
});
