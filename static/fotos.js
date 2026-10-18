/* SP Leilões — seleção de fotos: pré-visualização, redução no aparelho (máx. 1600 px, JPEG 85%),
   remover / escolher capa / adicionar mais, envio com barra de progresso e mensagens claras. */
(function () {
  var SIDE = 1600, Q = 0.85, MAXB = 20 * 1024 * 1024;
  document.querySelectorAll('form[data-fotos]').forEach(init);

  function init(form) {
    var wrap = form.querySelector('.fotos'), input = wrap.querySelector('input[type=file]');
    var prev = wrap.querySelector('.fotos-prev'), msgEl = wrap.querySelector('.fotos-msg');
    var MAX = +wrap.dataset.max || 10, items = [], busy = 0, sending = false;
    var btn = form.querySelector('button[type=submit],button:not([type])');
    var newCover = form.querySelector('input[name=capa][value=n0]');
    wrap.classList.add('js');

    function existing() {
      var n = 0;
      form.querySelectorAll('.foto-atual').forEach(function (el) {
        var rm = el.querySelector('input[name=remover_foto]'); if (!rm || !rm.checked) n++;
      });
      return n;
    }
    function msg(t, kind) { msgEl.textContent = t || ''; msgEl.className = 'fotos-msg ' + (kind || 'err'); }
    function counter() {
      var c = wrap.querySelector('.fotos-count');
      if (c) c.textContent = (existing() + items.length) + ' de ' + MAX + ' fotos';
    }
    function render() {
      prev.innerHTML = '';
      items.forEach(function (it, i) {
        var d = document.createElement('div'); d.className = 'fp' + (it.err ? ' bad' : '');
        var isCover = i === 0 && (!newCover || newCover.checked || !form.querySelector('.foto-atual'));
        d.innerHTML = (it.url ? '<img alt="">' : '<div class="ph">' + (it.err ? '⚠️' : (it.busy ? '⏳' : '🖼️')) + '<small></small></div>') +
          (isCover ? '<span class="capa-tag">Capa</span>' : '') +
          '<div class="fp-bt">' + (!isCover && !it.err ? '<button type="button" class="mk">★ Capa</button>' : '') +
          '<button type="button" class="rm" aria-label="Remover">✕</button></div>';
        if (it.url) d.querySelector('img').src = it.url;
        else d.querySelector('small').textContent = it.err ? it.err : (it.busy ? 'preparando…' : it.note || it.file.name);
        d.querySelector('.rm').onclick = function () { if (it.url) URL.revokeObjectURL(it.url); items.splice(i, 1); render(); };
        var mk = d.querySelector('.mk');
        if (mk) mk.onclick = function () { items.splice(i, 1); items.unshift(it); if (newCover) newCover.checked = true; render(); };
        prev.appendChild(d);
      });
      counter();
      if (btn) btn.disabled = busy > 0 || sending;
    }

    function shrink(file) {
      return new Promise(function (ok) {
        var url = URL.createObjectURL(file), img = new Image();
        img.onload = function () {
          try {
            var w = img.naturalWidth, h = img.naturalHeight, s = Math.min(1, SIDE / Math.max(w, h));
            var c = document.createElement('canvas'); c.width = Math.max(1, Math.round(w * s)); c.height = Math.max(1, Math.round(h * s));
            var x = c.getContext('2d'); x.fillStyle = '#fff'; x.fillRect(0, 0, c.width, c.height); x.drawImage(img, 0, 0, c.width, c.height);
            c.toBlob(function (b) { URL.revokeObjectURL(url); ok(b && b.size ? b : null); }, 'image/jpeg', Q);
          } catch (e) { URL.revokeObjectURL(url); ok(null); }
        };
        img.onerror = function () { URL.revokeObjectURL(url); ok(null); };
        img.src = url;
      });
    }
    function readable(file) {
      return file.slice(0, 64).arrayBuffer().then(function (b) { return b.byteLength > 0; }, function () { return false; });
    }

    input.addEventListener('change', function () {
      var files = Array.prototype.slice.call(input.files); input.value = ''; msg('');
      var room = MAX - existing() - items.length;
      if (files.length > room) { msg('Máximo de ' + MAX + ' fotos por lote. ' + (room > 0 ? 'Foram adicionadas só ' + room + '.' : 'Remova alguma para adicionar outra.')); files = files.slice(0, Math.max(room, 0)); }
      files.forEach(function (file) {
        var it = { file: file, busy: true }; items.push(it); busy++;
        (async function () {
          var heic = /\.(heic|heif)$/i.test(file.name) || /hei[cf]/i.test(file.type);
          if (!file.size || !(await readable(file))) it.err = 'Não foi possível ler “' + file.name + '”. Se a foto estiver só na nuvem, baixe-a para o aparelho ou use a câmera.';
          else if (file.size > MAXB && !/^image\/(jpeg|png|webp)/.test(file.type)) it.err = '“' + file.name + '” tem mais de 20 MB.';
          else {
            var b = await shrink(file);
            if (b) { it.blob = b; it.url = URL.createObjectURL(b); }
            else if (file.size > MAXB) it.err = '“' + file.name + '” tem mais de 20 MB.';
            else it.note = heic ? 'HEIC — será convertida' : file.name;   // o servidor converte/valida
          }
          it.busy = false; busy--;
          if (it.err) msg(it.err);
          render();
        })();
      });
      render();
    });
    form.querySelectorAll('input[name=remover_foto]').forEach(function (el) { el.addEventListener('change', counter); });
    form.querySelectorAll('input[name=capa]').forEach(function (el) { el.addEventListener('change', render); });
    render();

    form.addEventListener('submit', function (e) {
      if (busy) { e.preventDefault(); msg('Aguarde: as fotos ainda estão sendo preparadas.', 'warn'); return; }
      if (items.some(function (it) { return it.err; })) { e.preventDefault(); msg('Remova as fotos marcadas com ⚠️ antes de salvar.'); return; }
      if (!window.FormData || !window.XMLHttpRequest) return;
      e.preventDefault();
      var fd = new FormData(form); fd.delete('fotos');
      items.forEach(function (it, i) { if (it.blob) fd.append('fotos', it.blob, 'foto' + (i + 1) + '.jpg'); else fd.append('fotos', it.file, it.file.name); });
      var bar = wrap.querySelector('.fotos-bar'); sending = true; render(); msg('Enviando…', 'ok');
      var x = new XMLHttpRequest(); x.open('POST', form.getAttribute('action') || location.href);
      x.setRequestHeader('X-Requested-With', 'fetch');
      x.upload.onprogress = function (ev) { if (ev.lengthComputable && bar) { bar.hidden = false; bar.value = ev.loaded / ev.total; msg('Enviando… ' + Math.round(100 * ev.loaded / ev.total) + '%', 'ok'); } };
      x.onload = function () {
        var r = null; try { r = JSON.parse(x.responseText); } catch (err) {}
        if (r && r.ok && r.redirect) { msg('Salvo! Abrindo o lote…', 'ok'); location.href = r.redirect; return; }
        sending = false; if (bar) bar.hidden = true; render();
        if (r && r.error) msg(r.error);
        else if (x.status === 413) msg('As fotos ultrapassaram o limite de envio. Envie menos fotos por vez.');
        else if (/\/entrar/.test(x.responseURL || '')) msg('Sua sessão de administrador expirou. Entre de novo em outra aba e depois clique em salvar outra vez.');
        else if (x.status === 400) msg('Sessão expirada. Recarregue a página e tente de novo.');
        else msg('Não foi possível salvar (erro ' + x.status + '). Tente de novo.');
        msgEl.scrollIntoView({ block: 'center' });
      };
      x.onerror = function () { sending = false; if (bar) bar.hidden = true; render(); msg('Falha de conexão durante o envio. Verifique a internet e tente de novo.'); };
      x.send(fd);
    });
  }
})();
