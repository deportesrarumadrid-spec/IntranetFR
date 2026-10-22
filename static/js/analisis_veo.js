/* Estadísticas de Veo de un partido (tarjeta con barras, todas las estadísticas y tabla por jugador).
 * Compartido por el móvil (analisis_postpartido.js) y Dirección Deportiva (analisis_postpartido_dd.js). */
(function () {
    const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const VEO_CLAVE = ['possession', 'total_attempts', 'shots_inside_box', 'passes', 'pass_accuracy', 'final_third_passes',
                       'duels', 'tackles', 'recoveries', 'interceptions', 'corners', 'fouls',
                       'distance_total_meters', 'high_intensity_runs_total', 'sprints_total', 'top_speed_kmh'];

    function filaBarra(f) {
        const n = +f.nuestro || 0, r = +f.rival || 0, t = n + r || 1;
        return `<div style="padding:6px 0;border-top:1px solid #f1f5f9;">
            <div style="display:flex;justify-content:space-between;font-size:12px;font-weight:800;color:var(--text,#111827);">
                <span style="color:var(--red,#E31E24);">${esc(f.nuestro)}</span><span style="font-size:10.5px;color:#64748b;font-weight:700;">${esc(f.etiqueta)}</span><span>${esc(f.rival)}</span></div>
            <div style="display:flex;gap:3px;margin-top:4px;height:6px;">
                <div style="flex:${n / t};background:var(--red,#E31E24);border-radius:3px;"></div><div style="flex:${r / t};background:#cbd5e1;border-radius:3px;"></div></div>
        </div>`;
    }

    // ───── Gráficos al estilo de Veo (SVG) ─────
    const ROJO = '#c0313b', GRIS = '#39433e', LINEA = '#5b6661';
    const st = document.createElement('style');
    st.textContent = `
    .veo-graf{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;margin-top:10px;}
    .veo-g{background:#121916;border-radius:12px;padding:12px 12px 10px;color:#e5e7eb;}
    .veo-g h5{margin:0;font-size:13px;font-weight:800;color:#fff;}
    .veo-g .sub{font-size:8.5px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;color:#93a39b;margin:3px 0 8px;}
    .veo-g svg{display:block;width:100%;height:auto;}
    .veo-g .ley{display:flex;gap:10px;flex-wrap:wrap;font-size:10px;color:#cbd5d0;margin-top:6px;}
    .veo-g .ley i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;vertical-align:-1px;}`;
    document.head.appendChild(st);

    const tarjeta = (titulo, sub, cuerpo) => `<div class="veo-g"><h5>${esc(titulo)}</h5><div class="sub">${esc(sub)}</div>${cuerpo}</div>`;

    // Campo de 105 x 68 (x = largo, atacando a la derecha; y = ancho)
    function campo(dentro) {
        const l = `stroke="${LINEA}" stroke-width="0.35" fill="none"`;
        return `<svg viewBox="-2 -2 109 72" preserveAspectRatio="xMidYMid meet">
            <rect x="0" y="0" width="105" height="68" rx="2" ${l}/>
            <line x1="52.5" y1="0" x2="52.5" y2="68" ${l}/><circle cx="52.5" cy="34" r="9.15" ${l}/>
            <rect x="0" y="13.84" width="16.5" height="40.32" ${l}/><rect x="88.5" y="13.84" width="16.5" height="40.32" ${l}/>
            <rect x="0" y="24.84" width="5.5" height="18.32" ${l}/><rect x="99.5" y="24.84" width="5.5" height="18.32" ${l}/>
            <path d="M16.5 26.7 A9.15 9.15 0 0 1 16.5 41.3" ${l}/><path d="M88.5 26.7 A9.15 9.15 0 0 0 88.5 41.3" ${l}/>
            <rect x="-1.4" y="30.3" width="1.4" height="7.4" fill="${LINEA}"/><rect x="105" y="30.3" width="1.4" height="7.4" fill="${LINEA}"/>
            <path d="M56 5 h3 m-1.2 -1.2 l1.2 1.2 l-1.2 1.2" ${l}/>
            ${dentro}</svg>`;
    }
    const X = x => (x * 105).toFixed(2), Y = y => (y * 68).toFixed(2);

    function camiseta(x, y, dorsal, escala) {
        const s = escala || 1;
        return `<g transform="translate(${X(x)} ${Y(y)}) scale(${s})">
            <path d="M-3.4 -2.6 L-1.4 -3.5 Q0 -2.4 1.4 -3.5 L3.4 -2.6 L4.4 -0.6 L2.8 0.1 L2.8 3.3 L-2.8 3.3 L-2.8 0.1 L-4.4 -0.6 Z" fill="${ROJO}" stroke="#7f1d1d" stroke-width="0.2"/>
            <text x="0" y="1.55" text-anchor="middle" font-size="3" font-weight="800" fill="#fff" font-family="Arial">${esc(dorsal)}</text></g>`;
    }

    function barras(datos, alto, colorTotal) {
        // datos: [{etiqueta, total, ok}] → barras verticales (total gris, completados rojo)
        const max = Math.max(1, ...datos.map(d => d.total || 0));
        const w = 100 / datos.length;
        return `<svg viewBox="0 -6 100 ${alto + 18}">
            ${datos.map((d, i) => {
                const ht = (d.total || 0) / max * alto, ho = (d.ok || 0) / max * alto, x = i * w + w * 0.18, bw = w * 0.64;
                return `<rect x="${x}" y="${alto - ht}" width="${bw}" height="${ht}" fill="${colorTotal || GRIS}"/>
                    <rect x="${x}" y="${alto - ho}" width="${bw}" height="${ho}" fill="${ROJO}"/>
                    <text x="${x + bw / 2}" y="${alto - ht - 1}" text-anchor="middle" font-size="3.2" fill="#e5e7eb">${d.total ?? ''}</text>
                    <text x="${x + bw / 2}" y="${alto + 5}" text-anchor="middle" font-size="3.4" fill="#cbd5d0">${esc(d.etiqueta)}</text>`;
            }).join('')}</svg>`;
    }

    function donut(pct, etiqueta, ocasiones, goles) {
        const r = 15, c = 2 * Math.PI * r, p = Math.max(0, Math.min(100, +pct || 0));
        return `<div style="flex:1;min-width:90px;text-align:center;">
            <svg viewBox="0 0 40 40" style="max-width:110px;margin:0 auto;"><circle cx="20" cy="20" r="${r}" fill="none" stroke="${GRIS}" stroke-width="4"/>
            ${p > 0 ? `<circle cx="20" cy="20" r="${r}" fill="none" stroke="${ROJO}" stroke-width="4" stroke-linecap="round" stroke-dasharray="${c * p / 100} ${c}" transform="rotate(-90 20 20)"/>` : ''}
            <text x="20" y="22.5" text-anchor="middle" font-size="8" font-weight="800" fill="#fff" font-family="Arial">${Math.round(p)}<tspan font-size="4">%</tspan></text></svg>
            <div style="font-size:10px;font-weight:800;color:#fff;margin-top:2px;">${esc(etiqueta)}</div>
            <div style="font-size:9.5px;color:#93a39b;">${ocasiones ?? 0} ocasiones · ${goles ?? 0} goles</div></div>`;
    }

    window.anaVeoGraficosHtml = function (v) {
        if (!v || !Object.keys(v).length) return '';
        const out = [];
        if (v.posiciones && v.posiciones.length) {
            out.push(tarjeta('Posición media', 'Posición media en el campo por jugador',
                campo(v.posiciones.map(p => camiseta(p.x, p.y, p.dorsal)).join(''))));
        }
        if (v.localizacion_pases) {
            const m = v.localizacion_pases, cel = [];
            m.forEach((fila, f) => fila.forEach((pct, c) => {
                const r = 2 + Math.sqrt(pct) * 2.3;
                cel.push(`<circle cx="${17.5 + c * 35}" cy="${11.33 + f * 22.67}" r="${pct ? r : 4}" fill="${ROJO}"/>
                    <text x="${17.5 + c * 35}" y="${12.6 + f * 22.67}" text-anchor="middle" font-size="${pct >= 10 ? 4.6 : 4}" font-weight="800" fill="#fff" font-family="Arial">${pct}%</text>`);
            }));
            const guias = `<line x1="35" y1="0" x2="35" y2="68" stroke="#cbd5d0" stroke-width="0.35" stroke-dasharray="1.6 1.2"/>
                <line x1="70" y1="0" x2="70" y2="68" stroke="#cbd5d0" stroke-width="0.35" stroke-dasharray="1.6 1.2"/>
                <line x1="0" y1="22.67" x2="105" y2="22.67" stroke="#cbd5d0" stroke-width="0.35" stroke-dasharray="1.6 1.2"/>
                <line x1="0" y1="45.33" x2="105" y2="45.33" stroke="#cbd5d0" stroke-width="0.35" stroke-dasharray="1.6 1.2"/>`;
            out.push(tarjeta('Localización de pases', 'Dónde se produjeron los pases exitosos de tu equipo',
                campo(guias + cel.join('')) + '<div class="ley" style="justify-content:space-between;"><span>DEFENSA</span><span>ATAQUE</span></div>'));
        }
        if (v.direccion_pases && v.direccion_pases.some(d => d.total)) {
            out.push(tarjeta('Dirección de pases', 'Hacia qué dirección jugó más tu equipo', barras(v.direccion_pases, 48) +
                `<div class="ley"><span><i style="background:${ROJO}"></i>Completados</span><span><i style="background:${GRIS}"></i>Intentados</span></div>`));
        }
        if (v.cadenas_pases && v.cadenas_pases.length) {
            const porLong = {}; v.cadenas_pases.forEach(c => { const k = c.length >= 8 ? '+8' : String(c.length); porLong[k] = (porLong[k] || 0) + c.count; });
            const datos = ['2', '3', '4', '5', '6', '7', '+8'].map(k => ({ etiqueta: k, total: porLong[k] || 0, ok: porLong[k] || 0 }));
            out.push(tarjeta('Cadenas de pases', 'Cuánto mantuviste tus secuencias (pases enlazados)', barras(datos, 48)));
        }
        const red = v.red_pases;
        if (red && red.nodos && red.nodos.length) {
            const pos = {}; red.nodos.forEach(n => pos[n.dorsal] = n);
            const max = Math.max(1, ...red.conexiones.map(c => c.n));
            const lineas = red.conexiones.filter(c => pos[c.de] && pos[c.a]).sort((a, b) => a.n - b.n).map(c =>
                `<line x1="${X(pos[c.de].x)}" y1="${Y(pos[c.de].y)}" x2="${X(pos[c.a].x)}" y2="${Y(pos[c.a].y)}" stroke="${ROJO}" stroke-opacity="${0.25 + 0.75 * c.n / max}" stroke-width="${0.25 + 1.6 * c.n / max}" stroke-linecap="round"/>`).join('');
            out.push(tarjeta('Red de pases', 'Los jugadores que más se pasaron el balón', campo(lineas + red.nodos.map(n => camiseta(n.x, n.y, n.dorsal)).join(''))));
        }
        if (v.conversion && v.conversion.length) {
            out.push(tarjeta('Desglose de tiros', 'Porcentaje de intentos que acabaron en gol',
                `<div style="display:flex;gap:6px;flex-wrap:wrap;">${v.conversion.map(c => donut(c.pct, c.etiqueta, c.ocasiones, c.goles)).join('')}</div>`));
        }
        if (v.tiros && v.tiros.length) {
            const g = v.tiros.filter(t => t.gol).length;
            out.push(tarjeta('Mapa de tiros', 'Dónde se lanzaron los tiros y cuáles acabaron en gol',
                campo(v.tiros.map(t => `<circle cx="${X(t.x)}" cy="${Y(t.y)}" r="1.25" fill="${t.gol ? ROJO : 'none'}" stroke="${ROJO}" stroke-width="0.45"><title>#${esc(t.dorsal)} · ${t.min}'</title></circle>`).join('')) +
                `<div class="ley"><span><i style="border:1.5px solid ${ROJO};"></i>Tiros (${v.tiros.length})</span><span><i style="background:${ROJO}"></i>Goles (${g})</span></div>`));
        }
        const def = [['entradas', 'Entradas', ROJO, 'rombo'], ['intercepciones', 'Intercepciones', '#f59e0b', 'tri'], ['recuperaciones', 'Recuperaciones', '#22c55e', 'circ']];
        if (def.some(d => (v[d[0]] || []).length)) {
            const forma = (e, color, f) => f === 'rombo' ? `<rect x="${(+X(e.x) - 0.95).toFixed(2)}" y="${(+Y(e.y) - 0.95).toFixed(2)}" width="1.9" height="1.9" fill="${color}" transform="rotate(45 ${X(e.x)} ${Y(e.y)})"/>`
                : f === 'tri' ? `<path d="M${X(e.x)} ${(+Y(e.y) - 1.3).toFixed(2)} l1.2 2.2 h-2.4 z" fill="${color}"/>`
                : `<circle cx="${X(e.x)}" cy="${Y(e.y)}" r="1.05" fill="${color}"/>`;
            out.push(tarjeta('Acciones defensivas', 'Dónde el equipo recuperó el balón e interrumpió el juego',
                campo(def.map(d => (v[d[0]] || []).map(e => forma(e, d[2], d[3])).join('')).join('')) +
                `<div class="ley">${def.map(d => `<span><i style="background:${d[2]}"></i>${d[1]} (${(v[d[0]] || []).length})</span>`).join('')}</div>`));
        }
        if (v.zonas_velocidad && v.zonas_velocidad.length) {
            const zs = v.zonas_velocidad, max = Math.max(0.01, ...zs.map(z => Math.max(z.hsr, z.sprint)));
            const w = 100 / zs.length, alto = 40;
            const svg = `<svg viewBox="0 -6 100 ${alto + 18}">${zs.map((z, i) => {
                const x = i * w + w * 0.15, bw = w * 0.32, h1 = z.hsr / max * alto, h2 = z.sprint / max * alto;
                return `<rect x="${x}" y="${alto - h1}" width="${bw}" height="${h1}" fill="#4ade80"/><rect x="${x + bw + 1}" y="${alto - h2}" width="${bw}" height="${h2}" fill="#f97316"/>
                    <text x="${x + bw + 0.5}" y="${alto + 5}" text-anchor="middle" font-size="3.1" fill="#cbd5d0">${z.desde}–${z.hasta}'</text>
                    <text x="${x + bw / 2}" y="${alto - h1 - 1}" text-anchor="middle" font-size="2.6" fill="#e5e7eb">${z.hsr}%</text>`;
            }).join('')}</svg>`;
            out.push(tarjeta('Zonas de velocidad', 'Cómo se distribuyó la intensidad a lo largo del partido', svg +
                `<div class="ley"><span><i style="background:#4ade80"></i>Alta intensidad (20-25 km/h)</span><span><i style="background:#f97316"></i>Sprint (≥25 km/h)</span></div>`));
        }
        return out.length ? `<div class="veo-graf">${out.join('')}</div>` : '';
    };

    window.anaVeoHtml = function (r, conCabecera) {
        const todas = {};
        r.secciones.forEach(s => s.filas.forEach(f => { if (!todas[f.clave]) todas[f.clave] = f; }));
        const clave = VEO_CLAVE.map(k => todas[k]).filter(Boolean).map(filaBarra).join('');
        const secciones = r.secciones.map(s => `<div style="font-size:10px;font-weight:900;color:var(--red,#E31E24);text-transform:uppercase;margin:10px 0 4px;">${esc(s.titulo)}</div>
            <table style="width:100%;border-collapse:collapse;font-size:11px;">${s.filas.map(f => `<tr style="border-top:1px solid #f1f5f9;">
                <td style="padding:4px 2px;color:#475569;">${esc(f.etiqueta)}</td><td style="text-align:center;font-weight:900;color:var(--red,#E31E24);">${esc(f.nuestro)}</td>
                <td style="text-align:center;font-weight:800;">${esc(f.rival)}</td><td style="text-align:right;color:#94a3b8;font-size:10px;white-space:nowrap;">${f.partes.map(p => p.join('-')).join(' / ')}</td></tr>`).join('')}</table>`).join('');
        const cols = r.columnas_jugador.filter(c => r.jugadores.some(j => j[c[0]]));
        const jug = r.jugadores.length ? `<div style="overflow-x:auto;"><table style="border-collapse:collapse;font-size:10.5px;white-space:nowrap;">
            <tr>${['Dorsal'].concat(cols.map(c => c[1])).map(h => `<th style="background:#1e293b;color:#fff;padding:5px 6px;font-weight:800;">${esc(h)}</th>`).join('')}</tr>
            ${r.jugadores.map((j, i) => `<tr style="background:${i % 2 ? '#f8fafc' : '#fff'};"><td style="padding:4px 6px;font-weight:900;text-align:center;">${esc(j.dorsal)}</td>${cols.map(c => `<td style="padding:4px 6px;text-align:center;">${j[c[0]] == null ? '' : esc(j[c[0]])}</td>`).join('')}</tr>`).join('')}
        </table></div>` : '<div style="font-size:11px;color:var(--soft,#9ca3af);">Sin datos por jugador.</div>';
        const goles = r.goleadores.length ? `<div style="font-size:11px;color:#475569;margin-top:4px;">⚽ ${r.goleadores.map(g => `${g.equipo === 'nuestro' ? '<b style="color:var(--red,#E31E24);">Nosotros</b>' : 'Rival'} ${g.parte}ª min ${g.minuto}${g.dorsal ? ' (#' + esc(g.dorsal) + ')' : ''}`).join(' · ')}</div>` : '';
        return (conCabecera ? `<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
                <div style="font-size:15px;font-weight:900;color:var(--text,#111827);">${esc(r.nuestro)} <span style="color:var(--red,#E31E24);">${esc(r.marcador[0])} - ${esc(r.marcador[1])}</span> ${esc(r.rival)}</div>
                <a href="${esc(r.url)}" target="_blank" rel="noopener" style="flex-shrink:0;font-size:10.5px;font-weight:900;color:#fff;background:#16a34a;border-radius:8px;padding:6px 9px;text-decoration:none;">▶ VER EN VEO</a></div>${goles}` : '') +
            `<div style="display:flex;justify-content:space-between;font-size:9px;font-weight:900;color:var(--soft,#9ca3af);text-transform:uppercase;margin-top:10px;"><span style="color:var(--red,#E31E24);">Nosotros</span><span>Rival</span></div>
            ${clave}
            ${window.anaVeoGraficosHtml(r.visual)}
            <details style="margin-top:8px;"><summary style="cursor:pointer;font-size:11px;font-weight:900;color:#1e293b;padding:6px 0;">Todas las estadísticas (1ª / 2ª parte)</summary>${secciones}</details>
            <details style="margin-top:4px;"><summary style="cursor:pointer;font-size:11px;font-weight:900;color:#1e293b;padding:6px 0;">Por jugador (dorsal)</summary>${jug}</details>`;
    };

})();
