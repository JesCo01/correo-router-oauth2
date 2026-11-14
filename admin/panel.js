"use strict";
/* Panel de correo-reenvio. Sin frameworks: estado unico, render por vista y
   delegacion de eventos. Las validaciones de cliente reflejan motor.validar();
   el servidor siempre vuelve a validar al guardar. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const RE_ID = /^[a-z0-9_]{1,40}$/;
const RE_CORREO = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const VISTAS = { resumen: "Resumen", cuentas: "Cuentas", reglas: "Reglas", ajustes: "Ajustes", raspberry: "Raspberry Pi", registro: "Registro del servicio" };
const PROVEEDORES = { outlook: "Outlook / Hotmail", gmail: "Gmail" };
const AUTENTICACIONES = { oauth2: "Microsoft (OAuth2)", app_password: "Contrasena de aplicacion" };
const MODOS = {
  todo: { etiqueta: "Todo a un correo", ayuda: "Todo lo que llegue a la cuenta se reenvia a un solo destino." },
  filtro: { etiqueta: "Filtrar a un correo", ayuda: "Solo los correos que cumplan los filtros se reenvian a un destino." },
  rutas: { etiqueta: "Cada remitente a su correo", ayuda: "Cada ruta tiene sus filtros y su destino. Gana la primera que coincida." },
};
const AJUSTES = [
  ["intervalo_respaldo_seg", "Revision periodica (s)", "Intervalo de Microsoft Graph; tambien para Gmail si no soporta IDLE.", 60, 3600],
  ["idle_seg", "Espera IDLE maxima (s)", "Tras este tiempo se renueva la conexion y se resincroniza.", 60, 1700],
  ["reintentos", "Intentos por correo", "Antes de omitir un correo que no se puede enviar.", 1, 20],
  ["limite_por_ciclo", "Correos por regla y ciclo", "Limite de correos que se procesan en cada ciclo.", 1, 500],
  ["tamano_maximo_mb", "Tamano maximo (MB)", "Correos mas grandes se omiten. Protege la memoria de la Raspberry.", 1, 25],
];

const S = {
  vista: "resumen",
  base: null,          // ultima respuesta de /api/config (linea base del servidor)
  cfg: null,           // copia de trabajo
  marca: "",           // JSON de cfg al cargar o guardar; sirve para detectar cambios
  secretos: {},        // { cuentaId: contrasena } pendiente de guardar en .env
  errores: {},         // validacion en vivo: { clave: mensaje }
  servicio: { activo: false, cuentas: {} },
  registro: [],
  oauth: null,         // { id, codigo, url, estado, detalle }
  sondeoOauth: null,
  exportacion: null,
  editorCuenta: null,
  editorRegla: null,
  guardando: false,
};

/* ------------------------------------------------------------ red */

async function api(ruta, cuerpo) {
  const opts = cuerpo === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cuerpo),
  };
  try {
    const r = await fetch(ruta, opts);
    const data = await r.json().catch(() => ({}));
    return { ok: r.ok, data };
  } catch (e) {
    return { ok: false, data: { mensaje: "No hay conexion con el panel.", errores: ["Sin conexion con el panel."] } };
  }
}

async function cargarConfig() {
  const { ok, data } = await api("/api/config");
  if (!ok) return toast("No se pudo leer la configuracion.", "err");
  S.base = data;
  S.cfg = structuredClone(data.config);
  S.editorCuenta = null;
  S.editorRegla = null;
  S.secretos = {};
  S.marca = JSON.stringify(S.cfg);
  render();
}

async function cargarEstado() {
  const { ok, data } = await api("/api/estado");
  if (!ok) return;
  S.servicio = data;
  pintarServicio();
  if (S.vista === "resumen") render();
  else pintarEstadosCuentas();
}

async function cargarRegistro() {
  const { ok, data } = await api("/api/registro?n=200");
  if (!ok) return;
  S.registro = data.lineas || [];
  if (S.vista === "registro") {
    const caja = $("#registro-caja");
    if (caja) {
      caja.innerHTML = pintarRegistro();
      caja.scrollTop = caja.scrollHeight;
    }
  }
}

/* ------------------------------------------------------------ estado y validacion */

const sucio = () => !!S.cfg && (JSON.stringify(S.cfg) !== S.marca || Object.keys(S.secretos).length > 0);

function validar() {
  const e = {};
  if (!S.cfg) return e;
  const ids = S.cfg.cuentas.map(c => c.id);
  S.cfg.cuentas.forEach((c, i) => {
    if (!RE_ID.test(c.id || "")) e[`c${i}.id`] = "Solo minusculas, numeros y guion bajo (maximo 40).";
    else if (ids.indexOf(c.id) !== i) e[`c${i}.id`] = "Este identificador ya existe.";
    if (!RE_CORREO.test(c.correo || "")) e[`c${i}.correo`] = "Escribe un correo valido.";
    if (c.auth === "oauth2" && c.proveedor !== "outlook") e[`c${i}.auth`] = "OAuth2 solo esta disponible para Outlook.";
    if (c.proveedor === "outlook" && c.auth !== "oauth2") e[`c${i}.auth`] = "Outlook usa Microsoft Graph con OAuth2.";
  });
  const rids = S.cfg.reglas.map(r => r.id);
  S.cfg.reglas.forEach((r, i) => {
    if (!r.nombre) e[`r${i}.nombre`] = "Escribe un nombre para la regla.";
    if (!r.cuenta || !ids.includes(r.cuenta)) e[`r${i}.cuenta`] = "Elige la cuenta de origen.";
    if (rids.indexOf(r.id) !== i) e[`r${i}.id`] = "Identificador repetido.";
    if (r.limite !== undefined && !(Number.isInteger(r.limite) && r.limite >= 1 && r.limite <= 500)) e[`r${i}.limite`] = "Entre 1 y 500.";
    if (!r.rutas || !r.rutas.length) e[`r${i}.sinrutas`] = "Agrega al menos una ruta.";
    (r.rutas || []).forEach((ru, j) => {
      if (!RE_CORREO.test(ru.destino || "")) e[`r${i}.ruta${j}.destino`] = "Correo de destino no valido.";
      const dias = ru.filtros && ru.filtros.dias;
      if (dias !== undefined && !(Number.isInteger(Number(dias)) && Number(dias) >= 0)) e[`r${i}.ruta${j}.dias`] = "Numero de dias (0 o mas).";
    });
  });
  AJUSTES.forEach(([k, , , min, max]) => {
    const v = S.cfg.ajustes && S.cfg.ajustes[k];
    if (v !== undefined && !(Number(v) >= min && Number(v) <= max)) e[`ajuste.${k}`] = `Debe estar entre ${min} y ${max}.`;
  });
  return e;
}

function estadoCuenta(c, i) {
  const cred = (S.base && S.base.credenciales && S.base.credenciales[c.id]) || null;
  const run = S.servicio.cuentas && S.servicio.cuentas[c.id];
  if (c.activa === false) return { clase: "neutro", texto: "Pausada", title: "La cuenta esta desactivada." };
  if (!cred || !cred.ok) return { clase: "warn", texto: "Sin credenciales", title: "Falta contrasena o conexion con Microsoft." };
  if (c.raspberry_pi) return { clase: "neutro", texto: "Asignada a Pi", title: "El estado de la Raspberry se consulta en su registro; no desde este panel." };
  if (run && run.estado === "requiere_login") return { clase: "warn", texto: "Reconectar", title: run.error || "Microsoft requiere iniciar sesion otra vez." };
  if (!S.servicio.activo) return { clase: "neutro", texto: "Servicio detenido", title: "Inicia el servicio para procesar correo." };
  if (run && run.estado === "activa") return { clase: "ok", texto: "Conectada", title: "Escuchando correo nuevo." };
  if (run && run.estado === "error") return { clase: "err", texto: "Error", title: run.error || "Error de conexion." };
  return { clase: "neutro", texto: "Conectando…", title: "Estableciendo conexion." };
}

/* ------------------------------------------------------------ render */

function render() {
  if (!S.cfg) return;
  S.errores = validar();
  $("#titulo").textContent = VISTAS[S.vista];
  $$(".nav button[data-vista]").forEach(b => b.setAttribute("aria-current", b.dataset.vista === S.vista ? "page" : "false"));
  const vistas = { resumen: pintarResumen, cuentas: pintarCuentas, reglas: pintarReglas, ajustes: pintarAjustes, raspberry: pintarRaspberry, registro: pintarRegistroVista };
  $("#vista").innerHTML = vistas[S.vista]();
  pintarServicio();
  actualizarBotones();
}

// Actualiza errores y botones sin re-pintar la vista (no pierde el foco al escribir)
function tocar() {
  S.errores = validar();
  $$("[data-error-for]").forEach(el => {
    const msg = S.errores[el.dataset.errorFor] || "";
    el.textContent = msg;
    const campo = el.closest(".campo");
    if (campo) campo.classList.toggle("error", !!msg);
  });
  actualizarBotones();
}

function actualizarBotones() {
  const hayErrores = Object.keys(S.errores).length > 0;
  const cambios = sucio();
  const guardar = $("#btn-guardar");
  guardar.disabled = S.guardando || !cambios || hayErrores;
  const guardarCuenta = $('[data-accion="guardar-cuenta"]');
  if (guardarCuenta) guardarCuenta.disabled = S.guardando || hayErrores;
  const guardarRegla = $('[data-accion="guardar-regla"]');
  if (guardarRegla) guardarRegla.disabled = S.guardando || hayErrores;
  guardar.title = hayErrores ? "Corrige los campos marcados en rojo" : (cambios ? "" : "No hay cambios");
  $("#btn-descartar").disabled = !cambios;
  const exportar = $('[data-accion="exportar-pi"]');
  if (exportar) exportar.disabled = cambios || hayErrores || !S.cfg.cuentas.some(c => c.raspberry_pi && c.activa !== false);
}

function pintarServicio() {
  const chip = $("#estado-servicio");
  const btn = $("#btn-servicio");
  if (S.servicio.activo) {
    chip.className = "chip ok";
    chip.textContent = "Servicio del PC activo";
    btn.textContent = "Detener lector del PC";
  } else {
    chip.className = "chip neutro";
    chip.textContent = "Servicio del PC detenido";
    btn.textContent = "Iniciar lector del PC";
  }
}

function pintarEstadosCuentas() {
  S.cfg.cuentas.forEach((c, i) => {
    const el = document.querySelector(`[data-estado-cuenta="${i}"]`);
    if (!el) return;
    const est = estadoCuenta(c, i);
    el.className = `chip ${est.clase}`;
    el.textContent = est.texto;
    el.title = est.title;
  });
}

const campo = ({ etiqueta, control, ayuda = "", clave = "", ancho = false }) => `
  <label class="campo${ancho ? " ancho" : ""}">
    <span>${esc(etiqueta)}</span>
    ${control}
    ${ayuda ? `<small>${esc(ayuda)}</small>` : ""}
    ${clave ? `<span class="msg-error" data-error-for="${esc(clave)}">${esc(S.errores[clave] || "")}</span>` : ""}
  </label>`;

const interruptor = (attrs, marcado, texto) => `
  <label class="check">
    <span class="interruptor"><input type="checkbox" ${attrs}${marcado ? " checked" : ""}><span></span></span>
    <span>${esc(texto)}</span>
  </label>`;

const opciones = (lista, sel) => lista.map(([v, t]) => `<option value="${esc(v)}"${v === sel ? " selected" : ""}>${esc(t)}</option>`).join("");

const vacio = (titulo, texto, accion = "") => `
  <div class="vacio"><strong>${esc(titulo)}</strong><div>${esc(texto)}</div>${accion}</div>`;

const alerta = (tipo, html) => `<div class="alerta ${tipo}" role="status"><div>${html}</div></div>`;

/* ----- Resumen ----- */

function pintarResumen() {
  const cuentas = S.cfg.cuentas;
  const reglas = S.cfg.reglas;
  const enviados = Object.values(S.servicio.cuentas || {}).reduce((a, c) => a + (c.enviados || 0), 0);
  const sinCred = cuentas.filter(c => S.base && S.base.credenciales && S.base.credenciales[c.id] && !S.base.credenciales[c.id].ok).length;
  const nErr = Object.keys(S.errores).length;

  const avisos = [];
  if (nErr) avisos.push(alerta("err", `Hay <b>${nErr}</b> campo(s) por corregir antes de guardar.`));
  if (sucio()) avisos.push(alerta("warn", "Tienes cambios sin guardar."));
  if (sinCred) avisos.push(alerta("warn", `${sinCred} cuenta(s) necesitan contrasena o conexion con Microsoft.`));
  if (!S.servicio.activo && reglas.length) avisos.push(alerta("info", "El servicio esta detenido. Pulsa <b>Iniciar servicio</b> para procesar correo."));

  const filas = reglas.map(r => {
    const destinos = (r.rutas || []).map(ru => `${esc(ru.nombre || "Ruta")} → <code>${esc(ru.destino || "sin destino")}</code>`).join(" · ");
    return `<div class="fila"><div class="texto"><b>${esc(r.nombre || "Sin nombre")}</b>
      <small>${esc(r.cuenta || "sin cuenta")} · ${esc(MODOS[r.modo] ? MODOS[r.modo].etiqueta : "")} · ${destinos}</small></div>
      <span class="chip ${r.activa === false ? "neutro" : "ok"}">${r.activa === false ? "Pausada" : "Activa"}</span></div>`;
  }).join("");

  return `
    <div class="metricas">
      <div class="metrica"><small>Cuentas</small><b>${cuentas.length}</b></div>
      <div class="metrica"><small>Reglas activas</small><b>${reglas.filter(r => r.activa !== false).length}</b></div>
      <div class="metrica"><small>Reenviados (sesion)</small><b>${enviados}</b></div>
      <div class="metrica"><small>Servicio</small><b>${S.servicio.activo ? "Activo" : "Detenido"}</b></div>
    </div>
    ${avisos.join("")}
    <div class="card">
      <div class="card-cab"><div><h2>Flujo de reenvio</h2><small>Lo que hace cada regla, en orden.</small></div>
        <button class="btn btn-pequeno" data-accion="ir" data-vista="reglas">Editar reglas</button></div>
      ${reglas.length ? `<div class="lista">${filas}</div>` : vacio("Sin reglas", "Crea una regla para empezar a reenviar correo.", `<div class="mt"><button class="btn btn-primario" data-accion="ir" data-vista="reglas">Crear regla</button></div>`)}
    </div>`;
}

/* ----- Exportacion Raspberry Pi ----- */

function pintarRaspberry() {
  const seleccionadas = S.cfg.cuentas.filter(c => c.activa !== false && c.raspberry_pi);
  const filas = S.cfg.cuentas.map((c, i) => `<div class="fila">
    ${interruptor(`data-ent="cuenta" data-i="${i}" data-campo="raspberry_pi"`, !!c.raspberry_pi, c.correo || c.id || "Nueva cuenta")}
    <small>${c.activa === false ? "Pausada: no se exporta" : `${S.cfg.reglas.filter(r => r.cuenta === c.id && r.activa !== false).length} reglas activas`}</small>
  </div>`).join("");
  return `<div class="card">
    <div class="card-cab"><div><h2>Cuentas para Raspberry Pi</h2><small>Las cuentas seleccionadas se excluyen del lector del PC.</small></div></div>
    <div class="lista">${filas || "Agrega cuentas antes de exportar."}</div>
    <p>Se incluyen solo las cuentas seleccionadas y activas, sus reglas activas, las contrasenas necesarias y los refresh tokens de Microsoft.</p>
    <p>Guarda los cambios y pulsa Exportar para crear la carpeta paquete_pi/ en este PC.</p>
    <button class="btn btn-primario" data-accion="exportar-pi" ${!seleccionadas.length || sucio() || Object.keys(S.errores).length ? "disabled" : ""}>Exportar para Raspberry Pi</button>
    ${sucio() ? `<p class="muted">Guarda los cambios antes de exportar.</p>` : ""}
    ${S.exportacion ? alerta("ok", `Carpeta creada: <code>${esc(S.exportacion.carpeta)}</code><br>${S.exportacion.cuentas.length} cuentas y ${S.exportacion.reglas} reglas. Abre LEEME.md dentro del paquete para instalarlo.`) : ""}
    </div><div class="card"><h2>Instalar y actualizar</h2>
    <p>Copia el paquete a una carpeta de transferencia en la Pi y ejecuta instalar_pi.py. No copies directamente sobre la instalacion.</p>
    <p>El instalador conserva el estado y los tokens ya renovados en la Pi. Deten el servicio antes de actualizar y reinicialo al terminar.</p>
    <p>Si una cuenta requiere iniciar sesion otra vez, detenla en la Pi, reconectala desde este PC, exporta e instala usando --renovar-token ID_CUENTA.</p>
    <p>El panel muestra el estado del lector del PC. El estado de la Pi se consulta en su registro de systemd.</p></div>`;
}

async function exportarPi() {
  if (sucio()) return toast("Guarda los cambios antes de exportar.", "warn");
  const { ok, data } = await api("/api/exportar-pi", {});
  if (!ok || !data.ok) return toast(data.mensaje || "No se pudo exportar.", "err", 7000);
  S.exportacion = data;
  toast("Paquete para Raspberry Pi creado.", "ok");
  render();
}

/* ----- Cuentas ----- */

function pintarCuentas() {
  if (S.editorCuenta) {
    const i = S.cfg.cuentas.findIndex(c => c.id === S.editorCuenta.id);
    if (i >= 0) return tarjetaCuenta(S.cfg.cuentas[i], i);
    S.editorCuenta = null;
  }
  const filas = S.cfg.cuentas.map((c, i) => {
    const est = estadoCuenta(c, i);
    const cred = S.base?.credenciales?.[c.id];
    return `<tr>
      <td class="cuenta-correo">${esc(c.correo || "Sin correo")}</td>
      <td>${esc(PROVEEDORES[c.proveedor] || "")}</td>
      <td>${c.raspberry_pi ? "Raspberry Pi" : "PC"}</td>
      <td><span class="chip ${est.clase}" data-estado-cuenta="${i}" title="${esc(est.title)}">${esc(est.texto)}</span></td>
      <td><div class="acciones acciones-cuenta">
        <button class="btn btn-pequeno btn-icono" data-accion="editar-cuenta" data-i="${i}" title="Editar cuenta" aria-label="Editar cuenta ${esc(c.correo)}">
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-pencil-line" aria-hidden="true"><path d="M12 20h9"></path><path d="M16.376 3.622a1 1 0 0 1 3.002 3.002L7.368 18.635a2 2 0 0 1-.855.506l-2.872.838a.5.5 0 0 1-.62-.62l.838-2.872a2 2 0 0 1 .506-.854z"></path><path d="m15 5 3 3"></path></svg>
        </button>
        ${c.auth === "oauth2" && !cred?.ok ? `<button class="btn btn-pequeno" data-accion="conectar" data-i="${i}">Conectar</button>` : ""}
        <button class="btn btn-fantasma btn-pequeno" data-accion="probar" data-i="${i}">Probar</button>
        <button class="btn btn-fantasma btn-pequeno btn-peligro" data-accion="borrar-cuenta" data-i="${i}" aria-label="Eliminar ${esc(c.correo)}">Eliminar</button>
      </div></td>
    </tr>`;
  }).join("");
  return `<div class="card cuentas-lista">
    <div class="card-cab"><div><h2>Cuentas de correo <span class="muted">(${S.cfg.cuentas.length})</span></h2>
    <small>Consulta el estado o edita una cuenta.</small></div>
    <button class="btn btn-primario" data-accion="nueva-cuenta">Nueva cuenta</button></div>
    ${filas ? `<div class="tabla-scroll"><table class="tabla-cuentas"><caption class="sr">Cuentas de correo y estado</caption>
      <thead><tr><th scope="col">Correo</th><th scope="col">Proveedor</th><th scope="col">Equipo</th><th scope="col">Estado</th><th scope="col">Acciones</th></tr></thead>
      <tbody>${filas}</tbody></table></div>` : vacio("Sin cuentas", "Agrega tu primera cuenta para configurar el reenvio.")}
  </div>`;
}

function abrirEditorCuenta(i, nueva = false) {
  if (S.editorCuenta) return;
  const c = S.cfg.cuentas[i];
  S.editorCuenta = { id: c.id, original: structuredClone(c), secreto: S.secretos[c.id], nueva };
}

function cancelarEditorCuenta() {
  const ed = S.editorCuenta;
  if (!ed || S.guardando) return;
  const i = S.cfg.cuentas.findIndex(c => c.id === ed.id);
  if (i >= 0) {
    if (ed.nueva) S.cfg.cuentas.splice(i, 1);
    else S.cfg.cuentas[i] = ed.original;
  }
  if (ed.secreto === undefined) delete S.secretos[ed.id];
  else S.secretos[ed.id] = ed.secreto;
  S.editorCuenta = null;
}

function nuevoIdCuenta() {
  let id;
  do { id = "cuenta_" + crypto.randomUUID().replace(/-/g, ""); }
  while (S.cfg.cuentas.some(c => c.id === id));
  return id;
}

function tarjetaCuenta(c, i) {
  const cred = (S.base && S.base.credenciales && S.base.credenciales[c.id]) || {};
  const est = estadoCuenta(c, i);
  const esOauth = c.auth === "oauth2";
  const conectando = S.oauth && S.oauth.id === c.id;

  let bloqueAuth = "";
  if (!esOauth) {
    bloqueAuth = campo({
      etiqueta: "Contrasena de aplicacion", ancho: true,
      control: `<input type="password" value="${esc(S.secretos[c.id] || "")}" autocomplete="new-password" data-ent="secreto" data-i="${i}"
                  placeholder="${cred.ok ? "Guardada. Escribe para reemplazarla" : "Pegala aqui"}">`,
      ayuda: "Se guarda en .env. Gmail: Cuenta de Google > Seguridad > Contrasenas de aplicacion.",
    });
  } else if (conectando) {
    const err = S.oauth.estado === "error";
    bloqueAuth = `<div class="ancho" style="grid-column:1/-1">${alerta(err ? "err" : "info", err
      ? `${esc(S.oauth.detalle)}<div class="mt"><button class="btn btn-pequeno" data-accion="cancelar-oauth">Cerrar</button></div>`
      : `Abre <a href="${esc(S.oauth.url)}" target="_blank" rel="noopener">${esc(S.oauth.url)}</a> e introduce este codigo:
         <div class="mt"><span class="codigo-box">${esc(S.oauth.codigo)}</span></div>
         <div class="mt"><button class="btn btn-pequeno" data-accion="copiar" data-texto="${esc(S.oauth.codigo)}">Copiar codigo</button>
         <button class="btn btn-fantasma btn-pequeno" data-accion="cancelar-oauth">Cancelar</button></div>
         <small class="muted">Esperando tu confirmacion…</small>`)}</div>`;
  } else if (cred.ok) {
    bloqueAuth = `<div style="grid-column:1/-1">${alerta("info", "Sesion guardada. Outlook usa Microsoft Graph; pulsa Probar en la tabla para comprobar acceso al buzon.")}
      <button class="btn btn-pequeno" data-accion="conectar" data-i="${i}">Volver a conectar</button></div>`;
  } else {
    bloqueAuth = `<div style="grid-column:1/-1">${alerta("warn", "Pendiente de conexion con Microsoft.")}
      <button class="btn btn-primario btn-pequeno" data-accion="conectar" data-i="${i}">Conectar con Microsoft</button></div>`;
  }

  return `
    <div class="card cuenta-editor">
      <div class="card-cab">
        <div><h2>${S.editorCuenta?.nueva ? "Nueva cuenta" : "Editar cuenta"}</h2><small>Configura el correo y guarda para volver a la tabla.</small></div>
        <span class="chip ${est.clase}" data-estado-cuenta="${i}" title="${esc(est.title)}">${esc(est.texto)}</span>
      </div>
      <div class="rejilla">
        ${campo({ etiqueta: "Correo", clave: `c${i}.correo`,
          control: `<input type="email" value="${esc(c.correo)}" data-ent="cuenta" data-i="${i}" data-campo="correo" autocomplete="off">` })}
        ${campo({ etiqueta: "Proveedor",
          control: `<select data-ent="cuenta" data-i="${i}" data-campo="proveedor">${opciones(Object.entries(PROVEEDORES), c.proveedor)}</select>` })}
        ${campo({ etiqueta: "Autenticacion", clave: `c${i}.auth`,
          control: `<select data-ent="cuenta" data-i="${i}" data-campo="auth">${opciones(Object.entries(AUTENTICACIONES).filter(([k]) => c.proveedor === "outlook" ? k === "oauth2" : k === "app_password"), c.auth)}</select>` })}
        ${bloqueAuth}
        <div style="grid-column:1/-1">${interruptor(`data-ent="cuenta" data-i="${i}" data-campo="activa"`, c.activa !== false, "Cuenta activa")}
        ${interruptor(`data-ent="cuenta" data-i="${i}" data-campo="raspberry_pi"`, !!c.raspberry_pi, "Usar en Raspberry Pi (excluir del lector del PC)")}</div>
      </div>
      <div class="acciones mt" style="justify-content:flex-end">
        <button class="btn" data-accion="cancelar-cuenta">Cancelar</button>
        <button class="btn btn-primario" data-accion="guardar-cuenta">Guardar cuenta</button>
      </div>
    </div>`;
}

/* ----- Reglas ----- */

function pintarReglas() {
  if (!S.cfg.cuentas.length) {
    return vacio("Primero agrega una cuenta", "Cada regla necesita una cuenta de origen.",
      `<div class="mt"><button class="btn btn-primario" data-accion="ir" data-vista="cuentas">Ir a cuentas</button></div>`);
  }
  if (S.editorRegla) {
    const i = S.cfg.reglas.findIndex(r => r.id === S.editorRegla.id);
    if (i >= 0) return tarjetaRegla(S.cfg.reglas[i], i);
    S.editorRegla = null;
  }
  const filas = S.cfg.reglas.map((r, i) => {
    const c = S.cfg.cuentas.find(c => c.id === r.cuenta);
    const destinos = (r.rutas || []).map(ru => ru.destino).filter(Boolean);
    return `<tr>
      <td class="cuenta-correo">${esc(r.nombre || "Sin nombre")}</td>
      <td>${esc(c?.correo || "Cuenta no disponible")}</td>
      <td>${esc(MODOS[r.modo]?.etiqueta || r.modo)}</td>
      <td title="${esc(destinos.join(", "))}">${destinos.length > 1 ? `${destinos.length} rutas` : esc(destinos[0] || "Sin destino")}</td>
      <td><span class="chip ${r.activa === false ? "neutro" : "ok"}">${r.activa === false ? "Pausada" : "Activa"}</span></td>
      <td><div class="acciones acciones-cuenta">
        <button class="btn btn-pequeno btn-icono" data-accion="editar-regla" data-i="${i}" title="Editar regla" aria-label="Editar regla ${esc(r.nombre)}"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-pencil-line" aria-hidden="true"><path d="M12 20h9"></path><path d="M16.376 3.622a1 1 0 0 1 3.002 3.002L7.368 18.635a2 2 0 0 1-.855.506l-2.872.838a.5.5 0 0 1-.62-.62l.838-2.872a2 2 0 0 1 .506-.854z"></path><path d="m15 5 3 3"></path></svg></button>
        <button class="btn btn-fantasma btn-pequeno btn-peligro" data-accion="borrar-regla" data-i="${i}" aria-label="Eliminar regla ${esc(r.nombre)}">Eliminar</button>
      </div></td>
    </tr>`;
  }).join("");
  return `<div class="card">
    <div class="card-cab"><div><h2>Reglas de reenvio <span class="muted">(${S.cfg.reglas.length})</span></h2><small>Consulta tus reglas o abre una para editarla.</small></div>
      <button class="btn btn-primario" data-accion="nueva-regla">Nueva regla</button></div>
    ${filas ? `<div class="tabla-scroll"><table class="tabla-cuentas tabla-reglas"><caption class="sr">Reglas de reenvio y estado</caption>
      <thead><tr><th scope="col">Nombre</th><th scope="col">Cuenta</th><th scope="col">Modo</th><th scope="col">Destino</th><th scope="col">Estado</th><th scope="col">Acciones</th></tr></thead>
      <tbody>${filas}</tbody></table></div>` : vacio("Sin reglas", "Pulsa Nueva regla para configurar el reenvio.")}
    </div>`;
}

function abrirEditorRegla(i, nueva = false) {
  if (S.editorRegla) return;
  const r = S.cfg.reglas[i];
  S.editorRegla = { id: r.id, original: structuredClone(r), nueva };
}

function cancelarEditorRegla() {
  if (!S.editorRegla || S.guardando) return;
  const ed = S.editorRegla;
  const i = S.cfg.reglas.findIndex(r => r.id === ed.id);
  if (i >= 0) {
    if (ed.nueva) S.cfg.reglas.splice(i, 1);
    else S.cfg.reglas[i] = ed.original;
  }
  S.editorRegla = null;
}

function tarjetaRegla(r, i) {
  const modo = r.modo || "todo";
  const rutas = r.rutas || [];
  const cuentaOpts = [["", "Elige una cuenta"], ...S.cfg.cuentas.map(c => [c.id, c.correo || "Sin correo"])];
  const segmento = `<div class="seg" role="group" aria-label="Tipo de regla">${Object.entries(MODOS).map(([v, m]) =>
    `<button type="button" data-accion="modo" data-i="${i}" data-valor="${v}" aria-pressed="${v === modo}">${esc(m.etiqueta)}</button>`).join("")}</div>`;

  const bloqueRutas = rutas.map((ru, j) => tarjetaRuta(ru, i, j, modo, rutas.length)).join("");

  return `
    <div class="card regla-editor">
      <div class="card-cab">
        <div><h2>${S.editorRegla?.nueva ? "Nueva regla" : "Editar regla"}</h2><small>Configura el reenvio y guarda para volver a la tabla.</small></div>
        <div class="acciones">
          ${interruptor(`data-ent="regla" data-i="${i}" data-campo="activa"`, r.activa !== false, "Activa")}
        </div>
      </div>
      <div class="rejilla">
        ${campo({ etiqueta: "Nombre", clave: `r${i}.nombre`, control: `<input type="text" value="${esc(r.nombre)}" data-ent="regla" data-i="${i}" data-campo="nombre" placeholder="Facturas a contabilidad">` })}
        ${campo({ etiqueta: "Cuenta de origen", clave: `r${i}.cuenta`, control: `<select data-ent="regla" data-i="${i}" data-campo="cuenta">${opciones(cuentaOpts, r.cuenta)}</select>` })}
        ${campo({ etiqueta: "Carpeta", ayuda: "Por defecto, la bandeja de entrada.", control: `<input type="text" value="${esc(r.carpeta || "")}" data-ent="regla" data-i="${i}" data-campo="carpeta" placeholder="INBOX">` })}
        ${campo({ etiqueta: "Limite por ciclo", clave: `r${i}.limite`, ayuda: "Vacio = valor general de Ajustes.",
          control: `<input type="number" min="1" max="500" value="${esc(r.limite ?? "")}" data-ent="regla" data-i="${i}" data-campo="limite">` })}
        <div class="campo ancho" style="gap:10px">
          ${interruptor(`data-ent="regla" data-i="${i}" data-campo="procesar_existentes"`, !!r.procesar_existentes, "Reenviar tambien los correos que ya estan en la bandeja")}
          <small>Por defecto solo se reenvian los correos que lleguen despues de crear la regla. Activalo con cuidado.</small>
          ${interruptor(`data-ent="regla" data-i="${i}" data-campo="marcar_leido"`, !!r.marcar_leido, "Marcar como leido al reenviar")}
        </div>
      </div>
      <div class="mt"><span class="campo" style="margin-bottom:8px"><span>Que hacer con los correos</span></span>${segmento}</div>
      ${bloqueRutas}
      ${modo === "rutas" ? `<div class="mt acciones"><button class="btn btn-pequeno" data-accion="nueva-ruta" data-i="${i}">+ Agregar ruta</button>
        <small class="muted">Pon una ruta sin filtros al final para atrapar el resto.</small></div>` : ""}
      <div class="acciones mt editor-pie">
        <button class="btn" data-accion="cancelar-regla">Cancelar</button>
        <button class="btn btn-primario" data-accion="guardar-regla">Guardar regla</button>
      </div>
    </div>`;
}

function tarjetaRuta(ru, i, j, modo, total) {
  const f = ru.filtros || {};
  const destino = campo({ etiqueta: "Reenviar a", clave: `r${i}.ruta${j}.destino`,
    control: `<input type="email" value="${esc(ru.destino)}" data-ent="ruta" data-i="${i}" data-j="${j}" data-campo="destino" placeholder="destino@correo.com">` });

  if (modo === "todo") {
    return `<div class="ruta"><div class="rejilla">${destino}</div></div>`;
  }

  const filtros = `
    ${campo({ etiqueta: "Remitente contiene", control: `<input type="text" value="${esc(f.desde)}" data-ent="ruta" data-i="${i}" data-j="${j}" data-campo="filtros.desde">` })}
    ${campo({ etiqueta: "Asunto contiene", control: `<input type="text" value="${esc(f.asunto)}" data-ent="ruta" data-i="${i}" data-j="${j}" data-campo="filtros.asunto">` })}
    ${campo({ etiqueta: "Texto contiene", ayuda: "Busca en el asunto y en el inicio del cuerpo.", control: `<input type="text" value="${esc(f.contiene)}" data-ent="ruta" data-i="${i}" data-j="${j}" data-campo="filtros.contiene">` })}
    ${campo({ etiqueta: "Solo ultimos N dias", clave: `r${i}.ruta${j}.dias`, control: `<input type="number" min="0" value="${esc(f.dias ?? "")}" data-ent="ruta" data-i="${i}" data-j="${j}" data-campo="filtros.dias">` })}
    <div class="campo">${interruptor(`data-ent="ruta" data-i="${i}" data-j="${j}" data-campo="filtros.solo_no_leidos"`, !!f.solo_no_leidos, "Solo no leidos")}</div>`;

  if (modo === "filtro") {
    return `<div class="ruta"><div class="rejilla">${destino}${filtros}</div></div>`;
  }

  const acciones = `
    <div class="ruta-acciones">
      <button class="btn btn-fantasma btn-pequeno" data-accion="mover-ruta" data-i="${i}" data-j="${j}" data-dir="-1" aria-label="Subir ruta" ${j === 0 ? "disabled" : ""}>↑</button>
      <button class="btn btn-fantasma btn-pequeno" data-accion="mover-ruta" data-i="${i}" data-j="${j}" data-dir="1" aria-label="Bajar ruta" ${j === total - 1 ? "disabled" : ""}>↓</button>
      <button class="btn btn-fantasma btn-pequeno btn-peligro" data-accion="borrar-ruta" data-i="${i}" data-j="${j}" aria-label="Eliminar ruta">Quitar</button>
    </div>`;
  return `
    <div class="ruta">
      <div class="ruta-cab"><b>Ruta ${j + 1}: ${esc(ru.nombre || "sin nombre")}</b>${acciones}</div>
      <div class="rejilla">
        ${campo({ etiqueta: "Nombre de la ruta", clave: `r${i}.ruta${j}.nombre`, control: `<input type="text" value="${esc(ru.nombre)}" data-ent="ruta" data-i="${i}" data-j="${j}" data-campo="nombre" placeholder="Clientes">` })}
        ${destino}
        ${filtros}
      </div>
    </div>`;
}

/* ----- Ajustes ----- */

function pintarAjustes() {
  const A = S.cfg.ajustes || {};
  const D = S.base.defectos;
  const filas = AJUSTES.map(([k, etiqueta, ayuda, min, max]) => campo({
    etiqueta, ayuda: `${ayuda} (${min}–${max})`, clave: `ajuste.${k}`,
    control: `<input type="number" min="${min}" max="${max}" value="${esc(A[k] ?? "")}" placeholder="${esc(D[k])}" data-ent="ajuste" data-campo="${k}">`,
  })).join("");
  return `
    <div class="card">
      <div class="card-cab"><div><h2>Ajustes del servicio</h2>
        <small>Valores generales. Los cambios se aplican solos al guardar.</small></div>
        <button class="btn btn-pequeno" data-accion="restaurar-ajustes">Restaurar recomendados</button></div>
      <div class="rejilla">${filas}</div>
    </div>
    ${alerta("info", "Pensado para una Raspberry Pi Zero: sin base de datos, correos descargados por partes y escrituras en disco solo cuando algo cambia.")}`;
}

/* ----- Registro ----- */

function pintarRegistro() {
  if (!S.registro.length) return `<span class="muted">Sin actividad todavia.</span>`;
  return S.registro.map(l => {
    const cls = /ERROR/.test(l) ? "e" : /WARNING/.test(l) ? "w" : /INFO/.test(l) ? "i" : "";
    return `<span class="${cls}">${esc(l)}</span>`;
  }).join("\n");
}

function pintarRegistroVista() {
  return `
    <div class="card">
      <div class="card-cab"><div><h2>Registro del servicio</h2><small>Se actualiza solo. Muestra las ultimas lineas.</small></div>
        <button class="btn btn-pequeno" data-accion="actualizar-registro">Actualizar</button></div>
      <div class="registro" id="registro-caja" tabindex="0" aria-label="Registro">${pintarRegistro()}</div>
    </div>`;
}

/* ------------------------------------------------------------ eventos */

function onCampo(e) {
  const el = e.target;
  const ent = el.dataset.ent;
  if (!ent) return;
  const valor = el.type === "checkbox" ? el.checked : el.value;
  const i = Number(el.dataset.i);
  const j = Number(el.dataset.j);
  const campoDatos = el.dataset.campo;
  let estructural = el.type === "checkbox" || el.tagName === "SELECT";

  switch (ent) {
    case "secreto": {
      const cid = S.cfg.cuentas[i].id;
      if (valor) S.secretos[cid] = valor; else delete S.secretos[cid];
      break;
    }
    case "cuenta": {
      const c = S.cfg.cuentas[i];
      c[campoDatos] = valor;
      if (campoDatos === "proveedor") c.auth = valor === "outlook" ? "oauth2" : "app_password";
      if (campoDatos === "auth" || campoDatos === "proveedor") estructural = true;
      break;
    }
    case "regla": {
      const r = S.cfg.reglas[i];
      if (campoDatos === "limite") {
        if (valor === "") delete r.limite; else r.limite = Number(valor);
      } else if (campoDatos === "carpeta") {
        if (valor.trim()) r.carpeta = valor.trim(); else delete r.carpeta;
      } else {
        r[campoDatos] = valor;
      }
      break;
    }
    case "ruta": {
      const ru = S.cfg.reglas[i].rutas[j];
      ru.filtros = ru.filtros || {};
      if (campoDatos === "nombre" || campoDatos === "destino") {
        ru[campoDatos] = valor;
      } else if (campoDatos.startsWith("filtros.")) {
        const k = campoDatos.slice("filtros.".length);
        if (el.type === "checkbox") {
          if (valor) ru.filtros[k] = true; else delete ru.filtros[k];
        } else if (valor === "") {
          delete ru.filtros[k];
        } else {
          ru.filtros[k] = k === "dias" ? Number(valor) : valor;
        }
      }
      break;
    }
    case "ajuste": {
      S.cfg.ajustes = S.cfg.ajustes || {};
      if (valor === "") delete S.cfg.ajustes[campoDatos];
      else S.cfg.ajustes[campoDatos] = Number(valor);
      break;
    }
    default:
      return;
  }
  if (estructural) render(); else tocar();
}

async function guardar() {
  if (S.guardando) return;
  if (Object.keys(S.errores).length) return toast("Corrige los campos marcados en rojo.", "err");
  S.guardando = true;
  actualizarBotones();
  const { ok, data } = await api("/api/config", {
    config: S.cfg,
    secretos_cuentas: S.secretos,
  });
  S.guardando = false;
  if (!ok) {
    toast((data.errores || [data.mensaje || "No se pudo guardar."]).join(" "), "err", 7000);
    actualizarBotones();
    return;
  }
  S.base = data.vista;
  S.cfg = structuredClone(data.vista.config);
  S.editorCuenta = null;
  S.editorRegla = null;
  S.marca = JSON.stringify(S.cfg);
  S.secretos = {};
  (data.avisos || []).forEach(a => toast(a, "warn", 6000));
  toast("Cambios guardados.", "ok");
  render();
}

function descartar() {
  if (S.guardando) return;
  S.editorCuenta = null;
  S.editorRegla = null;
  S.cfg = structuredClone(S.base.config);
  S.secretos = {};
  S.marca = JSON.stringify(S.cfg);
  toast("Cambios descartados.", "info");
  render();
}

async function probar(i) {
  if (sucio()) return toast("Guarda los cambios antes de probar la conexion.", "warn");
  const id = S.cfg.cuentas[i].id;
  toast("Probando conexion…", "info", 2500);
  const { data } = await api("/api/probar", { id });
  toast(data.mensaje || (data.ok ? "Conexion correcta." : "Error de conexion."), data.ok ? "ok" : "err", 6000);
}

async function conectarMicrosoft(i) {
  if (sucio()) return toast("Guarda los cambios antes de conectar la cuenta.", "warn");
  const id = S.cfg.cuentas[i].id;
  const { ok, data } = await api("/api/oauth/iniciar", { id });
  if (!ok || !data.ok) return toast(data.mensaje || "No se pudo iniciar la conexion.", "err", 6000);
  abrirEditorCuenta(i);
  S.oauth = { id, codigo: data.codigo, url: data.url, estado: "pendiente", detalle: data.detalle };
  render();
  sondearOauth();
}

function sondearOauth() {
  clearInterval(S.sondeoOauth);
  S.sondeoOauth = setInterval(async () => {
    if (!S.oauth) return clearInterval(S.sondeoOauth);
    const { data } = await api(`/api/oauth/estado?id=${encodeURIComponent(S.oauth.id)}`);
    if (!S.oauth) return;
    if (data.estado === "ok") {
      clearInterval(S.sondeoOauth);
      S.oauth = null;
      toast("Cuenta conectada con Microsoft.", "ok");
      await cargarConfig();
    } else if (data.estado === "error") {
      clearInterval(S.sondeoOauth);
      S.oauth.estado = "error";
      S.oauth.detalle = data.detalle;
      render();
    }
  }, 3000);
}

async function alternarServicio() {
  const accion = S.servicio.activo ? "detener" : "iniciar";
  const { data } = await api("/api/servicio", { accion });
  toast(data.mensaje || "Listo.", "info");
  setTimeout(cargarEstado, 1500);
}

function nuevaRuta(modo) {
  return modo === "todo" ? { nombre: "Todo el correo", destino: "", filtros: {} }
    : modo === "filtro" ? { nombre: "Filtro", destino: "", filtros: {} }
    : { nombre: "", destino: "", filtros: {} };
}

function onClick(e) {
  const b = e.target.closest("[data-accion]");
  if (!b || b.disabled) return;
  const a = b.dataset.accion;
  const i = Number(b.dataset.i);
  const j = Number(b.dataset.j);

  switch (a) {
    case "ir":
      return ir(b.dataset.vista);
    case "exportar-pi":
      return exportarPi();
    case "editar-cuenta":
      abrirEditorCuenta(i);
      break;
    case "cancelar-cuenta":
      cancelarEditorCuenta();
      break;
    case "guardar-cuenta":
      if (!sucio()) { S.editorCuenta = null; render(); return; }
      return guardar();
    case "nueva-cuenta":
      S.cfg.cuentas.push({ id: nuevoIdCuenta(), correo: "", proveedor: "outlook", auth: "oauth2", activa: true, raspberry_pi: false });
      abrirEditorCuenta(S.cfg.cuentas.length - 1, true);
      break;
    case "borrar-cuenta": {
      const id = S.cfg.cuentas[i].id || "esta cuenta";
      if (!confirm(`¿Eliminar ${S.cfg.cuentas[i].correo || "esta cuenta"}? Las reglas que la usan quedaran sin cuenta de origen.`)) return;
      S.cfg.cuentas.splice(i, 1);
      delete S.secretos[id];
      if (S.oauth && S.oauth.id === id) S.oauth = null;
      break;
    }
    case "probar":
      return probar(i);
    case "conectar":
      return conectarMicrosoft(i);
    case "cancelar-oauth":
      clearInterval(S.sondeoOauth);
      S.oauth = null;
      break;
    case "copiar":
      navigator.clipboard?.writeText(b.dataset.texto).then(() => toast("Codigo copiado.", "ok", 2000), () => toast("No se pudo copiar.", "warn"));
      return;
    case "editar-regla":
      abrirEditorRegla(i);
      break;
    case "cancelar-regla":
      cancelarEditorRegla();
      break;
    case "guardar-regla":
      if (!sucio()) { S.editorRegla = null; render(); return; }
      return guardar();
    case "nueva-regla": {
      if (S.editorRegla) return;
      let id;
      do { id = "regla_" + crypto.randomUUID().replace(/-/g, ""); }
      while (S.cfg.reglas.some(r => r.id === id));
      S.cfg.reglas.push({ id, nombre: "", cuenta: S.cfg.cuentas[0]?.id || "", carpeta: "INBOX", modo: "todo",
        rutas: [nuevaRuta("todo")], limite: undefined, marcar_leido: false, procesar_existentes: false, activa: true });
      abrirEditorRegla(S.cfg.reglas.length - 1, true);
      break;
    }
    case "borrar-regla": {
      const nombre = S.cfg.reglas[i].nombre || "esta regla";
      if (!confirm(`¿Eliminar la regla "${nombre}"?`)) return;
      S.cfg.reglas.splice(i, 1);
      break;
    }
    case "modo": {
      const r = S.cfg.reglas[i];
      const valor = b.dataset.valor;
      if (r.modo === valor) return;
      const primera = (r.rutas && r.rutas[0]) || {};
      if (valor === "todo") r.rutas = [{ nombre: "Todo el correo", destino: primera.destino || "", filtros: {} }];
      else if (valor === "filtro") r.rutas = [{ nombre: "Filtro", destino: primera.destino || "", filtros: { ...(primera.filtros || {}) } }];
      else if (!r.rutas || !r.rutas.length) r.rutas = [nuevaRuta("rutas")];
      r.modo = valor;
      break;
    }
    case "nueva-ruta":
      S.cfg.reglas[i].rutas.push({ ...nuevaRuta("rutas"), nombre: "Ruta " + (S.cfg.reglas[i].rutas.length + 1) });
      break;
    case "borrar-ruta": {
      const rutas = S.cfg.reglas[i].rutas;
      rutas.splice(j, 1);
      if (!rutas.length) rutas.push(nuevaRuta("rutas"));
      break;
    }
    case "mover-ruta": {
      const rutas = S.cfg.reglas[i].rutas;
      const k = j + Number(b.dataset.dir);
      if (k < 0 || k >= rutas.length) return;
      [rutas[j], rutas[k]] = [rutas[k], rutas[j]];
      break;
    }
    case "restaurar-ajustes":
      S.cfg.ajustes = {};
      break;
    case "actualizar-registro":
      return cargarRegistro();
    default:
      return;
  }
  render();
}

function ir(vista) {
  if (!VISTAS[vista]) return;
  S.vista = vista;
  history.replaceState(null, "", "#" + vista);
  render();
  if (vista === "registro") cargarRegistro();
  $("#vista").focus({ preventScroll: true });
}

/* ------------------------------------------------------------ utilidades UI */

function toast(texto, tipo = "info", ms = 4500) {
  const el = document.createElement("div");
  el.className = `toast ${tipo}`;
  el.setAttribute("role", tipo === "err" ? "alert" : "status");
  el.textContent = texto;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}

const TEMAS = ["sistema", "claro", "oscuro"];
function aplicarTema(t) {
  const raiz = document.documentElement;
  if (t === "sistema") raiz.removeAttribute("data-theme");
  else raiz.setAttribute("data-theme", t === "claro" ? "light" : "dark");
  $("#btn-tema").title = "Tema: " + t;
}
function cambiarTema() {
  let actual = "sistema";
  try { actual = localStorage.getItem("tema") || "sistema"; } catch (e) { /* almacenamiento no disponible */ }
  const siguiente = TEMAS[(TEMAS.indexOf(actual) + 1) % TEMAS.length];
  try { localStorage.setItem("tema", siguiente); } catch (e) { /* ignorar */ }
  aplicarTema(siguiente);
}

/* ------------------------------------------------------------ arranque */

function iniciar() {
  let tema = "sistema";
  try { tema = localStorage.getItem("tema") || "sistema"; } catch (e) { /* ignorar */ }
  aplicarTema(tema);

  const hash = location.hash.slice(1);
  if (VISTAS[hash]) S.vista = hash;

  $("#vista").addEventListener("input", onCampo);
  $("#vista").addEventListener("change", onCampo);
  document.addEventListener("click", e => {
    if (e.target.closest("[data-accion]")) onClick(e);
    else if (e.target.closest(".nav button[data-vista]")) ir(e.target.closest(".nav button").dataset.vista);
  });
  $("#btn-guardar").addEventListener("click", guardar);
  $("#btn-descartar").addEventListener("click", descartar);
  $("#btn-servicio").addEventListener("click", alternarServicio);
  $("#btn-tema").addEventListener("click", cambiarTema);
  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      if (!$("#btn-guardar").disabled) guardar();
    }
  });
  window.addEventListener("beforeunload", e => {
    if (sucio()) { e.preventDefault(); e.returnValue = ""; }
  });

  cargarConfig().then(() => {
    cargarEstado();
    if (S.vista === "registro") cargarRegistro();
  });
  setInterval(cargarEstado, 5000);
  setInterval(() => { if (S.vista === "registro") cargarRegistro(); }, 4000);
}

iniciar();
