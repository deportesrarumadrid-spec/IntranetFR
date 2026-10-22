/* CONSULTOR IA — preguntas libres sobre un partido de Veo, con dibujos de secuencias.
 * Uso: abrirConsultorIA({ veo_id, titulo }). Compartido por el móvil y Dirección Deportiva. */
(function () {
    const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const C = { veo_id: null, titulo: '', historial: [], plantilla: null, ocupado: false, dibujos: {}, clips: {}, video: null };
    const ROJO = '#E31E24', AZUL = '#60a5fa';

    const st = document.createElement('style');
    st.textContent = `
    #consIA{position:fixed;inset:0;z-index:30000;background:rgba(15,23,42,.55);display:none;align-items:stretch;justify-content:center;}
    #consIA .cv{background:#f2f3f7;width:100%;max-width:980px;display:flex;flex-direction:column;height:100%;}
    @media(min-width:900px){#consIA{padding:24px;} #consIA .cv{border-radius:16px;overflow:hidden;height:auto;max-height:calc(100vh - 48px);}}
    #consIA .cab{background:#111827;color:#fff;padding:12px 14px;display:flex;align-items:center;gap:10px;}
    #consIA .cab b{font-size:14px;letter-spacing:.3px;} #consIA .cab small{display:block;font-size:10.5px;color:#9ca3af;font-weight:600;}
    #consIA .x{margin-left:auto;background:none;border:none;color:#fff;font-size:26px;cursor:pointer;line-height:1;}
    #consIA .msgs{flex:1;overflow-y:auto;padding:12px;}
    #consIA .m-u{margin:8px 0 8px auto;max-width:85%;background:#111827;color:#fff;border-radius:14px 14px 2px 14px;padding:10px 12px;font-size:13px;font-weight:600;}
    #consIA .m-a{margin:8px 0;background:#fff;border:1px solid #e5e7eb;border-radius:14px 14px 14px 2px;padding:12px 14px;font-size:13px;line-height:1.5;color:#1f2937;}
    #consIA .m-a h2,#consIA .m-a h3,#consIA .m-a h4{font-size:13px;font-weight:900;color:${ROJO};text-transform:uppercase;margin:12px 0 6px;}
    #consIA .m-a ul,#consIA .m-a ol{padding-left:18px;margin:4px 0 8px;} #consIA .m-a li{margin-bottom:4px;} #consIA .m-a p{margin:4px 0 8px;}
    #consIA .m-e{margin:8px 0;background:#fef2f2;color:#991b1b;border-radius:10px;padding:10px 12px;font-size:12.5px;font-weight:700;}
    #consIA .dib{background:#121916;border-radius:12px;padding:10px;margin:10px 0;color:#e5e7eb;}
    #consIA .dib .t{font-size:12px;font-weight:800;color:#fff;} #consIA .dib .n{font-size:10px;color:#93a39b;margin-top:4px;}
    #consIA .dib svg{display:block;width:100%;height:auto;margin-top:6px;}
    #consIA .sug{padding:10px 12px;border-bottom:1px solid #e5e7eb;background:#fff;max-height:38vh;overflow-y:auto;}
    #consIA .sug .cat{font-size:9.5px;font-weight:900;color:#9ca3af;letter-spacing:1px;text-transform:uppercase;margin:8px 0 5px;}
    #consIA .chip{display:inline-flex;align-items:center;gap:6px;text-align:left;border:1.5px solid #e5e7eb;background:#f9fafb;border-radius:10px;padding:7px 10px;margin:0 6px 6px 0;font-size:11.5px;font-weight:700;color:#374151;cursor:pointer;font-family:inherit;line-height:1.3;}
    #consIA .chip:hover{border-color:${ROJO};}
    #consIA .chip.mia{background:#fff1f2;border-color:#fecdd3;}
    #consIA .chip .del{color:#9ca3af;font-weight:900;padding:0 2px;}
    #consIA .pie{background:#fff;border-top:1px solid #e5e7eb;padding:10px 12px;display:flex;gap:8px;align-items:flex-end;}
    #consIA textarea{flex:1;border:1.5px solid #e5e7eb;border-radius:12px;padding:10px;font-size:13.5px;font-family:inherit;resize:none;max-height:120px;}
    #consIA .btn{border:none;border-radius:11px;padding:11px 14px;font-weight:900;font-size:11px;letter-spacing:.5px;cursor:pointer;font-family:inherit;}
    #consIA .btn:disabled{opacity:.5;}
    #consIA .spin{display:inline-block;width:13px;height:13px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:consspin .7s linear infinite;vertical-align:-2px;}
    @keyframes consspin{to{transform:rotate(360deg)}}
    #consIA .cons-clip{display:inline-flex;align-items:center;gap:4px;margin:2px 0 2px 6px;border:none;border-radius:8px;background:#16a34a;color:#fff;font-size:10.5px;font-weight:900;padding:4px 9px;cursor:pointer;font-family:inherit;white-space:nowrap;}
    #consIA .mic{flex-shrink:0;width:46px;height:46px;border-radius:50%;border:none;background:#f3f4f6;font-size:20px;cursor:pointer;align-self:center;}
    #consIA .mic.on{background:${ROJO};color:#fff;animation:conslat 1s ease-in-out infinite;}
    @keyframes conslat{0%,100%{box-shadow:0 0 0 0 rgba(227,30,36,.55)}50%{box-shadow:0 0 0 10px rgba(227,30,36,0)}}
    #consVid{position:fixed;inset:0;z-index:31000;background:rgba(0,0,0,.92);display:none;flex-direction:column;align-items:center;justify-content:center;padding:12px;}
    #consVid video{width:100%;max-width:1100px;max-height:72vh;background:#000;border-radius:10px;}
    #consVid .bar{display:flex;gap:8px;align-items:center;width:100%;max-width:1100px;margin-top:10px;color:#fff;}
    #consVid .bar b{flex:1;font-size:13px;} #consVid button{border:none;border-radius:9px;padding:9px 13px;font-weight:900;font-size:11px;cursor:pointer;font-family:inherit;}`;
    document.head.appendChild(st);

    // ───── dibujo de secuencias ─────
    function campo(dentro) {
        const l = 'stroke="#5b6661" stroke-width="0.35" fill="none"';
        return `<svg viewBox="-2 -2 109 72"><defs>
            <marker id="cfR" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="4" markerHeight="4" orient="auto"><path d="M0 0 L6 3 L0 6 z" fill="${ROJO}"/></marker>
            <marker id="cfA" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="4" markerHeight="4" orient="auto"><path d="M0 0 L6 3 L0 6 z" fill="${AZUL}"/></marker></defs>
            <rect x="0" y="0" width="105" height="68" rx="2" ${l}/><line x1="52.5" y1="0" x2="52.5" y2="68" ${l}/><circle cx="52.5" cy="34" r="9.15" ${l}/>
            <rect x="0" y="13.84" width="16.5" height="40.32" ${l}/><rect x="88.5" y="13.84" width="16.5" height="40.32" ${l}/>
            <rect x="0" y="24.84" width="5.5" height="18.32" ${l}/><rect x="99.5" y="24.84" width="5.5" height="18.32" ${l}/>
            <rect x="-1.4" y="30.3" width="1.4" height="7.4" fill="#5b6661"/><rect x="105" y="30.3" width="1.4" height="7.4" fill="#5b6661"/>
            ${dentro}</svg>`;
    }
    const X = x => (x * 105).toFixed(2), Y = y => (y * 68).toFixed(2);
    function ficha(x, y, txt, color, r) {
        return `<circle cx="${X(x)}" cy="${Y(y)}" r="${r || 2.4}" fill="${color}" stroke="#fff" stroke-width="0.3"/>
            <text x="${X(x)}" y="${(+Y(y) + 0.95).toFixed(2)}" text-anchor="middle" font-size="2.6" font-weight="800" fill="#fff" font-family="Arial">${esc(txt)}</text>`;
    }
    function dibujoSvg(d) {
        let dentro = '';
        const fl = d.flechas || [];
        fl.forEach(f => {
            const col = f.eq === 'opp' ? AZUL : ROJO, mk = f.eq === 'opp' ? 'cfA' : 'cfR';
            dentro += `<line x1="${X(f.x1)}" y1="${Y(f.y1)}" x2="${X(f.x2)}" y2="${Y(f.y2)}" stroke="${col}" stroke-width="0.6" ${f.ok === false ? 'stroke-dasharray="1.4 1"' : ''} marker-end="url(#${mk})" opacity="${fl.length > 12 ? 0.75 : 1}"/>`;
        });
        if (fl.length <= 12) {
            const vistos = new Set();
            fl.forEach(f => {
                const col = f.eq === 'opp' ? AZUL : ROJO;
                const k1 = f.de + '@' + f.x1.toFixed(2);
                if (!vistos.has(k1)) { dentro += ficha(f.x1, f.y1, f.de, col); vistos.add(k1); }
                if (f.n) dentro += `<text x="${((+X(f.x1) + +X(f.x2)) / 2).toFixed(2)}" y="${((+Y(f.y1) + +Y(f.y2)) / 2 - 1.2).toFixed(2)}" text-anchor="middle" font-size="2.8" font-weight="900" fill="#fde047" font-family="Arial">${f.n}</text>`;
            });
            const ult = fl[fl.length - 1];
            if (ult && ult.a && ult.ok !== false) dentro += ficha(ult.x2, ult.y2, ult.a, ult.eq === 'opp' ? AZUL : ROJO);
        }
        (d.puntos || []).forEach(p => {
            dentro += `<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="1.5" fill="${p.relleno === false ? 'none' : (p.color || ROJO)}" stroke="${p.color || ROJO}" stroke-width="0.45"><title>${esc(p.nombre || ('#' + p.etiqueta))} · ${esc(p.min)}'</title></circle>
                <text x="${X(p.x)}" y="${(+Y(p.y) - 2).toFixed(2)}" text-anchor="middle" font-size="2.2" fill="#e5e7eb" font-family="Arial">${esc(p.etiqueta)}${p.min != null ? ' · ' + esc(p.min) + "'" : ''}</text>`;
        });
        const ley = fl.length ? `<span style="color:${ROJO};">●</span> Nosotros (→) &nbsp; <span style="color:${AZUL};">●</span> Rival (←) &nbsp; — completado &nbsp; - - fallado` : '';
        return `<div class="dib"><div class="t">${esc(d.titulo)}</div>${campo(dentro)}<div class="n">${esc(d.nota || '')}${d.nota && ley ? ' · ' : ''}${ley}</div></div>`;
    }

    // ───── interfaz ─────
    function overlay() {
        let o = document.getElementById('consIA');
        if (o) return o;
        o = document.createElement('div');
        o.id = 'consIA';
        o.innerHTML = `<div class="cv">
            <div class="cab"><span style="font-size:20px;">🧠</span><div><b>CONSULTOR IA</b><small id="consTit"></small></div>
                <button class="x" onclick="cerrarConsultorIA()">×</button></div>
            <div class="sug" id="consSug"></div>
            <div class="msgs" id="consMsgs"></div>
            <div class="pie">
                <button class="mic" id="consMic" onclick="consMic()" title="Dictar la pregunta por voz">🎤</button>
                <textarea id="consTxt" rows="2" placeholder="Pregunta lo que quieras del partido… p. ej. dibújame la secuencia más repetida del rival en su salida de balón"></textarea>
                <div style="display:flex;flex-direction:column;gap:6px;">
                    <button class="btn" style="background:${ROJO};color:#fff;" id="consEnviar" onclick="consEnviar()">ENVIAR</button>
                    <button class="btn" style="background:#f3f4f6;color:#374151;padding:7px 10px;font-size:10px;" onclick="consGuardarPregunta()" title="Guardar en mi plantilla">＋ PLANTILLA</button>
                </div>
            </div></div>`;
        o.addEventListener('click', e => { if (e.target === o) cerrarConsultorIA(); });
        document.body.appendChild(o);
        o.querySelector('#consTxt').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && window.innerWidth > 700) { e.preventDefault(); consEnviar(); } });
        return o;
    }

    function pintarSugerencias() {
        const s = document.getElementById('consSug');
        if (!s) return;
        if (!C.plantilla) { s.innerHTML = '<span style="font-size:12px;color:#9ca3af;"><span class="spin"></span> Cargando preguntas…</span>'; return; }
        const porCat = {};
        C.plantilla.sugeridas.forEach(p => (porCat[p.cat] = porCat[p.cat] || []).push(p));
        const chip = (p, mia) => `<button class="chip ${mia ? 'mia' : ''}" onclick="consUsar(this)" data-t="${esc(p.texto)}">${esc(p.texto)}${mia ? `<span class="del" onclick="event.stopPropagation();consBorrarPregunta('${esc(p.id)}')">×</span>` : ''}</button>`;
        s.innerHTML = `<div style="display:flex;align-items:center;justify-content:space-between;"><span style="font-size:11px;font-weight:900;color:#111827;">PLANTILLA DE PREGUNTAS</span>
                <button class="chip" style="margin:0;padding:4px 9px;font-size:10px;" onclick="consPlegar()">Ocultar</button></div>
            <div class="cat">⭐ Mis preguntas</div>${C.plantilla.mias.length ? C.plantilla.mias.map(p => chip(p, true)).join('') : '<div style="font-size:11px;color:#9ca3af;margin-bottom:6px;">Escribe una pregunta abajo y pulsa <b>＋ PLANTILLA</b> para guardarla aquí.</div>'}
            ${Object.entries(porCat).map(([c, ps]) => `<div class="cat">${esc(c)}</div>${ps.map(p => chip(p, false)).join('')}`).join('')}`;
    }

    async function cargarPlantilla(metodo, cuerpo) {
        try {
            const r = await fetch('/api/consultor/plantilla', metodo ? { method: metodo, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cuerpo) } : undefined);
            const d = await r.json();
            if (d.status === 'ok') C.plantilla = { sugeridas: d.sugeridas, mias: d.mias };
        } catch (e) { if (!C.plantilla) C.plantilla = { sugeridas: [], mias: [] }; }
        pintarSugerencias();
    }

    function pintarMensajes() {
        const m = document.getElementById('consMsgs');
        if (!m) return;
        if (!C.historial.length && !C.ocupado) {
            m.innerHTML = `<div class="m-a" style="background:#111827;color:#e5e7eb;border:none;">
                <b style="color:#fff;">Analista táctico IA</b><br>Pregúntame lo que quieras de este partido: secuencias de pases, salidas de balón,
                saques de puerta, pérdidas, recuperaciones, jugadores clave… Trabajo con <b>todas las acciones registradas por Veo</b>
                (pasador, receptor, origen y destino de cada pase) y te <b>dibujo</b> las jugadas. Elige una pregunta de la plantilla o escribe la tuya.</div>`;
            return;
        }
        m.innerHTML = C.historial.map(x => x.rol === 'user' ? `<div class="m-u">${esc(x.texto)}</div>`
            : x.rol === 'error' ? `<div class="m-e">${esc(x.texto)}</div>` : `<div class="m-a">${x.html}</div>`).join('')
            + (C.ocupado ? '<div class="m-a" style="color:#6b7280;"><span class="spin"></span> Analizando todas las acciones del partido… (puede tardar unos 30 segundos)</div>' : '');
        m.querySelectorAll('.cons-dibujo').forEach(el => { const d = C.dibujos[el.dataset.d]; if (d) el.outerHTML = dibujoSvg(d); });
        m.querySelectorAll('.cons-clip').forEach(b => { b.onclick = () => verClip(b.dataset.c); });
        m.scrollTop = m.scrollHeight;
    }

    // ───── dictado por voz ─────
    // 1º el dictado del propio navegador (texto en directo); si no existe o falla, se graba el audio y lo transcribe la IA
    const V = { activo: false, rec: null, mr: null, trozos: [], base: '', temporizador: null };

    function micUI(on, aviso) {
        V.activo = on;
        const b = document.getElementById('consMic'), t = document.getElementById('consTxt');
        if (b) { b.classList.toggle('on', on); b.textContent = on ? '■' : '🎤'; }
        if (t) t.placeholder = aviso || 'Pregunta lo que quieras del partido… o pulsa 🎤 y díctala';
    }

    function dictadoNavegador() {
        const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SR) return false;
        const t = document.getElementById('consTxt');
        const r = new SR();
        r.lang = 'es-ES'; r.interimResults = true; r.continuous = true;
        V.base = t.value ? t.value.trim() + ' ' : '';
        let final = '';
        r.onresult = e => {
            let provisional = '';
            for (let i = e.resultIndex; i < e.results.length; i++) {
                if (e.results[i].isFinal) final += e.results[i][0].transcript + ' ';
                else provisional += e.results[i][0].transcript;
            }
            t.value = (V.base + final + provisional).replace(/\s+/g, ' ');
        };
        r.onerror = e => {
            V.rec = null;
            if (e.error === 'not-allowed' && !V.yaAvisado) { V.yaAvisado = true; alert('Permite el acceso al micrófono para dictar la pregunta.'); micUI(false); return; }
            if (['service-not-allowed', 'network', 'audio-capture', 'language-not-supported'].includes(e.error)) {
                micUI(false); grabarAudio();  // este móvil no deja dictar: se graba y transcribe la IA
            }
        };
        r.onend = () => { if (V.rec === r) { V.rec = null; micUI(false); t.focus(); } };
        V.rec = r;
        try { r.start(); } catch (e) { V.rec = null; return false; }
        micUI(true, 'Escuchando… habla y pulsa ■ para terminar');
        return true;
    }

    async function grabarAudio() {
        if (!navigator.mediaDevices || !window.MediaRecorder) { alert('Este móvil no permite dictar por voz desde la intranet.'); return; }
        let stream;
        try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
        catch (e) { alert('Permite el acceso al micrófono para dictar la pregunta.'); return; }
        const tipo = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'].find(x => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(x)) || '';
        const mr = new MediaRecorder(stream, tipo ? { mimeType: tipo } : undefined);
        V.trozos = [];
        mr.ondataavailable = e => { if (e.data && e.data.size) V.trozos.push(e.data); };
        mr.onstop = async () => {
            stream.getTracks().forEach(tr => tr.stop());
            clearTimeout(V.temporizador);
            V.mr = null;
            micUI(false, 'Pasando el audio a texto…');
            const blob = new Blob(V.trozos, { type: (mr.mimeType || tipo || 'audio/webm').split(';')[0] });
            if (blob.size < 1500) { micUI(false); return; }
            const fd = new FormData();
            fd.append('audio', blob, 'pregunta.' + (blob.type.includes('mp4') ? 'm4a' : blob.type.includes('ogg') ? 'ogg' : 'webm'));
            try {
                const d = await (await fetch('/api/consultor/transcribir', { method: 'POST', body: fd })).json();
                if (d.status !== 'ok') throw new Error(d.message);
                const t = document.getElementById('consTxt');
                t.value = ((t.value ? t.value.trim() + ' ' : '') + d.texto).trim();
                t.focus();
            } catch (e) { alert('No se ha podido pasar el audio a texto. Prueba otra vez o escríbelo.'); }
            micUI(false);
        };
        mr.start();
        V.mr = mr;
        V.temporizador = setTimeout(() => { if (V.mr) V.mr.stop(); }, 60000);  // máximo 1 minuto
        micUI(true, 'Grabando… habla y pulsa ■ para terminar');
    }

    window.consMic = function () {
        if (V.activo) {
            if (V.rec) { const r = V.rec; V.rec = null; try { r.stop(); } catch (e) {} micUI(false); document.getElementById('consTxt').focus(); }
            if (V.mr) V.mr.stop();
            return;
        }
        if (C.ocupado) return;
        if (!dictadoNavegador()) grabarAudio();
    };

    // ───── reproductor de jugadas ─────
    function verClip(id) {
        const c = C.clips[id];
        if (!c || !C.video) return;
        let v = document.getElementById('consVid');
        if (!v) {
            v = document.createElement('div');
            v.id = 'consVid';
            v.innerHTML = `<video id="consVideo" controls playsinline preload="metadata"></video>
                <div class="bar"><b id="consVidTit"></b>
                <button style="background:#374151;color:#fff;" onclick="consRepetir()">↺ REPETIR</button>
                <button style="background:#fff;color:#111827;" onclick="consCerrarVid()">✕ CERRAR</button></div>`;
            document.body.appendChild(v);
            const vid = v.querySelector('video');
            vid.addEventListener('timeupdate', () => { if (C.clipAct && vid.currentTime >= C.clipAct.fin) vid.pause(); });
        }
        C.clipAct = c;
        document.getElementById('consVidTit').textContent = `${c.titulo} (${Math.round(c.fin - c.inicio)} s)`;
        const vid = document.getElementById('consVideo');
        v.style.display = 'flex';
        const ir = () => { vid.currentTime = c.inicio; vid.play().catch(() => {}); };
        if (vid.getAttribute('src') !== C.video) { vid.src = C.video; vid.addEventListener('loadedmetadata', ir, { once: true }); vid.load(); }
        else ir();
    }
    window.consRepetir = function () { const vid = document.getElementById('consVideo'); if (vid && C.clipAct) { vid.currentTime = C.clipAct.inicio; vid.play().catch(() => {}); } };
    window.consCerrarVid = function () { const v = document.getElementById('consVid'); const vid = document.getElementById('consVideo'); if (vid) vid.pause(); if (v) v.style.display = 'none'; };

    window.abrirConsultorIA = function (opc) {
        const o = overlay();
        if (C.veo_id !== opc.veo_id) { C.historial = []; C.dibujos = {}; C.clips = {}; C.video = null; }
        C.veo_id = opc.veo_id; C.titulo = opc.titulo || ''; C.equipo = opc.equipo || ''; C.fecha = opc.fecha || '';
        document.getElementById('consTit').textContent = C.titulo;
        o.style.display = 'flex';
        document.body.style.overflow = 'hidden';
        pintarSugerencias(); pintarMensajes();
        if (!C.plantilla) cargarPlantilla();
    };
    window.cerrarConsultorIA = function () {
        if (V.activo) consMic();
        const o = document.getElementById('consIA');
        if (o) o.style.display = 'none';
        document.body.style.overflow = '';
    };
    window.consPlegar = function () {
        const s = document.getElementById('consSug');
        s.innerHTML = '<button class="chip" style="margin:0;" onclick="pintarConsSug()">📋 Ver plantilla de preguntas</button>';
    };
    window.pintarConsSug = pintarSugerencias;
    window.consUsar = function (b) {
        const t = document.getElementById('consTxt');
        t.value = b.dataset.t; t.focus();
    };
    window.consGuardarPregunta = function () {
        const t = (document.getElementById('consTxt').value || '').trim();
        if (!t) { alert('Escribe primero la pregunta que quieres guardar.'); return; }
        cargarPlantilla('POST', { texto: t });
    };
    window.consBorrarPregunta = function (id) {
        if (confirm('¿Quitar esta pregunta de tu plantilla?')) cargarPlantilla('DELETE', { id });
    };
    window.consEnviar = async function () {
        const t = document.getElementById('consTxt');
        const q = (t.value || '').trim();
        if (!q || C.ocupado) return;
        t.value = '';
        const previo = C.historial.filter(x => x.rol === 'user' || x.rol === 'model').map(x => ({ rol: x.rol, texto: x.texto }));
        C.historial.push({ rol: 'user', texto: q });
        C.ocupado = true; document.getElementById('consEnviar').disabled = true;
        consPlegar(); pintarMensajes();
        try {
            const r = await fetch('/api/consultor/preguntar', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ veo_id: C.veo_id, pregunta: q, historial: previo, equipo: C.equipo, fecha: C.fecha }) });
            const d = await r.json();
            if (d.status !== 'ok') throw new Error(d.message || 'Error');
            // los ids de dibujo se repiten entre preguntas: se renombran para que cada respuesta tenga los suyos
            let html = d.html;
            if (d.video_url) C.video = d.video_url;
            Object.keys(d.clips || {}).forEach(k => {
                const nuevo = k + '_' + C.historial.length;
                C.clips[nuevo] = d.clips[k];
                html = html.split(`data-c="${k}"`).join(`data-c="${nuevo}"`);
            });
            Object.keys(d.dibujos || {}).forEach(k => {
                const nuevo = k + '_' + C.historial.length;
                C.dibujos[nuevo] = d.dibujos[k];
                html = html.split(`data-d="${k}"`).join(`data-d="${nuevo}"`);
            });
            C.historial.push({ rol: 'model', texto: d.texto, html });
        } catch (e) {
            C.historial.push({ rol: 'error', texto: 'No se ha podido responder: ' + e.message });
        }
        C.ocupado = false; document.getElementById('consEnviar').disabled = false;
        pintarMensajes();
    };
})();
