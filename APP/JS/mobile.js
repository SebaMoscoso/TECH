/**
 * InvertirCL — Mobile JS v1.0
 * ============================
 * J5 del Roadmap: Lógica de interacción móvil
 *
 * Maneja:
 *  - Botón hamburguesa → sidebar drawer
 *  - Overlay de cierre
 *  - Toggle del order panel en el simulador
 *  - Detección de breakpoint
 *
 * USO: <script src="../js/mobile.js"></script>
 * Se auto-inicializa al cargar el DOM.
 */

const Mobile = (() => {

  const BP_TABLET = 768;
  const BP_MOBILE = 480;

  function isMobile()  { return window.innerWidth <= BP_MOBILE; }
  function isTablet()  { return window.innerWidth <= BP_TABLET; }
  function isDesktop() { return window.innerWidth > BP_TABLET; }

  // ── INSERTAR OVERLAY ─────────────────────────────────────────
  function insertarOverlay() {
    if (document.getElementById('sidebar-overlay')) return;
    const div = document.createElement('div');
    div.id        = 'sidebar-overlay';
    div.className = 'sidebar-overlay';
    div.onclick   = () => cerrarTodo();
    document.body.appendChild(div);
  }

  // ── INSERTAR HAMBURGUESA EN TOPBAR ───────────────────────────
  function insertarHamburguesa() {
    const topbar = document.querySelector('.topbar');
    if (!topbar || document.querySelector('.hamburger-btn')) return;

    const btn = document.createElement('button');
    btn.className = 'hamburger-btn';
    btn.id        = 'hamburger-btn';
    btn.setAttribute('aria-label', 'Menú');
    btn.innerHTML = `<span></span><span></span><span></span>`;
    btn.onclick   = () => toggleSidebar();

    // Insertar después del logo
    const logo = topbar.querySelector('.logo, .nav-logo, .logo-link');
    if (logo && logo.nextSibling) {
      topbar.insertBefore(btn, logo.nextSibling);
    } else {
      topbar.insertBefore(btn, topbar.firstChild);
    }
  }

  // ── TOGGLE SIDEBAR ────────────────────────────────────────────
  function toggleSidebar() {
    const sidebar  = document.querySelector('.sidebar, .settings-nav, .sidebar-left');
    const overlay  = document.getElementById('sidebar-overlay');
    const hamburger = document.getElementById('hamburger-btn');
    if (!sidebar) return;

    const isOpen = sidebar.classList.contains('open');
    if (isOpen) {
      cerrarTodo();
    } else {
      sidebar.classList.add('open');
      overlay?.classList.add('active');
      hamburger?.classList.add('open');
      document.body.style.overflow = 'hidden';
    }
  }

  // ── TOGGLE SIDEBAR DERECHO (heatmap) ─────────────────────────
  function toggleSidebarRight() {
    const sidebar  = document.querySelector('.sidebar-right');
    const overlay  = document.getElementById('sidebar-overlay');
    if (!sidebar) return;
    const isOpen = sidebar.classList.contains('open');
    if (isOpen) {
      sidebar.classList.remove('open');
      overlay?.classList.remove('active');
      document.body.style.overflow = '';
    } else {
      sidebar.classList.add('open');
      overlay?.classList.add('active');
      document.body.style.overflow = 'hidden';
    }
  }

  // ── CERRAR TODO ───────────────────────────────────────────────
  function cerrarTodo() {
    document.querySelectorAll('.sidebar, .settings-nav, .sidebar-left, .sidebar-right')
      .forEach(el => el.classList.remove('open'));
    document.getElementById('sidebar-overlay')?.classList.remove('active');
    document.getElementById('hamburger-btn')?.classList.remove('open');
    document.body.style.overflow = '';
  }

  // ── BARRA DE TOGGLE SIMULADOR ─────────────────────────────────
  function insertarSimToggleBar() {
    // Solo en simulator.html (detectar por presencia de .order-panel)
    const orderPanel = document.querySelector('.order-panel');
    const rightPanel = document.querySelector('.right-panel');
    if (!orderPanel && !rightPanel) return;
    if (document.querySelector('.sim-toggle-bar')) return;

    const bar = document.createElement('div');
    bar.className = 'sim-toggle-bar';
    bar.innerHTML = `
      <button class="sim-toggle-btn" id="btnToggleOrden" onclick="Mobile.toggleOrderPanel()">
        📋 Nueva orden
      </button>
      <button class="sim-toggle-btn" id="btnToggleRight" onclick="Mobile.toggleRightPanel()">
        📊 Historial
      </button>
    `;

    // Insertar después del topbar, antes del layout
    const layout = document.querySelector('.layout');
    if (layout) {
      layout.parentNode.insertBefore(bar, layout);
    }
  }

  // ── TOGGLE ORDER PANEL (simulador) ───────────────────────────
  function toggleOrderPanel() {
    const panel = document.querySelector('.order-panel');
    const btn   = document.getElementById('btnToggleOrden');
    if (!panel) return;
    panel.classList.toggle('open');
    btn?.classList.toggle('active', panel.classList.contains('open'));
  }

  // ── TOGGLE RIGHT PANEL (simulador) ───────────────────────────
  function toggleRightPanel() {
    const panel = document.querySelector('.right-panel');
    const btn   = document.getElementById('btnToggleRight');
    if (!panel) return;
    // En móvil solo toggling max-height
    const isOpen = panel.style.maxHeight && panel.style.maxHeight !== '240px';
    panel.style.maxHeight = isOpen ? '240px' : '480px';
    btn?.classList.toggle('active', !isOpen);
  }

  // ── BOTONES HEATMAP ───────────────────────────────────────────
  function insertarHeatmapBtns() {
    const centerPanel = document.querySelector('.center-panel');
    if (!centerPanel) return;
    if (document.getElementById('heatmap-mobile-btns')) return;

    const wrap = document.createElement('div');
    wrap.id        = 'heatmap-mobile-btns';
    wrap.style.cssText = `
      display:none;padding:8px 12px;background:var(--bg2);
      border-bottom:1px solid var(--linea);gap:8px;
    `;
    wrap.innerHTML = `
      <button class="sim-toggle-btn" onclick="Mobile.toggleSidebar()">⚙️ Controles</button>
      <button class="sim-toggle-btn" onclick="Mobile.toggleSidebarRight()">📖 Order Book</button>
    `;

    const header = centerPanel.querySelector('.center-header');
    if (header) header.after(wrap);

    // Mostrar solo en móvil/tablet
    if (isTablet()) wrap.style.display = 'flex';
  }

  // ── RESIZE HANDLER ────────────────────────────────────────────
  function onResize() {
    if (isDesktop()) {
      // En desktop: cerrar todo y restaurar
      cerrarTodo();
      // Restaurar order/right panel
      const orderPanel = document.querySelector('.order-panel');
      if (orderPanel) orderPanel.classList.remove('open');
    }

    // Mostrar/ocultar botones específicos de móvil
    const heatmapBtns = document.getElementById('heatmap-mobile-btns');
    if (heatmapBtns) {
      heatmapBtns.style.display = isTablet() ? 'flex' : 'none';
    }
  }

  // ── INIT ─────────────────────────────────────────────────────
  function init() {
    insertarOverlay();
    insertarHamburguesa();
    insertarSimToggleBar();
    insertarHeatmapBtns();

    window.addEventListener('resize', onResize);

    // Cerrar sidebar con tecla Escape
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') cerrarTodo();
    });
  }

  document.addEventListener('DOMContentLoaded', init);

  return {
    toggleSidebar,
    toggleSidebarRight,
    toggleOrderPanel,
    toggleRightPanel,
    cerrarTodo,
    isMobile,
    isTablet,
    isDesktop,
  };

})();

window.Mobile = Mobile;
console.log('[InvertirCL] Mobile JS v1.0 cargado');
