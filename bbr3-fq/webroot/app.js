/* BBR3 Control — module WebUI logic.
   Runs inside SukiSU Ultra's WebView; commands execute as root through
   window.ksu.exec(). No external dependencies. */

(function () {
  'use strict';

  const FALLBACK_MODULE_DIR = '/data/adb/modules/bbr3_fq';
  const SAFE_TOKEN = /^[a-z0-9_]+$/;
  const RECOMMENDED = { cc: 'bbr3', qdisc: 'fq' };

  const CC_META = {
    bbr3: { title: 'BBRv3', desc: '基于模型的拥塞控制，低排队延迟、抗随机丢包', recommend: true },
    bbr: { title: 'BBRv1', desc: '经典 BBR，行为稳定，可作为对照' },
    cubic: { title: 'CUBIC', desc: '内核默认算法，丢包驱动，兼容性最好' },
    reno: { title: 'Reno', desc: '传统 AIMD 算法，仅建议排障时使用' },
  };

  const QDISC_META = {
    fq: { title: 'FQ', desc: '公平队列 + pacing，BBR 的最佳搭档', recommend: true },
    fq_codel: { title: 'FQ-CoDel', desc: '公平队列 + CoDel AQM，抑制缓冲膨胀' },
    codel: { title: 'CoDel', desc: '主动队列管理，控制排队延迟' },
    sfq: { title: 'SFQ', desc: '随机公平队列，简单分流' },
    pfifo_fast: { title: 'pfifo_fast', desc: '内核旧默认，三优先级 FIFO' },
    pfifo: { title: 'pfifo', desc: '纯先进先出，无额外调度' },
  };

  const state = {
    data: null,
    pending: { cc: null, qdisc: null },
    dirty: false,
    applying: false,
    refreshing: false,
    preview: false,
    moduleDir: FALLBACK_MODULE_DIR,
    moduleInfoVersion: '',
    ccSig: null,
    qdiscSig: null,
    toastTimer: 0,
    flashTimer: 0,
    polling: 0,
  };

  const els = {};
  let callbackSeq = 0;

  function $(id) {
    return document.getElementById(id);
  }

  function hasBridge() {
    return !!(window.ksu && typeof window.ksu.exec === 'function');
  }

  function prefersReducedMotion() {
    try {
      return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (error) {
      return false;
    }
  }

  function cacheElements() {
    els.previewBanner = $('previewBanner');
    els.errorNotice = $('errorNotice');
    els.errorTitle = $('errorTitle');
    els.errorText = $('errorText');
    els.errorDetails = $('errorDetails');
    els.errorDetailText = $('errorDetailText');
    els.dismissErrorBtn = $('dismissErrorBtn');
    els.daemonPill = $('daemonPill');
    els.daemonPillText = $('daemonPillText');
    els.refreshBtn = $('refreshBtn');
    els.currentCC = $('currentCC');
    els.currentCCName = $('currentCCName');
    els.currentQD = $('currentQD');
    els.currentQDName = $('currentQDName');
    els.kernelVersion = $('kernelVersion');
    els.ifaceSummary = $('ifaceSummary');
    els.syncNotice = $('syncNotice');
    els.syncNoticeText = $('syncNoticeText');
    els.syncBtn = $('syncBtn');
    els.daemonNotice = $('daemonNotice');
    els.startDaemonBtn = $('startDaemonBtn');
    els.ccOptions = $('ccOptions');
    els.qdiscOptions = $('qdiscOptions');
    els.ccCount = $('ccCount');
    els.qdiscCount = $('qdiscCount');
    els.presetBtn = $('presetBtn');
    els.restartBtn = $('restartBtn');
    els.daemonStatIcon = $('daemonStatIcon');
    els.ifaceStatIcon = $('ifaceStatIcon');
    els.daemonState = $('daemonState');
    els.ifaceState = $('ifaceState');
    els.bootState = $('bootState');
    els.moduleVersion = $('moduleVersion');
    els.ifaceList = $('ifaceList');
    els.eventCount = $('eventCount');
    els.logTail = $('logTail');
    els.applyLabel = $('applyLabel');
    els.applySub = $('applySub');
    els.applyBtn = $('applyBtn');
    els.toast = $('toast');
  }

  function bindEvents() {
    els.refreshBtn.addEventListener('click', function () {
      refresh({ silent: false });
    });

    els.applyBtn.addEventListener('click', function () {
      apply();
    });

    els.presetBtn.addEventListener('click', function () {
      applyPreset();
    });

    els.syncBtn.addEventListener('click', function () {
      syncNow();
    });

    els.startDaemonBtn.addEventListener('click', function () {
      restartDaemon();
    });

    els.restartBtn.addEventListener('click', function () {
      restartDaemon();
    });

    els.dismissErrorBtn.addEventListener('click', function () {
      hideError();
    });

    els.ccOptions.addEventListener('change', function (event) {
      onOptionChange('cc', event);
    });

    els.qdiscOptions.addEventListener('change', function (event) {
      onOptionChange('qdisc', event);
    });

    document.addEventListener('visibilitychange', function () {
      if (!document.hidden && hasBridge() && !state.applying) {
        refresh({ silent: true });
      }
    });

    window.addEventListener('pagehide', stopPolling);
  }

  function loadModuleDir() {
    try {
      if (window.ksu && typeof window.ksu.moduleInfo === 'function') {
        const info = JSON.parse(window.ksu.moduleInfo());
        if (info && typeof info.moduleDir === 'string' && info.moduleDir.indexOf('/data/adb/modules/') === 0) {
          state.moduleDir = info.moduleDir;
        }
        if (info && info.version) {
          state.moduleInfoVersion = String(info.version);
        }
      }
    } catch (error) {
      /* Keep the fallback path. */
    }
  }

  function bridgeExec(cmd) {
    return new Promise(function (resolve) {
      if (!hasBridge()) {
        resolve({ code: -1, stdout: '', stderr: '未连接 SukiSU WebUI 桥接' });
        return;
      }
      const name = '__bbr3_cb_' + Date.now() + '_' + (callbackSeq++);
      let settled = false;

      function done(code, stdout, stderr) {
        if (settled) return;
        settled = true;
        try { delete window[name]; } catch (error) { /* ignore */ }
        resolve({
          code: Number(code) || 0,
          stdout: stdout || '',
          stderr: stderr || '',
        });
      }

      window[name] = done;
      try {
        window.ksu.exec(cmd, name);
      } catch (error) {
        done(-1, '', String(error && error.message ? error.message : error));
      }
      window.setTimeout(function () {
        done(-1, '', '执行超时，请重试');
      }, 15000);
    });
  }

  function shellQuote(value) {
    return "'" + String(value).replace(/'/g, "'\\''") + "'";
  }

  function runCLI(args) {
    const cmd = 'sh ' + shellQuote(state.moduleDir + '/bbr3-fq') + ' ' + args;
    return bridgeExec(cmd);
  }

  function parseState(output) {
    const start = output.indexOf('{');
    const end = output.lastIndexOf('}');
    if (start < 0 || end <= start) {
      throw new Error('模块没有返回状态 JSON');
    }
    const data = JSON.parse(output.slice(start, end + 1));
    if (!data || !data.cc || !data.qdisc ||
        !Array.isArray(data.cc.available) || !Array.isArray(data.qdisc.available)) {
      throw new Error('状态 JSON 结构不完整');
    }
    if (!Array.isArray(data.interfaces)) data.interfaces = [];
    if (!Array.isArray(data.events)) data.events = [];
    return data;
  }

  async function refresh(options) {
    const silent = !!(options && options.silent);
    if (state.refreshing) return;
    state.refreshing = true;
    if (!silent) els.refreshBtn.classList.add('is-spinning');

    try {
      const result = await runCLI('ui-state');
      if (result.code !== 0) {
        setFailurePill();
        if (!silent) {
          showError('读取状态失败', '无法从模块读取当前配置，请确认模块已启用。', result.stderr || result.stdout);
        }
        return;
      }

      let data;
      try {
        data = parseState(result.stdout);
      } catch (error) {
        setFailurePill();
        if (!silent) {
          showError('解析状态失败', '模块返回的数据不完整。', error.message + '\n' + result.stdout.slice(0, 600));
        }
        return;
      }

      state.data = data;
      if (!state.dirty) {
        state.pending.cc = data.cc.configured;
        state.pending.qdisc = data.qdisc.configured;
      }
      if (!data.cc.available.includes(state.pending.cc)) {
        state.pending.cc = data.cc.available[0] || null;
      }
      if (!data.qdisc.available.includes(state.pending.qdisc)) {
        state.pending.qdisc = data.qdisc.available[0] || null;
      }
      render();
    } finally {
      state.refreshing = false;
      if (!silent) els.refreshBtn.classList.remove('is-spinning');
    }
  }

  function setFailurePill() {
    els.daemonPill.classList.remove('is-loading');
    els.daemonPill.classList.add('is-off');
    els.daemonPillText.textContent = '读取失败';
  }

  function render() {
    const data = state.data;
    if (!data) return;

    updateDirty();
    renderStatusPill(data);
    renderCurrent(data);
    renderNotices(data);
    renderOptions('cc');
    renderOptions('qdisc');
    renderHealth(data);
    renderInterfaces(data);
    renderEvents(data);
    renderApplyBar();
  }

  function renderStatusPill(data) {
    els.daemonPill.classList.remove('is-off', 'is-loading');
    if (data.daemon.alive) {
      els.daemonPillText.textContent = '守护运行中';
    } else {
      els.daemonPill.classList.add('is-off');
      els.daemonPillText.textContent = '守护未运行';
    }
  }

  function displayName(kind, value) {
    const meta = kind === 'cc' ? CC_META : QDISC_META;
    if (value && meta[value] && meta[value].title) return meta[value].title;
    return value || '未知';
  }

  function renderCurrent(data) {
    els.currentCC.textContent = data.cc.current || '未知';
    els.currentCCName.textContent = displayName('cc', data.cc.current);
    els.currentQD.textContent = data.qdisc.current || '未知';
    els.currentQDName.textContent = displayName('qdisc', data.qdisc.current);
    els.kernelVersion.textContent = data.kernel || '未知内核';

    const total = data.interfaces.length;
    const good = data.interfaces.filter(function (item) { return item.ok; }).length;
    els.ifaceSummary.textContent = total === 0
      ? '无受管接口'
      : good + '/' + total + ' 接口就绪';
  }

  function renderNotices(data) {
    const drifted = data.cc.current !== data.cc.configured ||
      data.qdisc.current !== data.qdisc.configured;
    els.syncNotice.hidden = !drifted;
    if (drifted) {
      const current = (data.cc.current || '未知') + ' / ' + (data.qdisc.current || '未知');
      const desired = (data.cc.configured || '未知') + ' / ' + (data.qdisc.configured || '未知');
      els.syncNoticeText.textContent = data.daemon.alive
        ? '系统当前为 ' + current + '，配置为 ' + desired + '；守护进程会在 15 秒内恢复。'
        : '系统当前为 ' + current + '，配置为 ' + desired + '；守护进程未运行，请手动同步。';
    }
    els.daemonNotice.hidden = !!data.daemon.alive;
  }

  function renderOptions(kind) {
    const data = state.data;
    const list = data[kind].available;
    const container = kind === 'cc' ? els.ccOptions : els.qdiscOptions;
    const countEl = kind === 'cc' ? els.ccCount : els.qdiscCount;
    const sigKey = kind === 'cc' ? 'ccSig' : 'qdiscSig';
    const signature = list.join('|');

    countEl.textContent = list.length ? list.length + ' 种可用' : '无可用项';

    if (signature !== state[sigKey]) {
      state[sigKey] = signature;
      container.innerHTML = '';

      if (list.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'iface-empty';
        empty.textContent = '内核没有提供可用项';
        container.appendChild(empty);
      } else {
        const meta = kind === 'cc' ? CC_META : QDISC_META;
        list.forEach(function (value) {
          container.appendChild(buildOption(kind, value, meta[value]));
        });
      }
    }

    syncOptionSelection(kind);
  }

  function buildOption(kind, value, meta) {
    const item = meta || { title: value, desc: '内核提供的额外选项' };

    const label = document.createElement('label');
    label.className = 'option';
    label.dataset.value = value;

    const input = document.createElement('input');
    input.type = 'radio';
    input.name = kind;
    input.value = value;

    const radio = document.createElement('span');
    radio.className = 'option-radio';
    radio.setAttribute('aria-hidden', 'true');

    const body = document.createElement('span');
    body.className = 'option-body';

    const titleRow = document.createElement('span');
    titleRow.className = 'option-title-row';

    const title = document.createElement('span');
    title.className = 'option-title';
    title.textContent = item.title;
    titleRow.appendChild(title);

    if (item.title !== value) {
      const name = document.createElement('span');
      name.className = 'option-name';
      name.textContent = value;
      titleRow.appendChild(name);
    }

    if (item.recommend) {
      const tag = document.createElement('span');
      tag.className = 'tag tag-recommend';
      tag.textContent = '推荐';
      titleRow.appendChild(tag);
    }

    const currentTag = document.createElement('span');
    currentTag.className = 'option-current';
    currentTag.dataset.current = '';
    currentTag.textContent = '当前';
    currentTag.hidden = true;
    titleRow.appendChild(currentTag);

    const desc = document.createElement('span');
    desc.className = 'option-desc';
    desc.textContent = item.desc;

    body.appendChild(titleRow);
    body.appendChild(desc);
    label.appendChild(input);
    label.appendChild(radio);
    label.appendChild(body);

    return label;
  }

  function syncOptionSelection(kind) {
    const data = state.data;
    if (!data) return;
    const container = kind === 'cc' ? els.ccOptions : els.qdiscOptions;
    const pending = state.pending[kind];
    const current = data[kind].current;

    const options = container.querySelectorAll('.option');
    Array.prototype.forEach.call(options, function (label) {
      const value = label.dataset.value;
      const selected = value === pending;
      label.classList.toggle('is-selected', selected);
      const input = label.querySelector('input');
      if (input) input.checked = selected;
      const currentTag = label.querySelector('[data-current]');
      if (currentTag) currentTag.hidden = value !== current;
    });
  }

  function renderHealth(data) {
    els.daemonState.textContent = data.daemon.alive
      ? '运行中 · ' + data.daemon.pid
      : '未运行';
    setIconTone(els.daemonStatIcon, data.daemon.alive ? '' : 'is-bad');

    const total = data.interfaces.length;
    const good = data.interfaces.filter(function (item) { return item.ok; }).length;
    if (total === 0) {
      els.ifaceState.textContent = '无接口';
      setIconTone(els.ifaceStatIcon, 'is-warn');
    } else {
      els.ifaceState.textContent = good + '/' + total + ' 匹配';
      setIconTone(els.ifaceStatIcon, good === total ? '' : 'is-warn');
    }

    const bootOk = data.boot === '1';
    els.bootState.textContent = bootOk ? '已完成' : '未完成';
    setIconTone(els.bootState.closest('.stat').querySelector('.stat-icon'), bootOk ? '' : 'is-warn');

    els.moduleVersion.textContent = data.moduleVersion || state.moduleInfoVersion || '—';
  }

  function setIconTone(element, tone) {
    if (!element) return;
    element.classList.remove('is-warn', 'is-bad');
    if (tone) element.classList.add(tone);
  }

  function renderInterfaces(data) {
    els.ifaceList.innerHTML = '';

    if (data.interfaces.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'iface-empty';
      empty.textContent = '当前没有 wlan / rmnet / tun 接口';
      els.ifaceList.appendChild(empty);
      return;
    }

    data.interfaces.forEach(function (item) {
      const row = document.createElement('div');
      row.className = 'iface' + (item.ok ? '' : ' is-bad');
      if (!item.ok) {
        row.title = '期望 ' + data.qdisc.configured + '，当前 ' + (item.qdisc || '未知');
      }

      const dot = document.createElement('span');
      dot.className = 'iface-dot';
      dot.setAttribute('aria-hidden', 'true');

      const name = document.createElement('span');
      name.className = 'iface-name';
      name.textContent = item.name;

      const qdisc = document.createElement('span');
      qdisc.className = 'iface-qdisc';
      qdisc.textContent = item.qdisc || '未知';

      row.appendChild(dot);
      row.appendChild(name);
      row.appendChild(qdisc);
      els.ifaceList.appendChild(row);
    });
  }

  function renderEvents(data) {
    const events = data.events || [];
    els.logTail.textContent = events.length
      ? events.join('\n')
      : '（暂无变更事件）';
    els.eventCount.textContent = events.length ? events.length + ' 条' : '';
  }

  function updateDirty() {
    const data = state.data;
    if (!data || !state.pending.cc || !state.pending.qdisc) {
      state.dirty = false;
      return;
    }
    state.dirty = state.pending.cc !== data.cc.configured ||
      state.pending.qdisc !== data.qdisc.configured;
  }

  function renderApplyBar() {
    const data = state.data;
    const btnText = els.applyBtn.querySelector('.btn-text');

    if (!data) {
      els.applyLabel.textContent = '读取中';
      els.applySub.textContent = '—';
      els.applyBtn.disabled = true;
      els.presetBtn.disabled = true;
      return;
    }

    if (state.applying) {
      els.applyLabel.textContent = '正在应用';
      els.applySub.textContent = (state.pending.cc || '—') + ' + ' + (state.pending.qdisc || '—');
      els.applyBtn.disabled = true;
      els.applyBtn.classList.add('is-loading');
      els.applyBtn.setAttribute('aria-busy', 'true');
      if (btnText) btnText.textContent = '应用中';
    } else {
      els.applyBtn.classList.remove('is-loading');
      els.applyBtn.removeAttribute('aria-busy');
      if (btnText) btnText.textContent = '应用配置';

      if (state.dirty) {
        els.applyLabel.textContent = '待应用';
        els.applySub.textContent = (state.pending.cc || '—') + ' + ' + (state.pending.qdisc || '—');
      } else {
        els.applyLabel.textContent = '配置已生效';
        els.applySub.textContent = data.cc.configured + ' + ' + data.qdisc.configured;
      }
      els.applyBtn.disabled = !state.dirty;
    }

    const canRecommend = !!data &&
      data.cc.available.includes(RECOMMENDED.cc) &&
      data.qdisc.available.includes(RECOMMENDED.qdisc);
    els.presetBtn.disabled = state.applying || !canRecommend;
  }

  function onOptionChange(kind, event) {
    const input = event.target;
    if (!input || input.type !== 'radio' || input.name !== kind) return;
    state.pending[kind] = input.value;
    updateDirty();
    syncOptionSelection(kind);
    renderApplyBar();
  }

  async function apply() {
    if (state.applying || !state.data || !state.dirty) return;
    const cc = state.pending.cc;
    const qdisc = state.pending.qdisc;

    if (!SAFE_TOKEN.test(String(cc)) || !SAFE_TOKEN.test(String(qdisc))) {
      showError('参数无效', '选择值未通过校验，请刷新后重试。', 'cc=' + cc + ' qdisc=' + qdisc);
      return;
    }

    hideError();
    state.applying = true;
    renderApplyBar();

    let result;
    if (state.preview) {
      result = previewApply(cc, qdisc);
    } else {
      result = await runCLI('set-pair ' + cc + ' ' + qdisc);
    }

    state.applying = false;
    if (result.code !== 0) {
      renderApplyBar();
      showError('应用失败', '配置没有生效，请检查技术详情或模块日志。', result.stderr || result.stdout);
      return;
    }

    state.dirty = false;
    if (!state.preview) {
      await refresh({ silent: true });
    }

    const data = state.data;
    if (data && data.cc.current === data.cc.configured && data.qdisc.current === data.qdisc.configured) {
      const bad = data.interfaces.filter(function (item) { return !item.ok; }).length;
      if (bad > 0) {
        showToast('已切换，但 ' + bad + ' 个接口队列尚未生效', 'warn');
      } else {
        showToast('已应用 ' + cc + ' + ' + qdisc);
      }
      flashHero();
    } else if (data) {
      showError(
        '配置未完全生效',
        '当前为 ' + data.cc.current + ' / ' + data.qdisc.current +
          '，期望 ' + cc + ' / ' + qdisc + '。',
        ''
      );
    }
    renderApplyBar();
  }

  async function applyPreset() {
    if (state.applying || !state.data) return;
    const data = state.data;

    if (!data.cc.available.includes(RECOMMENDED.cc) ||
        !data.qdisc.available.includes(RECOMMENDED.qdisc)) {
      showToast('当前内核不支持推荐配置', 'warn');
      return;
    }

    state.pending.cc = RECOMMENDED.cc;
    state.pending.qdisc = RECOMMENDED.qdisc;
    updateDirty();
    syncOptionSelection('cc');
    syncOptionSelection('qdisc');
    renderApplyBar();

    if (state.dirty) {
      await apply();
      return;
    }

    const drifted = data.cc.current !== data.cc.configured ||
      data.qdisc.current !== data.qdisc.configured;
    if (drifted) {
      await syncNow();
    } else {
      showToast('已经是推荐配置');
    }
  }

  async function syncNow() {
    if (state.applying || !state.data) return;
    hideError();
    state.applying = true;
    renderApplyBar();

    let result;
    if (state.preview) {
      result = previewApply(state.data.cc.configured, state.data.qdisc.configured);
    } else {
      result = await runCLI('apply');
    }

    state.applying = false;
    if (result.code !== 0) {
      renderApplyBar();
      showError('同步失败', '无法重新应用配置。', result.stderr || result.stdout);
      return;
    }

    if (!state.preview) {
      await refresh({ silent: true });
    }
    const data = state.data;
    if (data && data.cc.current === data.cc.configured && data.qdisc.current === data.qdisc.configured) {
      showToast('已重新应用配置');
      flashHero();
    } else if (data) {
      showError(
        '同步未完成',
        '当前为 ' + data.cc.current + ' / ' + data.qdisc.current +
          '，配置为 ' + data.cc.configured + ' / ' + data.qdisc.configured + '。',
        result.stderr || result.stdout
      );
    }
    renderApplyBar();
  }

  async function restartDaemon() {
    if (state.applying || !state.data) return;
    hideError();
    state.applying = true;
    renderApplyBar();

    let result;
    if (state.preview) {
      state.data.daemon.alive = true;
      state.data.daemon.pid = 4242;
      result = { code: 0, stdout: '预览模式', stderr: '' };
      render();
    } else {
      result = await runCLI('restart');
    }

    state.applying = false;
    const failed = result.code !== 0 ||
      (result.stdout && result.stdout.indexOf('重启失败') >= 0);

    if (failed) {
      renderApplyBar();
      showError('重启失败', '后台守护没有启动，请检查模块日志。', result.stderr || result.stdout);
      return;
    }

    if (!state.preview) {
      await refresh({ silent: true });
    }
    showToast('后台守护已重启');
    renderApplyBar();
  }

  function showError(title, friendly, detail, options) {
    const scroll = !options || options.scroll !== false;
    els.errorTitle.textContent = title;
    els.errorText.textContent = friendly || '';
    const clean = (detail || '').trim();
    if (clean) {
      els.errorDetailText.textContent = clean.slice(0, 1200);
      els.errorDetails.hidden = false;
    } else {
      els.errorDetails.hidden = true;
      els.errorDetailText.textContent = '';
    }
    els.errorNotice.hidden = false;
    if (scroll) {
      els.errorNotice.scrollIntoView({
        block: 'nearest',
        behavior: prefersReducedMotion() ? 'auto' : 'smooth',
      });
    }
  }

  function hideError() {
    els.errorNotice.hidden = true;
  }

  function showToast(message, tone) {
    const kind = tone || 'ok';
    window.clearTimeout(state.toastTimer);
    els.toast.textContent = message;
    els.toast.classList.remove('is-visible', 'is-error', 'is-warn');
    if (kind === 'error') els.toast.classList.add('is-error');
    if (kind === 'warn') els.toast.classList.add('is-warn');
    window.requestAnimationFrame(function () {
      els.toast.classList.add('is-visible');
    });
    state.toastTimer = window.setTimeout(function () {
      els.toast.classList.remove('is-visible');
    }, kind === 'error' ? 6000 : 3500);
  }

  function flashHero() {
    const hero = document.querySelector('.hero');
    if (!hero) return;
    window.clearTimeout(state.flashTimer);
    hero.classList.remove('is-flash');
    void hero.offsetWidth;
    hero.classList.add('is-flash');
    state.flashTimer = window.setTimeout(function () {
      hero.classList.remove('is-flash');
    }, 900);
  }

  function startPolling() {
    stopPolling();
    state.polling = window.setInterval(function () {
      if (document.hidden || state.applying || state.refreshing) return;
      refresh({ silent: true });
    }, 15000);
  }

  function stopPolling() {
    if (state.polling) {
      window.clearInterval(state.polling);
      state.polling = 0;
    }
  }

  function previewApply(cc, qdisc) {
    if (!state.data) return { code: -1, stdout: '', stderr: '无数据' };
    state.data.cc.current = cc;
    state.data.cc.configured = cc;
    state.data.qdisc.current = qdisc;
    state.data.qdisc.configured = qdisc;
    state.data.interfaces.forEach(function (item) {
      item.qdisc = qdisc;
      item.ok = true;
    });
    state.data.events.unshift(new Date().toISOString().slice(0, 19).replace('T', ' ') +
      ' config: cc=' + cc + ' qdisc=' + qdisc);
    state.data.events = state.data.events.slice(0, 8);
    state.dirty = false;
    render();
    return { code: 0, stdout: '预览模式已应用', stderr: '' };
  }

  function initPreview() {
    state.preview = true;
    state.data = {
      kernel: '6.1.176-android14-11+ · preview',
      boot: '1',
      moduleVersion: 'v1.3',
      cc: {
        current: 'bbr3',
        configured: 'bbr3',
        available: ['reno', 'bbr', 'bbr3', 'cubic'],
      },
      qdisc: {
        current: 'fq',
        configured: 'fq',
        available: ['fq', 'fq_codel', 'codel', 'sfq', 'pfifo_fast', 'pfifo'],
      },
      daemon: { alive: true, pid: 3285 },
      interfaces: [
        { name: 'rmnet_data0', qdisc: 'fq', ok: true },
        { name: 'rmnet_data1', qdisc: 'fq', ok: true },
        { name: 'rmnet_data2', qdisc: 'fq', ok: true },
      ],
      events: [
        '2026-11-06 00:40:12 service started: cc=bbr3 default_qdisc=fq',
        '2026-11-06 00:40:12 fq -> rmnet_data1',
        '2026-11-06 00:40:12 fq -> rmnet_data2',
      ],
    };
    state.pending.cc = 'bbr3';
    state.pending.qdisc = 'fq';
    render();
  }

  async function init() {
    cacheElements();
    bindEvents();

    if (window.ksu && typeof window.ksu.enableEdgeToEdge === 'function') {
      try { window.ksu.enableEdgeToEdge(true); } catch (error) { /* ignore */ }
    }

    if (!hasBridge()) {
      els.previewBanner.hidden = false;
      els.daemonPill.classList.add('is-loading');
      els.daemonPillText.textContent = '预览模式';
      initPreview();
      return;
    }

    loadModuleDir();
    els.daemonPill.classList.add('is-loading');
    els.daemonPillText.textContent = '读取中';
    await refresh({ silent: false });
    startPolling();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
