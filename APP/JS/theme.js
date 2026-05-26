/**
 * InvertirCL — Theme Manager v1.0
 * ================================
 * J4 del Roadmap: Toggle modo oscuro / claro
 *
 * USO EN CADA PÁGINA:
 *   <script src="../js/theme.js"></script>
 *   Luego en el topbar: <div onclick="Theme.toggle()" ...>
 *   O usar el botón generado automáticamente: Theme.insertarBoton()
 *
 * CÓMO FUNCIONA:
 *   - Agrega/quita la clase 'light-mode' en <html>
 *   - Las variables CSS se sobreescriben con el selector html.light-mode
 *   - Transición suave de 0.25s en todas las propiedades de color
 *   - Persiste en localStorage bajo 'invertircl_theme'
 *   - Se aplica ANTES de que el DOM pinte (evita flash de modo equivocado)
 */

const Theme = (() => {

  const KEY        = 'invertircl_theme';
  const DARK_CLASS = '';           // clase por defecto (sin clase = dark)
  const LIGHT_CLASS = 'light-mode';

  // ── CSS DEL MODO CLARO ──────────────────────────────────────
  // Se inyecta en el <head> una sola vez. Sobreescribe las variables
  // CSS de :root solo cuando html tiene la clase 'light-mode'.
  const LIGHT_CSS = `
    html.light-mode {
      --bg:    #f0f2f5;
      --bg2:   #ffffff;
      --bg3:   #e8ecf0;
      --bg4:   #dde2e8;
      --verde: #00a854;
      --verde2:#008a44;
      --rojo:  #e53935;
      --azul:  #1a73e8;
      --amarillo:#e37400;
      --purpura:#7b1fa2;
      --naranja:#e64a19;
      --cyan:  #0097a7;
      --linea: rgba(0,0,0,0.08);
      --linea2:rgba(0,0,0,0.14);
      --texto: #1a202c;
      --texto2:#4a5568;
      --texto3:#a0aec0;
    }

    /* Transición suave en todos los elementos */
    html.light-mode *,
    html.light-mode *::before,
    html.light-mode *::after {
      transition:
        background-color 0.25s ease,
        border-color     0.25s ease,
        color            0.25s ease,
        box-shadow       0.25s ease !important;
    }

    /* Canvas y SVG no se transicionan (rendimiento) */
    html.light-mode canvas,
    html.light-mode svg {
      transition: none !important;
    }

    /* Ajustes específicos para elementos que necesitan más contraste en claro */
    html.light-mode .topbar,
    html.light-mode .sidebar,
    html.light-mode nav {
      box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    }

    html.light-mode .kpi-card,
    html.light-mode .sprint,
    html.light-mode .chart-panel,
    html.light-mode .positions-panel,
    html.light-mode .table-panel,
    html.light-mode .learn-panel,
    html.light-mode .settings-card,
    html.light-mode .ranking-wrap,
    html.light-mode .scanner-panel {
      box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }

    /* Inputs en modo claro */
    html.light-mode input,
    html.light-mode select,
    html.light-mode textarea {
      color: var(--texto);
    }

    /* Scrollbar en modo claro */
    html.light-mode ::-webkit-scrollbar-thumb {
      background: #c0c8d0;
    }

    /* Logo verde más oscuro para mejor contraste */
    html.light-mode .logo,
    html.light-mode .nav-logo,
    html.light-mode .topbar-logo {
      color: var(--verde);
    }

    /* Sparklines y gráficos — mantener colores vibrantes */
    html.light-mode .pos { color: var(--verde); }
    html.light-mode .neg { color: var(--rojo);  }

    /* Botón del tema */
    html.light-mode #theme-toggle-btn {
      background: rgba(0,0,0,0.06);
      border-color: rgba(0,0,0,0.12);
      color: var(--texto2);
    }
    html.light-mode #theme-toggle-btn:hover {
      background: rgba(0,0,0,0.1);
    }
  `;

  // ── ESTILOS DEL BOTÓN ───────────────────────────────────────
  const BTN_CSS = `
    #theme-toggle-btn {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 5px 11px;
      border-radius: 7px;
      border: 1px solid var(--linea2);
      background: rgba(255,255,255,0.04);
      color: var(--texto2);
      font-size: 0.75rem;
      font-family: var(--sans, 'DM Sans', sans-serif);
      cursor: pointer;
      transition: all 0.2s;
      white-space: nowrap;
      flex-shrink: 0;
    }
    #theme-toggle-btn:hover {
      background: rgba(255,255,255,0.08);
      color: var(--texto);
    }
    #theme-toggle-btn .theme-icon {
      font-size: 0.85rem;
      transition: transform 0.3s ease;
    }
    #theme-toggle-btn:active .theme-icon {
      transform: rotate(20deg);
    }
  `;

  // ── INYECTAR CSS ─────────────────────────────────────────────
  function inyectarCSS() {
    if (document.getElementById('invertircl-theme-css')) return;
    const style = document.createElement('style');
    style.id = 'invertircl-theme-css';
    style.textContent = LIGHT_CSS + BTN_CSS;
    document.head.appendChild(style);
  }

  // ── APLICAR TEMA (sin animación al cargar) ──────────────────
  function aplicar(tema, animado = false) {
    const html = document.documentElement;
    if (!animado) {
      // Desactivar transición global temporalmente para evitar flash
      html.style.transition = 'none';
    }
    if (tema === 'light') {
      html.classList.add(LIGHT_CLASS);
    } else {
      html.classList.remove(LIGHT_CLASS);
    }
    if (!animado) {
      // Forzar reflow y re-activar transición
      void html.offsetHeight;
      html.style.transition = '';
    }
    actualizarBoton(tema);
  }

  // ── ACTUALIZAR ÍCONO DEL BOTÓN ───────────────────────────────
  function actualizarBoton(tema) {
    const btn = document.getElementById('theme-toggle-btn');
    if (!btn) return;
    const icon = btn.querySelector('.theme-icon');
    const label = btn.querySelector('.theme-label');
    if (icon)  icon.textContent  = tema === 'light' ? '🌙' : '☀️';
    if (label) label.textContent = tema === 'light' ? 'Oscuro' : 'Claro';
    btn.title = tema === 'light' ? 'Cambiar a modo oscuro' : 'Cambiar a modo claro';
  }

  // ── API PÚBLICA ─────────────────────────────────────────────
  function getTema() {
    return localStorage.getItem(KEY) || 'dark';
  }

  function toggle() {
    const nuevo = getTema() === 'dark' ? 'light' : 'dark';
    localStorage.setItem(KEY, nuevo);
    aplicar(nuevo, true);
  }

  function insertarBoton(contenedorId) {
    // Si se pasa un ID, inserta el botón ahí. Si no, lo inserta en .topbar-right o .top-right
    const destino = contenedorId
      ? document.getElementById(contenedorId)
      : document.querySelector('.topbar-right') || document.querySelector('.top-right');

    if (!destino) return;
    if (document.getElementById('theme-toggle-btn')) return; // ya existe

    const btn = document.createElement('button');
    btn.id = 'theme-toggle-btn';
    btn.innerHTML = `<span class="theme-icon">☀️</span><span class="theme-label">Claro</span>`;
    btn.onclick = () => Theme.toggle();
    // Insertar al inicio del contenedor
    destino.insertBefore(btn, destino.firstChild);

    actualizarBoton(getTema());
  }

  // ── INIT AUTOMÁTICO ─────────────────────────────────────────
  // Se ejecuta inmediatamente (antes del DOMContentLoaded) para
  // evitar el flash del tema equivocado al cargar la página.
  (function initInmediato() {
    inyectarCSS();
    const tema = getTema();
    if (tema === 'light') {
      document.documentElement.classList.add(LIGHT_CLASS);
    }
  })();

  // Insertar botón cuando el DOM esté listo
  document.addEventListener('DOMContentLoaded', () => {
    insertarBoton();
    actualizarBoton(getTema());
  });

  return { toggle, getTema, aplicar, insertarBoton };

})();

window.Theme = Theme;
console.log('[InvertirCL] Theme Manager v1.0 cargado. Tema actual:', Theme.getTema());
