/* ANÁLISIS POSTPARTIDO — versión móvil (pestaña "Análisis")
 * Árbol de preguntas por fases (static/data/arbol_analisis_f11.json) + checklist de fallos por líneas.
 * Respuestas: datos.respuestas[idPregunta] = idOpcion | [idOpciones]
 *             datos.respuestas[idPregunta + '>' + idOpcion] = sub-respuesta (idOpcion | [idOpciones])
 *             datos.checklist[idLinea + '.ofe' | '.def'] = [idItems]
 * Requiere las globales de movil.html: EQUIPO, EQUIPO_FORMATO, toast().
 */
(function () {
    const ANA = {
        iniciado: false, arbol: null, vista: 'lista', paso: 0,
        lista: null, partidos: null, a: null, dirty: false,
        fase: {}, cerradas: new Set(), ocupado: false,
        veo: { estado: null, para: '', grab: [], sugerido: null }, veoDatos: {}
    };
    const VEO_ID_OK = id => /^[0-9a-f-]{36}$/.test(id || '');
    const BORRADOR = 'ana_pp_borrador_' + (typeof EQUIPO !== 'undefined' ? EQUIPO : '');
    const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const hoyIso = () => { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); };
    const fechaTxt = iso => { const p = (iso || '').split('-'); return p.length === 3 ? `${p[2]}/${p[1]}/${p[0]}` : (iso || ''); };
    const el = () => document.getElementById('anaApp');
    const esF7 = () => typeof EQUIPO_FORMATO !== 'undefined' && EQUIPO_FORMATO === 'F7';

    // ───── estilos ─────
    const css = `
    #anaApp{padding-bottom:90px;}
    .ana-card{background:#fff;border:1px solid var(--border);border-radius:12px;margin-bottom:10px;}
    .ana-btn{border:none;border-radius:10px;padding:12px 14px;font-family:inherit;font-size:11px;font-weight:800;letter-spacing:.6px;text-transform:uppercase;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:7px;}
    .ana-btn-red{background:var(--red);color:#fff;} .ana-btn-dark{background:#1e293b;color:#fff;} .ana-btn-light{background:#f1f5f9;color:#475569;}
    .ana-btn:disabled{opacity:.5;}
    .ana-item{display:flex;align-items:center;gap:10px;padding:12px 14px;border-top:1px solid var(--border);cursor:pointer;}
    .ana-item:first-child{border-top:none;}
    .ana-cab{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:12px;}
    .ana-cab label{font-size:9px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:var(--soft);display:block;margin-bottom:3px;}
    .ana-cab input{width:100%;box-sizing:border-box;border:1.5px solid var(--border);border-radius:8px;padding:9px;font-size:13px;font-family:inherit;font-weight:700;color:var(--text);background:#fff;}
    .ana-pasos{display:flex;gap:6px;overflow-x:auto;padding:2px 2px 10px;scrollbar-width:none;-webkit-overflow-scrolling:touch;}
    .ana-pasos::-webkit-scrollbar{display:none;}
    .ana-paso{flex-shrink:0;border:1.5px solid var(--border);background:#fff;border-radius:20px;padding:7px 12px;font-size:10px;font-weight:800;color:#475569;cursor:pointer;font-family:inherit;white-space:nowrap;display:flex;align-items:center;gap:6px;}
    .ana-paso.act{background:#1e293b;border-color:#1e293b;color:#fff;}
    .ana-paso .n{display:inline-flex;min-width:16px;height:16px;border-radius:8px;align-items:center;justify-content:center;font-size:9px;background:#e2e8f0;color:#475569;padding:0 4px;}
    .ana-paso.act .n{background:rgba(255,255,255,.25);color:#fff;}
    .ana-paso .n.ok{background:#16a34a;color:#fff;}
    .ana-bloque-tit{font-size:15px;font-weight:900;color:var(--text);margin:4px 2px 2px;}
    .ana-bloque-sub{font-size:11px;font-weight:600;color:var(--soft);margin:0 2px 10px;}
    .ana-sec>summary{list-style:none;cursor:pointer;padding:12px 14px;font-size:12px;font-weight:900;color:var(--red);text-transform:uppercase;letter-spacing:.4px;display:flex;align-items:center;justify-content:space-between;gap:8px;}
    .ana-sec>summary::-webkit-details-marker{display:none;}
    .ana-sec>summary .cnt{font-size:10px;color:var(--soft);font-weight:800;white-space:nowrap;}
    .ana-sec[open]>summary{border-bottom:1px solid var(--border);}
    .ana-preg{padding:12px 14px;border-top:1px solid #f1f5f9;}
    .ana-preg:first-of-type{border-top:none;}
    .ana-preg-t{font-size:12.5px;font-weight:800;color:var(--text);line-height:1.35;margin-bottom:8px;}
    .ana-preg-t small{display:block;font-size:10px;font-weight:700;color:var(--soft);margin-top:2px;text-transform:uppercase;letter-spacing:.5px;}
    .ana-op{display:flex;gap:10px;align-items:flex-start;width:100%;box-sizing:border-box;text-align:left;border:1.5px solid var(--border);background:#fff;border-radius:10px;padding:10px 12px;margin-bottom:6px;cursor:pointer;font-family:inherit;font-size:12px;font-weight:600;color:#334155;line-height:1.35;}
    .ana-op .mk{flex-shrink:0;width:18px;height:18px;border:2px solid #cbd5e1;border-radius:50%;margin-top:1px;display:flex;align-items:center;justify-content:center;font-size:11px;color:#fff;}
    .ana-op.multi .mk{border-radius:5px;}
    .ana-op.sel{border-color:var(--red);background:#fff1f2;color:var(--text);font-weight:700;}
    .ana-op.sel .mk{background:var(--red);border-color:var(--red);}
    .ana-op .ds{display:block;font-size:11px;font-weight:500;color:var(--soft);margin-top:2px;}
    .ana-sub{margin:-2px 0 8px 14px;padding:10px 0 4px 12px;border-left:3px solid #fecdd3;}
    .ana-sub-t{font-size:10px;font-weight:900;color:var(--red);text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px;}
    .ana-seg{display:flex;background:#f1f5f9;border-radius:9px;padding:3px;gap:3px;margin:0 14px 10px;}
    .ana-seg button{flex:1;border:none;background:none;border-radius:7px;padding:8px;font-family:inherit;font-size:11px;font-weight:800;color:#64748b;cursor:pointer;}
    .ana-seg button.act{background:#fff;color:var(--text);box-shadow:0 1px 3px rgba(0,0,0,.12);}
    .ana-linea-h{display:flex;align-items:center;justify-content:space-between;padding:12px 14px 10px;font-size:13px;font-weight:900;color:var(--text);}
    .ana-linea-h .cnt{font-size:10px;font-weight:800;color:#fff;background:var(--red);border-radius:10px;padding:2px 8px;}
    .ana-linea-h .cnt.cero{background:#e2e8f0;color:#64748b;}
    .ana-chips{padding:0 14px 8px;}
    .ana-nav{display:flex;gap:8px;margin:4px 0 12px;}
    .ana-nav .ana-btn{flex:1;}
    .ana-bar{display:none;position:fixed;left:0;right:0;bottom:clamp(62px,17.9vw,78px);background:rgba(255,255,255,.97);border-top:1px solid var(--border);padding:8px 12px;gap:8px;z-index:190;box-shadow:0 -4px 14px rgba(0,0,0,.06);}
    .ana-bar .ana-btn{flex:1;padding:12px 8px;}
    .ana-informe{padding:14px;font-size:12.5px;line-height:1.5;color:#334155;}
    .ana-informe{font-size:12.5px;line-height:1.4;}
    .ana-informe h2,.ana-informe h3{background:#1e293b;color:#fff;font-size:12px;font-weight:900;text-transform:uppercase;letter-spacing:.4px;padding:8px 10px;border-radius:8px;margin:16px 0 8px;}
    .ana-informe h2:first-child,.ana-informe h3:first-child{margin-top:0;}
    .ana-informe h4{font-size:12.5px;font-weight:900;color:var(--red);margin:12px 0 6px;}
    .ana-informe ul{list-style:none;padding:0;margin:0 0 8px;}
    .ana-informe ul li{background:#f8fafc;border-left:3px solid var(--red);border-radius:6px;padding:8px 10px;margin-bottom:6px;}
    .ana-informe ol{list-style:none;counter-reset:foco;padding:0;margin:0 0 8px;}
    .ana-informe ol li{counter-increment:foco;position:relative;background:#fff1f2;border-radius:8px;padding:8px 10px 8px 38px;margin-bottom:6px;}
    .ana-informe ol li::before{content:counter(foco);position:absolute;left:10px;top:8px;width:20px;height:20px;border-radius:50%;background:var(--red);color:#fff;font-size:11px;font-weight:900;display:flex;align-items:center;justify-content:center;}
    .ana-informe p{margin:4px 0 8px;}
    .ana-vacio{padding:26px 16px;text-align:center;color:var(--soft);font-size:12px;font-weight:600;}
    .ana-spin{display:inline-block;width:14px;height:14px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:anaspin .7s linear infinite;}
    @keyframes anaspin{to{transform:rotate(360deg)}}
    `;
    const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);

    // ───── datos ─────
    function nuevoAnalisis() {
        return { id: '', fecha: '', rival: '', resultado: '', datos: { respuestas: {}, checklist: {}, observaciones: '' }, informe_html: '' };
    }
    function guardarBorrador() {
        try { localStorage.setItem(BORRADOR, JSON.stringify({ a: ANA.a, dirty: ANA.dirty, paso: ANA.paso })); } catch (e) {}
    }
    function borrarBorrador() { try { localStorage.removeItem(BORRADOR); } catch (e) {} }
    function cambio() { ANA.dirty = true; guardarBorrador(); }

    async function cargarArbol() {
        if (ANA.arbol) return;
        const r = await fetch('/static/data/arbol_analisis_f11.json?v=1');
        ANA.arbol = await r.json();
    }
    async function cargarLista() {
        try {
            const r = await fetch('/api/analisis_pp/lista?equipo=' + encodeURIComponent(EQUIPO));
            const d = await r.json();
            ANA.lista = d.status === 'ok' ? d.analisis : [];
        } catch (e) { ANA.lista = []; }
    }
    async function cargarPartidos() {
        if (ANA.partidos) return;
        try {
            const r = await fetch('/api/analisis_pp/partidos?equipo=' + encodeURIComponent(EQUIPO));
            const d = await r.json();
            ANA.partidos = d.status === 'ok' ? d.partidos : [];
        } catch (e) { ANA.partidos = []; }
    }
    function partidoDe(fecha) {
        return (ANA.partidos || []).find(p => p.fecha === fecha);
    }

    // ───── pasos ─────
    function pasos() {
        const bl = (ANA.arbol ? ANA.arbol.bloques : []).map((b, i) => ({ tipo: 'bloque', b, corto: `${i + 1}. ${b.titulo}` }));
        return bl.concat([{ tipo: 'chk', corto: `${bl.length + 1}. Fallos por líneas` }, { tipo: 'inf', corto: 'Informe' }]);
    }
    function respondida(p) {
        const v = ANA.a.datos.respuestas[p.id];
        return Array.isArray(v) ? v.length > 0 : !!v;
    }
    function contarBloque(b) {
        let tot = 0, ok = 0;
        b.secciones.forEach(s => s.preguntas.forEach(p => { tot++; if (respondida(p)) ok++; }));
        return { tot, ok };
    }
    function contarChecklist() {
        return Object.values(ANA.a.datos.checklist).reduce((n, v) => n + (v || []).length, 0);
    }

    // ───── render ─────
    function render() {
        const app = el();
        if (!app) return;
        if (esF7()) {
            app.innerHTML = `<div class="ana-card ana-vacio"><div style="font-size:28px;margin-bottom:8px;">⚽</div>
                El análisis post partido para <b>fútbol 7</b> está en preparación.<br>Muy pronto estará disponible para ${esc(EQUIPO)}.</div>`;
            barra(false);
            return;
        }
        if (!ANA.arbol || ANA.lista === null) {
            app.innerHTML = '<div class="ana-card ana-vacio"><span class="ana-spin"></span> Cargando…</div>';
            barra(false);
            return;
        }
        if (ANA.vista === 'lista') renderLista(); else renderEditor();
    }

    function renderLista() {
        const items = (ANA.lista || []).map(a => `
            <div class="ana-item" data-act="abrir" data-id="${esc(a.id)}">
                <div style="flex:1;min-width:0;">
                    <div style="font-size:13px;font-weight:900;color:var(--text);">${esc(fechaTxt(a.fecha))} · ${esc(a.rival || 'Sin rival')}</div>
                    <div style="font-size:11px;font-weight:600;color:var(--soft);margin-top:2px;">${a.resultado ? 'Resultado ' + esc(a.resultado) + ' · ' : ''}${esc(a.autor || '')}</div>
                </div>
                ${a.tiene_informe ? '<span style="font-size:9px;font-weight:900;color:#16a34a;background:#f0fdf4;border-radius:10px;padding:3px 8px;">IA ✓</span>' : ''}
                <span style="color:var(--soft);font-size:18px;">›</span>
            </div>`).join('');
        el().innerHTML = `
            <button class="ana-btn ana-btn-red" style="width:100%;padding:14px;margin-bottom:12px;" data-act="nuevo">＋ Nuevo análisis</button>
            <div class="sl">Análisis guardados · ${esc(EQUIPO)}</div>
            <div class="ana-card">${items || '<div class="ana-vacio">Todavía no hay análisis de este equipo.</div>'}</div>`;
        barra(false);
    }

    function renderEditor() {
        const a = ANA.a, ps = pasos();
        ANA.paso = Math.min(ANA.paso, ps.length - 1);
        const chips = ps.map((p, i) => {
            let n = '';
            if (p.tipo === 'bloque') { const c = contarBloque(p.b); n = `<span class="n ${c.ok ? 'ok' : ''}">${c.ok}/${c.tot}</span>`; }
            if (p.tipo === 'chk') { const c = contarChecklist(); n = `<span class="n ${c ? 'ok' : ''}">${c}</span>`; }
            if (p.tipo === 'inf' && a.informe_html) n = '<span class="n ok">✓</span>';
            return `<button class="ana-paso ${i === ANA.paso ? 'act' : ''}" data-act="paso" data-i="${i}">${esc(p.corto)} ${n}</button>`;
        }).join('');
        const p = ps[ANA.paso];
        let cuerpo = '';
        if (p.tipo === 'bloque') cuerpo = renderBloque(p.b);
        else if (p.tipo === 'chk') cuerpo = renderChecklist();
        else cuerpo = renderInforme();

        el().innerHTML = `
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
                <button class="ana-btn ana-btn-light" style="padding:8px 12px;" data-act="volver">‹ Lista</button>
                <div style="flex:1;font-size:11px;font-weight:800;color:${ANA.dirty ? '#d97706' : '#16a34a'};text-align:right;">
                    ${ANA.dirty ? '● Sin guardar' : (a.id ? '✓ Guardado' : '')}</div>
            </div>
            <div class="ana-card ana-cab">
                <div><label>Fecha del partido</label><input type="date" id="anaFecha" value="${esc(a.fecha)}"></div>
                <div><label>Resultado</label><input id="anaResultado" placeholder="Ej. 2 - 1" value="${esc(a.resultado)}"></div>
                <div style="grid-column:1/-1;"><label>Rival</label><input id="anaRival" placeholder="Rival" value="${esc(a.rival)}"></div>
                <div style="grid-column:1/-1;" id="anaVeoCampo">${campoVeo()}</div>
            </div>
            <div class="ana-pasos" id="anaPasos">${chips}</div>
            <div id="anaCuerpo">${cuerpo}</div>
            <div class="ana-nav">
                ${ANA.paso > 0 ? '<button class="ana-btn ana-btn-light" data-act="ant">‹ Anterior</button>' : ''}
                ${ANA.paso < ps.length - 1 ? '<button class="ana-btn ana-btn-dark" data-act="sig">Siguiente ›</button>' : ''}
            </div>`;
        const act = document.querySelector('#anaPasos .ana-paso.act');
        if (act) act.scrollIntoView({ block: 'nearest', inline: 'center' });
        barra(true);
        if (ANA.veo.para !== a.fecha) cargarVeo();
    }

    // ───── VEO: partido vinculado y sus estadísticas ─────
    function campoVeo() {
        const v = ANA.veo, a = ANA.a;
        if (v.estado === 'sin_configurar' || v.estado === 'error') return '';
        if (v.estado !== 'ok') return '<label>Partido en Veo</label><div style="font-size:12px;color:var(--soft);padding:8px 0;"><span class="ana-spin"></span> Buscando el partido en Veo…</div>';
        const sel = a.datos.veo_id || '';
        const ops = v.grab.map(g => `<option value="${esc(g.id)}" ${g.id === sel ? 'selected' : ''}>${g.id === v.sugerido ? '★ ' : ''}${esc(fechaTxt(g.fecha))} · ${esc(g.titulo)}</option>`).join('');
        const aviso = !sel ? '<div style="font-size:10.5px;color:#d97706;font-weight:700;margin-top:4px;">No se ha encontrado solo: elige el partido si se grabó con Veo.</div>' : '';
        return `<label>Partido en Veo</label>
            <select id="anaVeo" style="width:100%;box-sizing:border-box;border:1.5px solid var(--border);border-radius:8px;padding:9px;font-size:12.5px;font-family:inherit;font-weight:700;background:#fff;">
                <option value="" ${!sel ? 'selected' : ''}>— Elegir partido de Veo —</option>
                <option value="NO" ${sel === 'NO' ? 'selected' : ''}>Sin vídeo de Veo</option>${ops}
            </select>${aviso}`;
    }

    async function cargarVeo() {
        const a = ANA.a;
        if (!a || !a.fecha) return;
        ANA.veo.para = a.fecha; ANA.veo.estado = null;
        const pintar = () => { const c = document.getElementById('anaVeoCampo'); if (c) c.innerHTML = campoVeo(); };
        pintar();
        try {
            const q = `equipo=${encodeURIComponent(EQUIPO)}&fecha=${encodeURIComponent(a.fecha)}&rival=${encodeURIComponent(a.rival || '')}`;
            const [rc, rg] = await Promise.all([fetch('/api/veo/candidatos?' + q).then(r => r.json()),
                                                fetch('/api/veo/grabaciones?fecha=' + encodeURIComponent(a.fecha)).then(r => r.json())]);
            if (rc.status === 'sin_configurar') { ANA.veo.estado = 'sin_configurar'; pintar(); return; }
            if (rc.status !== 'ok') throw new Error(rc.message);
            const vistos = new Set();
            ANA.veo.grab = (rc.candidatos || []).concat(rg.grabaciones || []).filter(g => !vistos.has(g.id) && vistos.add(g.id));
            ANA.veo.sugerido = rc.sugerido;
            ANA.veo.estado = 'ok';
            if (ANA.a === a && !a.datos.veo_id && rc.sugerido) { a.datos.veo_id = rc.sugerido; a.datos.veo_auto = true; cambio(); }
        } catch (e) {
            ANA.veo.estado = 'error';
        }
        if (ANA.a === a) { pintar(); if (VEO_ID_OK(a.datos.veo_id)) cargarVeoDatos(a.datos.veo_id); }
    }

    async function cargarVeoDatos(id) {
        if (ANA.veoDatos[id]) return;
        ANA.veoDatos[id] = 'cargando';
        try {
            const d = await (await fetch('/api/veo/partido/' + encodeURIComponent(id))).json();
            ANA.veoDatos[id] = d.status === 'ok' ? d.partido : 'error';
        } catch (e) { ANA.veoDatos[id] = 'error'; }
        if (ANA.vista === 'editor' && pasos()[ANA.paso].tipo === 'inf') refrescarCuerpo();
    }

    function tarjetaVeo() {
        const id = ANA.a.datos.veo_id;
        if (!VEO_ID_OK(id)) return '';
        const r = ANA.veoDatos[id];
        if (!r) cargarVeoDatos(id);
        const cuerpo = !r || r === 'cargando' ? '<div class="ana-vacio"><span class="ana-spin"></span> Cargando estadísticas de Veo…</div>'
            : r === 'error' ? '<div class="ana-vacio">No se han podido cargar las estadísticas de Veo.</div>'
            : anaVeoHtml(r, true);
        return `<div class="ana-card" style="padding:12px 14px;"><div style="font-size:10px;font-weight:900;color:#16a34a;letter-spacing:1px;text-transform:uppercase;margin-bottom:8px;">📹 Estadísticas Veo</div>${cuerpo}</div>`;
    }

    function renderBloque(b) {
        const secs = b.secciones.map(s => {
            const ok = s.preguntas.filter(respondida).length;
            return `<details class="ana-card ana-sec" data-sec="${esc(s.id)}" ${ANA.cerradas.has(s.id) ? '' : 'open'}>
                <summary><span>${esc(s.id)} · ${esc(s.titulo)}</span><span class="cnt">${ok}/${s.preguntas.length}</span></summary>
                ${s.preguntas.map(renderPregunta).join('')}
            </details>`;
        }).join('');
        return `<div class="ana-bloque-tit">${esc(b.titulo)}</div><div class="ana-bloque-sub">${esc(b.subtitulo || '')}</div>${secs}`;
    }

    function opcionesHtml(clave, tipo, opciones, valor, conSub, pid) {
        const elegidas = Array.isArray(valor) ? valor : (valor ? [valor] : []);
        return opciones.map(o => {
            const sel = elegidas.includes(o.id);
            let h = `<button class="ana-op ${tipo === 'multi' ? 'multi' : ''} ${sel ? 'sel' : ''}" data-act="op" data-k="${esc(clave)}" data-o="${esc(o.id)}" data-t="${tipo}">
                <span class="mk">${sel ? '✓' : ''}</span><span>${esc(o.texto)}${o.desc ? `<span class="ds">${esc(o.desc)}</span>` : ''}</span></button>`;
            if (conSub && sel && o.sub) {
                const sk = `${pid}>${o.id}`;
                h += `<div class="ana-sub"><div class="ana-sub-t">${esc(o.sub.texto)}${o.sub.tipo === 'multi' ? ' · varias' : ''}</div>
                    ${opcionesHtml(sk, o.sub.tipo, o.sub.opciones, ANA.a.datos.respuestas[sk], false)}</div>`;
            }
            return h;
        }).join('');
    }

    function renderPregunta(p) {
        return `<div class="ana-preg"><div class="ana-preg-t">${esc(p.texto)}${p.tipo === 'multi' ? '<small>Puedes marcar varias</small>' : ''}</div>
            ${opcionesHtml(p.id, p.tipo, p.opciones, ANA.a.datos.respuestas[p.id], true, p.id)}</div>`;
    }

    function renderChecklist() {
        const chk = ANA.a.datos.checklist;
        const lineas = ANA.arbol.checklist.lineas.map(l => {
            const fase = ANA.fase[l.id] || 'ofe';
            const nO = (chk[l.id + '.ofe'] || []).length, nD = (chk[l.id + '.def'] || []).length;
            const k = `${l.id}.${fase}`;
            return `<div class="ana-card">
                <div class="ana-linea-h"><span>${esc(l.nombre)}</span><span class="cnt ${nO + nD ? '' : 'cero'}">${nO + nD} fallos</span></div>
                <div class="ana-seg">
                    <button class="${fase === 'ofe' ? 'act' : ''}" data-act="fase" data-l="${l.id}" data-f="ofe">OFENSIVO${nO ? ' · ' + nO : ''}</button>
                    <button class="${fase === 'def' ? 'act' : ''}" data-act="fase" data-l="${l.id}" data-f="def">DEFENSIVO${nD ? ' · ' + nD : ''}</button>
                </div>
                <div class="ana-chips">${l[fase].map(o => {
                    const sel = (chk[k] || []).includes(o.id);
                    return `<button class="ana-op multi ${sel ? 'sel' : ''}" data-act="chk" data-k="${k}" data-o="${esc(o.id)}"><span class="mk">${sel ? '✓' : ''}</span><span>${esc(o.texto)}</span></button>`;
                }).join('')}</div>
            </div>`;
        }).join('');
        return `<div class="ana-bloque-tit">${esc(ANA.arbol.checklist.titulo)}</div>
            <div class="ana-bloque-sub">Marca los fallos que viste en cada línea, en ataque y en defensa</div>
            ${lineas}
            <div class="ana-card" style="padding:12px 14px;">
                <div class="ana-preg-t">Observaciones (opcional)</div>
                <textarea id="anaObs" rows="4" style="width:100%;box-sizing:border-box;border:1.5px solid var(--border);border-radius:8px;padding:9px;font-family:inherit;font-size:13px;resize:vertical;" placeholder="Cualquier detalle que quieras que tenga en cuenta el análisis">${esc(ANA.a.datos.observaciones)}</textarea>
            </div>`;
    }

    function renderInforme() {
        const a = ANA.a;
        const resumen = ANA.arbol.bloques.map(b => {
            const c = contarBloque(b);
            return `<div style="display:flex;justify-content:space-between;padding:7px 14px;border-top:1px solid #f1f5f9;font-size:12px;font-weight:700;color:#334155;">
                <span>${esc(b.titulo)}</span><span style="color:${c.ok ? '#16a34a' : 'var(--soft)'};">${c.ok}/${c.tot}</span></div>`;
        }).join('') + `<div style="display:flex;justify-content:space-between;padding:7px 14px;border-top:1px solid #f1f5f9;font-size:12px;font-weight:700;color:#334155;">
                <span>Fallos por líneas</span><span style="color:${contarChecklist() ? '#16a34a' : 'var(--soft)'};">${contarChecklist()}</span></div>`;
        const informe = ANA.ocupado === 'ia'
            ? '<div class="ana-vacio"><span class="ana-spin"></span><br><br>Generando el análisis de élite…<br>Puede tardar unos segundos.</div>'
            : (a.informe_html ? `<div class="ana-informe">${a.informe_html}</div>`
                : '<div class="ana-vacio">Pulsa <b>Análisis IA</b> para generar el informe a partir de tus respuestas.</div>');
        return `<div class="ana-bloque-tit">Informe</div><div class="ana-bloque-sub">Resumen de lo marcado e informe de la IA</div>
            <div class="ana-card"><div style="padding:12px 14px 6px;font-size:10px;font-weight:900;color:var(--soft);letter-spacing:1px;text-transform:uppercase;">Respuestas</div>${resumen}</div>
            ${tarjetaVeo()}
            <button class="ana-btn" style="width:100%;margin-bottom:10px;background:#fff;color:var(--red);border:2px solid var(--red);" data-act="pdf">⬇ Descargar PDF</button>
            <div class="ana-card"><div style="padding:12px 14px 0;font-size:10px;font-weight:900;color:var(--red);letter-spacing:1px;text-transform:uppercase;">Análisis de élite (IA)</div>${informe}</div>`;
    }

    function barra(visible) {
        let b = document.getElementById('anaBar');
        if (!b) {
            b = document.createElement('div');
            b.id = 'anaBar'; b.className = 'ana-bar';
            b.innerHTML = '<button class="ana-btn ana-btn-dark" data-act="guardar">💾 Guardar</button><button class="ana-btn ana-btn-red" data-act="ia">✨ Análisis IA</button><button class="ana-btn" style="background:#111827;color:#fff;" data-act="consultor">🧠 Consultor</button>';
            b.addEventListener('click', clic);
            document.body.appendChild(b);
        }
        const panel = document.getElementById('panelAna');
        const ver = visible && panel && panel.classList.contains('active');
        b.style.display = ver ? 'flex' : 'none';
        b.querySelectorAll('button').forEach(x => x.disabled = !!ANA.ocupado);
        if (ver) {
            b.children[0].innerHTML = ANA.ocupado === 'guardar' ? '<span class="ana-spin"></span> Guardando' : '💾 Guardar';
            b.children[1].innerHTML = ANA.ocupado === 'ia' ? '<span class="ana-spin"></span> Generando' : '✨ Análisis IA';
        }
    }

    // ───── acciones ─────
    function elegir(clave, opcion, tipo) {
        const r = ANA.a.datos.respuestas;
        if (tipo === 'multi') {
            const arr = Array.isArray(r[clave]) ? r[clave].slice() : [];
            const i = arr.indexOf(opcion);
            if (i >= 0) { arr.splice(i, 1); delete r[`${clave}>${opcion}`]; } else arr.push(opcion);
            if (arr.length) r[clave] = arr; else delete r[clave];
        } else {
            if (r[clave] === opcion) { delete r[clave]; delete r[`${clave}>${opcion}`]; }
            else {
                if (r[clave]) delete r[`${clave}>${r[clave]}`];
                r[clave] = opcion;
            }
        }
        cambio();
    }

    function sinRespuestas() {
        return !Object.keys(ANA.a.datos.respuestas).length && !contarChecklist() && !(ANA.a.datos.observaciones || '').trim();
    }

    async function guardar(silencioso) {
        const a = ANA.a;
        if (!a.fecha) { toast('Pon la fecha del partido', true); return false; }
        ANA.ocupado = 'guardar'; barra(true);
        try {
            const r = await fetch('/api/analisis_pp/guardar', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: a.id, equipo: EQUIPO, fecha: a.fecha, rival: a.rival, resultado: a.resultado, datos: a.datos })
            });
            const d = await r.json();
            if (d.status !== 'ok') throw new Error(d.message || 'Error al guardar');
            a.id = d.id; ANA.dirty = false; guardarBorrador();
            ANA.lista = null; cargarLista().then(() => { if (ANA.vista === 'lista') render(); });
            if (!silencioso) toast('Análisis guardado');
            return true;
        } catch (e) {
            toast(e.message || 'Error de conexión', true);
            return false;
        } finally {
            ANA.ocupado = false; if (ANA.vista === 'editor') renderEditor();
        }
    }

    async function generarIA() {
        if (sinRespuestas()) { toast('Rellena alguna pregunta antes', true); return; }
        if ((ANA.dirty || !ANA.a.id) && !(await guardar(true))) return;
        ANA.paso = pasos().length - 1;
        ANA.ocupado = 'ia'; renderEditor(); window.scrollTo({ top: 0, behavior: 'smooth' });
        try {
            const a = ANA.a;
            const r = await fetch('/api/analisis_pp/ia', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: a.id, equipo: EQUIPO, fecha: a.fecha, rival: a.rival, resultado: a.resultado, datos: a.datos })
            });
            const d = await r.json();
            if (d.status !== 'ok') throw new Error(d.message || 'Error generando el análisis');
            a.informe_html = d.informe_html; guardarBorrador();
            ANA.lista = null; cargarLista();
            toast('Análisis generado');
        } catch (e) {
            toast(e.message || 'Error de conexión', true);
        } finally {
            ANA.ocupado = false; renderEditor();
        }
    }

    async function descargarPdf() {
        // La ventana se abre en el mismo toque (si no, el móvil la bloquea al esperar al guardado)
        const w = window.open('', '_blank');
        if ((ANA.dirty || !ANA.a.id) && !(await guardar(true))) { if (w) w.close(); return; }
        const url = '/analisis_pp/' + encodeURIComponent(ANA.a.id) + '/pdf';
        if (w) w.location.href = url; else window.location.href = url;
    }

    async function abrir(id) {
        ANA.vista = 'editor'; ANA.paso = 0; ANA.a = null;
        el().innerHTML = '<div class="ana-card ana-vacio"><span class="ana-spin"></span> Abriendo…</div>';
        try {
            const r = await fetch('/api/analisis_pp/' + encodeURIComponent(id));
            const d = await r.json();
            if (d.status !== 'ok') throw new Error(d.message);
            const x = d.analisis;
            ANA.a = { id: x.id, fecha: x.fecha, rival: x.rival, resultado: x.resultado,
                      datos: Object.assign({ respuestas: {}, checklist: {}, observaciones: '' }, x.datos || {}), informe_html: x.informe_html || '' };
            ANA.dirty = false; guardarBorrador(); renderEditor();
        } catch (e) {
            toast('No se ha podido abrir el análisis', true); ANA.vista = 'lista'; render();
        }
    }

    async function nuevo() {
        ANA.a = nuevoAnalisis(); ANA.paso = 0; ANA.dirty = false; ANA.cerradas = new Set();
        await cargarPartidos();
        const hoy = hoyIso();
        const jugado = (ANA.partidos || []).find(p => p.fecha <= hoy && p.resultado);
        if (jugado) { ANA.a.fecha = jugado.fecha; ANA.a.rival = jugado.rival; ANA.a.resultado = jugado.resultado; }
        else ANA.a.fecha = hoy;
        ANA.vista = 'editor'; guardarBorrador(); renderEditor();
    }

    function volver() {
        if (ANA.dirty && !confirm('Tienes cambios sin guardar. ¿Volver a la lista igualmente? (el borrador se perderá)')) return;
        ANA.vista = 'lista'; ANA.a = null; ANA.dirty = false; borrarBorrador(); render();
    }

    function clic(e) {
        const t = e.target.closest('[data-act]');
        if (!t) return;
        const act = t.dataset.act;
        if (act === 'nuevo') nuevo();
        else if (act === 'abrir') {
            if (ANA.dirty && ANA.a && !confirm('Tienes un análisis sin guardar. ¿Abrir otro igualmente?')) return;
            abrir(t.dataset.id);
        }
        else if (act === 'volver') volver();
        else if (act === 'paso') { ANA.paso = +t.dataset.i; renderEditor(); window.scrollTo({ top: 0, behavior: 'smooth' }); }
        else if (act === 'sig' || act === 'ant') { ANA.paso += act === 'sig' ? 1 : -1; guardarBorrador(); renderEditor(); window.scrollTo({ top: 0, behavior: 'smooth' }); }
        else if (act === 'op') { e.preventDefault(); elegir(t.dataset.k, t.dataset.o, t.dataset.t); refrescarCuerpo(); }
        else if (act === 'chk') {
            const chk = ANA.a.datos.checklist, k = t.dataset.k, o = t.dataset.o;
            const arr = (chk[k] || []).slice(); const i = arr.indexOf(o);
            if (i >= 0) arr.splice(i, 1); else arr.push(o);
            if (arr.length) chk[k] = arr; else delete chk[k];
            cambio(); refrescarCuerpo();
        }
        else if (act === 'fase') { ANA.fase[t.dataset.l] = t.dataset.f; refrescarCuerpo(); }
        else if (act === 'guardar') guardar(false);
        else if (act === 'ia') generarIA();
        else if (act === 'pdf') descargarPdf();
        else if (act === 'consultor') {
            const id = ANA.a && ANA.a.datos.veo_id;
            if (!VEO_ID_OK(id)) { toast('Elige arriba el partido en Veo para usar el Consultor', true); return; }
            if (typeof abrirConsultorIA === 'function') abrirConsultorIA({ veo_id: id, titulo: `${EQUIPO} vs ${ANA.a.rival || 'rival'} · ${fechaTxt(ANA.a.fecha)}` });
        }
    }

    // Re-pinta solo el paso actual manteniendo el scroll (para tocar opciones sin saltos)
    function refrescarCuerpo() {
        const y = window.scrollY;
        renderEditor();
        window.scrollTo(0, y);
    }

    function entrada(e) {
        const id = e.target.id;
        if (!ANA.a || !['anaFecha', 'anaRival', 'anaResultado', 'anaObs', 'anaVeo'].includes(id)) return;
        if (id === 'anaVeo') {
            if (ANA.a.datos.veo_id === e.target.value) return;
            ANA.a.datos.veo_id = e.target.value; ANA.a.datos.veo_auto = false;
            if (VEO_ID_OK(e.target.value)) cargarVeoDatos(e.target.value);
            cambio(); return;
        }
        if (id === 'anaFecha') {
            ANA.a.fecha = e.target.value;
            // Si el partido de Veo se eligió solo, se vuelve a buscar para la nueva fecha
            if (ANA.a.datos.veo_auto) { ANA.a.datos.veo_id = ''; }
            const p = partidoDe(ANA.a.fecha);
            if (p) {
                ANA.a.rival = p.rival; ANA.a.resultado = p.resultado;
                document.getElementById('anaRival').value = p.rival;
                document.getElementById('anaResultado').value = p.resultado;
                toast('Rival y resultado rellenados');
            }
            cargarVeo();
        }
        else if (id === 'anaRival') ANA.a.rival = e.target.value;
        else if (id === 'anaResultado') ANA.a.resultado = e.target.value;
        else if (id === 'anaObs') ANA.a.datos.observaciones = e.target.value;
        cambio();
    }

    function toggleSeccion(e) {
        const d = e.target.closest && e.target.closest('details[data-sec]');
        if (!d) return;
        if (d.open) ANA.cerradas.delete(d.dataset.sec); else ANA.cerradas.add(d.dataset.sec);
    }

    // ───── API pública para movil.html ─────
    window.anaIniciar = async function () {
        const app = el();
        if (!app) return;
        if (!ANA.iniciado) {
            ANA.iniciado = true;
            app.addEventListener('click', clic);
            app.addEventListener('change', entrada);
            app.addEventListener('input', e => { if (e.target.id !== 'anaFecha') entrada(e); });
            app.addEventListener('toggle', toggleSeccion, true);
            try {
                const b = JSON.parse(localStorage.getItem(BORRADOR) || 'null');
                if (b && b.a) { ANA.a = b.a; ANA.dirty = !!b.dirty; ANA.paso = b.paso || 0; ANA.vista = 'editor'; }
            } catch (e) {}
        }
        if (esF7()) { render(); return; }
        render();
        try { await Promise.all([cargarArbol(), ANA.lista === null ? cargarLista() : null]); }
        catch (e) { app.innerHTML = '<div class="ana-card ana-vacio">No se ha podido cargar el análisis. Revisa la conexión.</div>'; return; }
        cargarPartidos();
        render();
    };
    window.anaAlCambiarTab = function (t) { barra(t === 'ana' && ANA.vista === 'editor' && !!ANA.a); };

    window.addEventListener('beforeunload', e => {
        if (ANA.dirty) { guardarBorrador(); }
    });
})();
