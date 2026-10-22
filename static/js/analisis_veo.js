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
            <details style="margin-top:8px;"><summary style="cursor:pointer;font-size:11px;font-weight:900;color:#1e293b;padding:6px 0;">Todas las estadísticas (1ª / 2ª parte)</summary>${secciones}</details>
            <details style="margin-top:4px;"><summary style="cursor:pointer;font-size:11px;font-weight:900;color:#1e293b;padding:6px 0;">Por jugador (dorsal)</summary>${jug}</details>`;
    };

})();
