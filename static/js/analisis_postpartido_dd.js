/* ANÁLISIS POSTPARTIDO — vista de Dirección Deportiva (pestaña "ANÁLISIS POSTPARTIDO")
 * Lista de análisis de todos los equipos, detalle con respuestas e informe IA, PDF y regenerar informe. */
(function () {
    let lista = null;
    const st = document.createElement('style');
    st.textContent = `
    .ana-dd-informe h2,.ana-dd-informe h3{background:#1e293b;color:#fff;font-size:12px;font-weight:900;text-transform:uppercase;letter-spacing:.4px;padding:8px 10px;border-radius:8px;margin:16px 0 8px;}
    .ana-dd-informe h2:first-child,.ana-dd-informe h3:first-child{margin-top:0;}
    .ana-dd-informe h4{font-size:12.5px;font-weight:900;color:#E31E24;margin:12px 0 6px;}
    .ana-dd-informe ul{list-style:none;padding:0;margin:0 0 8px;}
    .ana-dd-informe ul li{background:#f8fafc;border-left:3px solid #E31E24;border-radius:6px;padding:7px 10px;margin-bottom:5px;}
    .ana-dd-informe ol{list-style:none;counter-reset:foco;padding:0;margin:0 0 8px;}
    .ana-dd-informe ol li{counter-increment:foco;position:relative;background:#fff1f2;border-radius:8px;padding:7px 10px 7px 38px;margin-bottom:5px;}
    .ana-dd-informe ol li::before{content:counter(foco);position:absolute;left:10px;top:7px;width:20px;height:20px;border-radius:50%;background:#E31E24;color:#fff;font-size:11px;font-weight:900;display:flex;align-items:center;justify-content:center;}`;
    document.head.appendChild(st);
    const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const fechaTxt = iso => { const p = (iso || '').split('-'); return p.length === 3 ? `${p[2]}/${p[1]}/${p[0]}` : (iso || ''); };

    window.anaDDCargar = async function (forzar) {
        const cont = document.getElementById('anaDDLista');
        if (!cont) return;
        if (lista && !forzar) { anaDDRender(); return; }
        cont.innerHTML = '<div style="text-align:center;padding:30px;color:#94a3b8;font-size:12px;"><i class="fas fa-spinner fa-spin"></i> Cargando…</div>';
        try {
            const r = await fetch('/api/analisis_pp/lista');
            const d = await r.json();
            if (d.status !== 'ok') throw new Error(d.message || 'Error');
            lista = d.analisis || [];
        } catch (e) {
            cont.innerHTML = '<div style="text-align:center;padding:30px;color:#dc2626;font-size:12px;font-weight:800;">No se han podido cargar los análisis.</div>';
            return;
        }
        const sel = document.getElementById('anaDDEquipo');
        const actual = sel.value;
        const equipos = Array.from(new Set(lista.map(a => a.equipo))).sort();
        sel.innerHTML = '<option value="">TODOS LOS EQUIPOS</option>' + equipos.map(e => `<option value="${esc(e)}">${esc(e)}</option>`).join('');
        sel.value = equipos.includes(actual) ? actual : '';
        anaDDRender();
    };

    window.anaDDRender = function () {
        const cont = document.getElementById('anaDDLista');
        if (!cont || !lista) return;
        const eq = document.getElementById('anaDDEquipo').value;
        const filas = lista.filter(a => !eq || a.equipo === eq);
        document.getElementById('anaDDTotal').textContent = filas.length + ' análisis';
        if (!filas.length) {
            cont.innerHTML = '<div style="text-align:center;padding:30px;color:#94a3b8;font-size:12px;">Todavía no hay análisis. Los entrenadores los rellenan desde la pestaña <b>Análisis</b> del móvil.</div>';
            return;
        }
        const th = 'padding:8px 10px;font-size:10px;font-weight:900;text-transform:uppercase;color:#64748b;background:#f8fafc;border-bottom:2px solid #e2e8f0;text-align:left;white-space:nowrap;';
        const td = 'padding:9px 10px;font-size:11.5px;border-bottom:1px solid #f1f5f9;';
        cont.innerHTML = `<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;">
            <thead><tr><th style="${th}">Fecha</th><th style="${th}">Equipo</th><th style="${th}">Rival</th><th style="${th}">Resultado</th><th style="${th}">Entrenador</th><th style="${th}text-align:center;">Informe IA</th><th style="${th}">Actualizado</th><th style="${th}"></th></tr></thead>
            <tbody>${filas.map(a => `<tr style="cursor:pointer;" onclick="anaDDAbrir('${esc(a.id)}')" onmouseover="this.style.background='#f8fafc'" onmouseout="this.style.background=''">
                <td style="${td}font-weight:800;white-space:nowrap;">${esc(fechaTxt(a.fecha))}</td>
                <td style="${td}font-weight:800;">${esc(a.equipo)}</td>
                <td style="${td}">${esc(a.rival || '–')}</td>
                <td style="${td}font-weight:800;">${esc(a.resultado || '–')}</td>
                <td style="${td}color:#64748b;">${esc(a.autor)}</td>
                <td style="${td}text-align:center;">${a.tiene_informe ? '<i class="fas fa-circle-check" style="color:#16a34a;"></i>' : '<span style="color:#cbd5e1;">–</span>'}</td>
                <td style="${td}color:#94a3b8;white-space:nowrap;">${esc(a.actualizado)}</td>
                <td style="${td}text-align:right;color:#E31E24;font-weight:900;white-space:nowrap;">VER ›</td>
            </tr>`).join('')}</tbody></table></div>`;
    };

    function modal() {
        let m = document.getElementById('anaDDModal');
        if (!m) {
            m = document.createElement('div');
            m.id = 'anaDDModal';
            m.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(15,23,42,.6);z-index:20000;align-items:flex-start;justify-content:center;padding:30px 12px;overflow-y:auto;';
            m.addEventListener('click', e => { if (e.target === m) m.style.display = 'none'; });
            document.body.appendChild(m);
        }
        return m;
    }

    window.anaDDAbrir = async function (id) {
        const m = modal();
        m.style.display = 'flex';
        m.innerHTML = '<div style="background:white;border-radius:16px;padding:40px;color:#94a3b8;"><i class="fas fa-spinner fa-spin"></i> Cargando…</div>';
        try {
            const r = await fetch('/api/analisis_pp/' + encodeURIComponent(id));
            const d = await r.json();
            if (d.status !== 'ok') throw new Error(d.message || 'Error');
            pintarDetalle(d.analisis);
        } catch (e) {
            m.innerHTML = `<div style="background:white;border-radius:16px;padding:30px;color:#dc2626;font-weight:800;">No se ha podido abrir el análisis. <button onclick="document.getElementById('anaDDModal').style.display='none'" style="margin-left:10px;">Cerrar</button></div>`;
        }
    };

    function pintarDetalle(a) {
        const m = modal();
        const res = a.resumen || { bloques: [], checklist: [], observaciones: '' };
        const bloques = res.bloques.map(b => `
            <div style="margin-bottom:14px;">
                <div style="font-size:12px;font-weight:900;color:#E31E24;text-transform:uppercase;margin-bottom:6px;">${esc(b.titulo)}</div>
                ${b.preguntas.map(p => `<div style="margin:0 0 8px 0;">
                    <div style="font-size:11.5px;font-weight:800;color:#1e293b;">${esc(p.texto)}</div>
                    ${p.respuestas.map(r => `<div style="font-size:11.5px;color:#475569;margin:2px 0 0 12px;">• ${esc(r)}</div>`).join('')}
                </div>`).join('')}
            </div>`).join('') || '<div style="color:#94a3b8;font-size:12px;">Sin respuestas en el árbol.</div>';
        const checklist = res.checklist.length ? res.checklist.map(c => `
            <div style="margin-bottom:8px;"><div style="font-size:11.5px;font-weight:900;color:#1e293b;">${esc(c.titulo)}</div>
            ${c.respuestas.map(r => `<div style="font-size:11.5px;color:#475569;margin:2px 0 0 12px;">• ${esc(r)}</div>`).join('')}</div>`).join('')
            : '<div style="color:#94a3b8;font-size:12px;">Sin fallos marcados.</div>';
        const informe = a.informe_html
            ? `<div class="ana-dd-informe" style="font-size:12.5px;line-height:1.55;color:#334155;">${a.informe_html}</div>`
            : '<div style="color:#94a3b8;font-size:12px;">Todavía no se ha generado el informe con IA.</div>';
        m.innerHTML = `
        <div style="background:white;border-radius:16px;width:1000px;max-width:100%;overflow:hidden;box-shadow:0 20px 50px rgba(0,0,0,.3);">
            <div style="background:#1e293b;color:white;padding:16px 22px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
                <div style="flex:1;min-width:200px;">
                    <div style="font-size:15px;font-weight:900;text-transform:uppercase;">${esc(a.equipo)} · ${esc(fechaTxt(a.fecha))}</div>
                    <div style="font-size:11.5px;color:#cbd5e1;margin-top:2px;">Rival: ${esc(a.rival || '–')} · Resultado: ${esc(a.resultado || '–')} · ${esc(a.autor)}</div>
                </div>
                <a href="/analisis_pp/${encodeURIComponent(a.id)}/pdf" target="_blank" class="btn-fin" style="background:white;color:#E31E24;padding:8px 14px;font-size:10px;text-decoration:none;"><i class="fas fa-file-pdf"></i> PDF</a>
                <button class="btn-fin" id="anaDDBtnIA" style="background:#E31E24;color:white;padding:8px 14px;font-size:10px;" onclick="anaDDGenerarIA('${esc(a.id)}')"><i class="fas fa-wand-magic-sparkles"></i> ${a.informe_html ? 'REGENERAR' : 'GENERAR'} INFORME IA</button>
                <span style="font-size:26px;cursor:pointer;line-height:1;" onclick="document.getElementById('anaDDModal').style.display='none'">&times;</span>
            </div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:0;">
                <div style="padding:20px 22px;border-right:1px solid #f1f5f9;">
                    <div style="font-size:10px;font-weight:900;color:#94a3b8;letter-spacing:1px;text-transform:uppercase;margin-bottom:10px;">Informe de análisis (IA)</div>
                    <div id="anaDDInforme">${informe}</div>
                </div>
                <div style="padding:20px 22px;background:#fcfcfd;">
                    <div style="font-size:10px;font-weight:900;color:#94a3b8;letter-spacing:1px;text-transform:uppercase;margin-bottom:10px;">Respuestas del entrenador</div>
                    ${bloques}
                    <div style="font-size:12px;font-weight:900;color:#E31E24;text-transform:uppercase;margin:6px 0;">Checklist de fallos por líneas</div>
                    ${checklist}
                    ${res.observaciones ? `<div style="font-size:12px;font-weight:900;color:#E31E24;text-transform:uppercase;margin:12px 0 6px;">Observaciones</div><div style="font-size:11.5px;color:#475569;white-space:pre-wrap;">${esc(res.observaciones)}</div>` : ''}
                </div>
            </div>
        </div>`;
        m.dataset.analisis = JSON.stringify(a);
    }

    window.anaDDGenerarIA = async function (id) {
        const m = modal();
        let a;
        try { a = JSON.parse(m.dataset.analisis || 'null'); } catch (e) { a = null; }
        if (!a || a.id !== id) return;
        const btn = document.getElementById('anaDDBtnIA');
        btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> GENERANDO…';
        document.getElementById('anaDDInforme').innerHTML = '<div style="color:#94a3b8;font-size:12px;"><i class="fas fa-spinner fa-spin"></i> Generando el análisis… puede tardar unos segundos.</div>';
        try {
            const r = await fetch('/api/analisis_pp/ia', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: a.id, equipo: a.equipo, fecha: a.fecha, rival: a.rival, resultado: a.resultado, datos: a.datos })
            });
            const d = await r.json();
            if (d.status !== 'ok') throw new Error(d.message || 'Error');
            a.informe_html = d.informe_html;
            pintarDetalle(a);
            const fila = (lista || []).find(x => x.id === a.id);
            if (fila) { fila.tiene_informe = true; anaDDRender(); }
        } catch (e) {
            document.getElementById('anaDDInforme').innerHTML = `<div style="color:#dc2626;font-size:12px;font-weight:800;">${esc(e.message)}</div>`;
            btn.disabled = false; btn.innerHTML = '<i class="fas fa-wand-magic-sparkles"></i> GENERAR INFORME IA';
        }
    };
})();
