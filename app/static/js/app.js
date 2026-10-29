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

  function updateInstallUrls() {
    const epUrlInput = document.getElementById('epUrl');
    const epPassInput = document.getElementById('epPass');
    const tzSelect = document.getElementById('tz');
    const clientSelect = document.getElementById('clientTarget');

    const epUrl = (epUrlInput ? epUrlInput.value : '').trim();
    const epPass = (epPassInput ? epPassInput.value : '').trim();
    const tz = (tzSelect ? tzSelect.value : 'Europe/Rome').trim() || 'Europe/Rome';
    const client = (clientSelect ? clientSelect.value : 'stremio').trim().toLowerCase() || 'stremio';

    if (!epUrl) {
      return false;
    }

    const rawConfig = client === 'nuvio'
      ? `${epUrl}|${epPass}|${tz}|nuvio`
      : `${epUrl}|${epPass}|${tz}`;

    const encodedConfig = btoa(unescape(encodeURIComponent(rawConfig)));
    const baseUrl = window.location.origin;
    
    // Explicit visible URL paths: /nuvio/manifest.json vs /manifest.json
    const installUrl = client === 'nuvio'
      ? `${baseUrl}/${encodedConfig}/nuvio/manifest.json`
      : `${baseUrl}/${encodedConfig}/manifest.json`;

    const stremioUrl = installUrl.replace(/^https?:\/\//, 'stremio://');

    const installBlock = document.getElementById('installBlock');
    const installTitle = document.getElementById('installTitle');
    const installDesc = document.getElementById('installDesc');
    const stremioBtn = document.getElementById('stremioBtn');
    const installUrlInput = document.getElementById('installUrlInput');
    const statusBadge = document.querySelector('.status');

    if (installBlock && stremioBtn && installUrlInput) {
      if (client === 'nuvio') {
        if (installTitle) installTitle.textContent = 'Installazione su Nuvio (55 Cataloghi Piani)';
        if (installDesc) installDesc.textContent = "Copia questo URL manifesto (/nuvio/manifest.json) e incollalo nella gestione Addon di Nuvio per abilitare i 55 cataloghi indipendenti per le Collections:";
        stremioBtn.textContent = '🚀 Apri Manifesto Nuvio';
        stremioBtn.href = installUrl;
        stremioBtn.target = '_blank';
      } else {
        if (installTitle) installTitle.textContent = 'Installazione su Stremio';
        if (installDesc) installDesc.textContent = "Clicca sul pulsante sottostante o copia l'URL nel campo di ricerca addon di Stremio:";
        stremioBtn.textContent = '🚀 Installa su Stremio';
        stremioBtn.href = stremioUrl;
        stremioBtn.removeAttribute('target');
      }
      installUrlInput.value = installUrl;
      installBlock.style.display = 'block';
    }

    if (statusBadge) {
      statusBadge.className = 'status ok';
      statusBadge.innerHTML = `<span class="dot"></span> Profilo ${client === 'nuvio' ? 'Nuvio (55 Cataloghi)' : 'Stremio (Standard)'} attivo`;
    }

    return true;
  }

  // Update on client target change immediately
  const clientSelect = document.getElementById('clientTarget');
  if (clientSelect) {
    clientSelect.addEventListener('change', () => {
      const epUrlInput = document.getElementById('epUrl');
      if (epUrlInput && epUrlInput.value.trim()) {
        updateInstallUrls();
      }
    });
  }

  // Form submission: client-side Base64 generation without exposing passwords in GET query params
  const cfgForm = document.getElementById('cfg');
  if (cfgForm) {
    cfgForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const epUrlInput = document.getElementById('epUrl');
      if (!epUrlInput || !epUrlInput.value.trim()) {
        if (epUrlInput) epUrlInput.focus();
        return;
      }
      const ok = updateInstallUrls();
      if (ok) {
        const installBlock = document.getElementById('installBlock');
        if (installBlock) {
          installBlock.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
      }
    });
  }
});
