/* Imagen IDA Y VUELTA (la de OBJ FINES DE SEMANA → IDA Y VUELTA → IMAGEN).
   La usan el botón IMAGEN de la intranet y la pantalla de la TV del almacén (/tv). */
function idaVueltaCanvas(obsData) {
    var W = 1080, H = 1920, M = 8, GAP = 8, NCOL = 4;
    var ALTO_TABLAS = 1390;
    var FUENTE = '"Arial Narrow", "Roboto Condensed", Arial, sans-serif';
    var ORD = { 'G': 3, 'E': 2, 'P': 1 };
    var COLOR_RES = { 'G': '#00a043', 'E': '#f08c00', 'P': '#e00000' };
    var ordenCat = {'AFICIONADO':0,'JUVENIL':1,'CADETE':2,'INFANTIL':3,'ALEVIN':4,'ALEVIN F7':5,'BENJAMIN':6,'PREBENJAMIN':7};
    var ordenar = function(a, b) {
        var ca = ordenCat[a.categoria] !== undefined ? ordenCat[a.categoria] : 99;
        var cb = ordenCat[b.categoria] !== undefined ? ordenCat[b.categoria] : 99;
        return ca !== cb ? ca - cb : a.letra.localeCompare(b.letra);
    };
    // Goles nuestros / suyos (resultado viene "local - visitante")
    var goles = function(p) {
        var m = (p.resultado || '').match(/(\d+)\s*-\s*(\d+)/);
        if (!m) return null;
        var a = +m[1], b = +m[2];
        return p.es_local === false ? [b, a] : [a, b];
    };
    var txtRes = function(p) {
        var g = goles(p);
        return g ? g[0] + '-' + g[1] + ' ' + (p.estado || '') : '';
    };
    // VUELTA respecto a la IDA: mejora verde · iguala amarillo · empeora rojo (P→P: amarillo si mejora la diferencia de goles)
    var colorVuelta = function(ida, vuelta) {
        if (!ida || !ida.estado || !vuelta || !vuelta.estado) return null;
        var vi = ORD[ida.estado] || 0, vv = ORD[vuelta.estado] || 0;
        if (vv > vi) return '#00b050';
        if (vv < vi) return '#ff0000';
        if (vuelta.estado !== 'P') return '#ffff00';
        var gi = goles(ida), gv = goles(vuelta);
        return (gi && gv && (gv[0] - gv[1]) > (gi[0] - gi[1])) ? '#ffff00' : '#ff0000';
    };

    // ── Datos de las tablas (igual que la vista IDA Y VUELTA) ──
    var grupoMap = {};
    obsData.forEach(function(p) {
        var key = p.categoria + '|' + p.letra;
        if (!grupoMap[key]) grupoMap[key] = { categoria: p.categoria, letra: p.letra, partidos: [] };
        grupoMap[key].partidos.push(p);
    });
    var bloques = Object.values(grupoMap).sort(ordenar).map(function(gr) {
        var rivales = {}, orden = [];
        gr.partidos.slice().sort(function(a, b) { return a.jornada_num - b.jornada_num; }).forEach(function(p) {
            var r = p.rival || '';
            if (!rivales[r]) { rivales[r] = []; orden.push(r); }
            rivales[r].push(p);
        });
        return { titulo: (gr.categoria + ' ' + gr.letra).trim(), filas: orden.map(function(r) { return { rival: r, ida: rivales[r][0] || null, vuelta: rivales[r][1] || null }; }) };
    });

    // ── Datos del gráfico (igual que SEGUIMIENTO TEMPORADA) ──
    var cntGrupos = {};
    obsData.forEach(function(p) {
        var k = p.categoria + ' ' + p.letra;
        if (!cntGrupos[k]) cntGrupos[k] = new Set();
        if (p.grupo_short) cntGrupos[k].add(p.grupo_short);
    });
    var eqMap = {}, maxJ = 0;
    obsData.forEach(function(p) {
        var base = p.categoria + ' ' + p.letra;
        var clave = (cntGrupos[base] && cntGrupos[base].size > 1 && p.grupo_short) ? base + ' [' + p.grupo_short + ']' : base;
        if (!eqMap[clave]) eqMap[clave] = { categoria: p.categoria, letra: p.letra, clave: clave, jornadas: {} };
        if (p.jornada_num < 999) { eqMap[clave].jornadas[p.jornada_num] = p; if (p.jornada_num > maxJ) maxJ = p.jornada_num; }
    });
    var equiposGraf = Object.values(eqMap).sort(ordenar);

    // ── Lienzo vertical ──
    var cv = document.createElement('canvas');
    cv.width = W; cv.height = H;
    var ctx = cv.getContext('2d');
    ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, W, H);
    ctx.textBaseline = 'middle';
    var recortar = function(t, maxW) {
        if (ctx.measureText(t).width <= maxW) return t;
        while (t.length > 1 && ctx.measureText(t + '…').width > maxW) t = t.slice(0, -1);
        return t + '…';
    };

    // Filas de bloques (4 por fila) y alto de línea para que todo quepa
    var filasBloques = [];
    for (var i = 0; i < bloques.length; i += NCOL) filasBloques.push(bloques.slice(i, i + NCOL));
    var lineas = filasBloques.reduce(function(s, fb) {
        return s + 1 + Math.max.apply(null, fb.map(function(b) { return b.filas.length; }));
    }, 0);
    var lh = Math.min(16, (ALTO_TABLAS - M - GAP * (filasBloques.length - 1)) / Math.max(lineas, 1));
    var fs = Math.max(6, Math.floor(lh * 0.78));
    var colW = (W - 2 * M - GAP * (NCOL - 1)) / NCOL;
    var resW = Math.round(colW * 0.2);

    var y = M;
    filasBloques.forEach(function(fb) {
        var maxF = Math.max.apply(null, fb.map(function(b) { return b.filas.length; }));
        fb.forEach(function(b, ci) {
            var x = M + ci * (colW + GAP);
            // Título del equipo
            ctx.font = 'bold ' + (fs + 1) + 'px ' + FUENTE;
            ctx.fillStyle = '#c00000'; ctx.textAlign = 'left';
            ctx.fillText(b.titulo, x + 2, y + lh / 2);
            ctx.fillStyle = '#c00000'; ctx.fillRect(x, y + lh - 1.5, colW, 1.5);
            // Filas
            b.filas.forEach(function(f, ri) {
                var yy = y + lh * (ri + 1);
                var xIda = x + colW - 2 * resW, xVue = x + colW - resW;
                var cVue = colorVuelta(f.ida, f.vuelta);
                if (cVue) { ctx.fillStyle = cVue; ctx.fillRect(xVue, yy, resW, lh); }
                ctx.strokeStyle = '#bfbfbf'; ctx.lineWidth = 0.6;
                ctx.strokeRect(x, yy, colW - 2 * resW, lh);
                ctx.strokeRect(xIda, yy, resW, lh);
                ctx.strokeRect(xVue, yy, resW, lh);
                ctx.fillStyle = '#000000';
                ctx.font = fs + 'px ' + FUENTE; ctx.textAlign = 'center';
                ctx.fillText(recortar(f.rival, colW - 2 * resW - 6), x + (colW - 2 * resW) / 2, yy + lh / 2);
                // IDA: texto en color del resultado (G verde · E naranja · P rojo)
                if (f.ida) {
                    ctx.font = 'bold ' + fs + 'px ' + FUENTE;
                    ctx.fillStyle = COLOR_RES[f.ida.estado] || '#000000';
                    ctx.fillText(txtRes(f.ida), xIda + resW / 2, yy + lh / 2);
                }
                // VUELTA: la celda ya va pintada (mejora/iguala/empeora) → texto negro para que se lea
                if (f.vuelta) {
                    ctx.font = 'bold ' + fs + 'px ' + FUENTE;
                    ctx.fillStyle = cVue ? '#000000' : (COLOR_RES[f.vuelta.estado] || '#000000');
                    ctx.fillText(txtRes(f.vuelta), xVue + resW / 2, yy + lh / 2);
                }
            });
        });
        y += lh * (maxF + 1) + GAP;
    });

    // ── Tabla SEGUIMIENTO TEMPORADA (igual que la vista: ✓ / ✗ por jornada, % Cump. y Detalle) ──
    if (maxJ > 0 && equiposGraf.length) {
        var gy0 = ALTO_TABLAS + 12, cabH = 18;
        var nomW = 118, pctW = 54, detW = 64;
        var gx0 = M + nomW, gW = W - M - detW - pctW - gx0;
        var colJ = gW / maxJ;
        var rowH = Math.min(24, (H - M - gy0 - cabH) / equiposGraf.length);
        var rad = Math.max(3, Math.min(rowH, colJ) * 0.36);
        // Cabecera
        ctx.fillStyle = '#1e293b';
        ctx.fillRect(M, gy0, W - 2 * M, cabH);
        ctx.textAlign = 'left'; ctx.fillStyle = '#ffffff';
        ctx.font = 'bold 11px ' + FUENTE;
        ctx.fillText('Equipo', M + 6, gy0 + cabH / 2);
        ctx.textAlign = 'center';
        ctx.font = 'bold ' + Math.max(7, Math.min(9, Math.floor(colJ * 0.36))) + 'px ' + FUENTE;
        ctx.fillStyle = '#94a3b8';
        for (var j = 1; j <= maxJ; j++) ctx.fillText('J.' + j, gx0 + colJ * (j - 0.5), gy0 + cabH / 2);
        ctx.fillStyle = '#ffffff'; ctx.font = 'bold 11px ' + FUENTE;
        ctx.fillText('% Cump.', gx0 + gW + pctW / 2, gy0 + cabH / 2);
        ctx.fillText('Detalle', gx0 + gW + pctW + detW / 2, gy0 + cabH / 2);

        var icono = function(cx, cy, tipo) {
            ctx.beginPath(); ctx.arc(cx, cy, rad, 0, Math.PI * 2);
            ctx.fillStyle = tipo === 'ok' ? '#16a34a' : (tipo === 'ko' ? '#dc2626' : '#94a3b8');
            ctx.fill();
            ctx.strokeStyle = '#ffffff'; ctx.lineWidth = Math.max(1.2, rad * 0.32); ctx.lineCap = 'round';
            ctx.beginPath();
            if (tipo === 'ok') {
                ctx.moveTo(cx - rad * 0.45, cy + rad * 0.02); ctx.lineTo(cx - rad * 0.1, cy + rad * 0.38); ctx.lineTo(cx + rad * 0.48, cy - rad * 0.32);
            } else if (tipo === 'ko') {
                ctx.moveTo(cx - rad * 0.38, cy - rad * 0.38); ctx.lineTo(cx + rad * 0.38, cy + rad * 0.38);
                ctx.moveTo(cx + rad * 0.38, cy - rad * 0.38); ctx.lineTo(cx - rad * 0.38, cy + rad * 0.38);
            } else {
                ctx.moveTo(cx - rad * 0.45, cy); ctx.lineTo(cx + rad * 0.45, cy);
            }
            ctx.stroke();
        };

        equiposGraf.forEach(function(eq, k) {
            var ry = gy0 + cabH + k * rowH, mid = ry + rowH / 2;
            var verde = 0, rojo = 0;
            // Fondo de nombre, % y detalle (como en la vista)
            ctx.fillStyle = '#f8fafc';
            ctx.fillRect(M, ry + 0.5, nomW, rowH - 1);
            ctx.fillRect(gx0 + gW, ry + 0.5, pctW + detW, rowH - 1);
            ctx.fillStyle = '#e2e8f0'; ctx.fillRect(M + nomW - 1.5, ry, 1.5, rowH);
            ctx.font = 'bold ' + Math.min(11, Math.floor(rowH * 0.5)) + 'px ' + FUENTE;
            ctx.fillStyle = '#1e293b'; ctx.textAlign = 'left';
            ctx.fillText(recortar(eq.clave, nomW - 10), M + 6, mid);
            ctx.textAlign = 'center';
            for (var j = 1; j <= maxJ; j++) {
                var cx = gx0 + colJ * (j - 0.5);
                var p = eq.jornadas[j];
                if (p && p.estado) {
                    if (p.cumplido === true) { verde++; icono(cx, mid, 'ok'); }
                    else if (p.cumplido === false) { rojo++; icono(cx, mid, 'ko'); }
                    else icono(cx, mid, 'sin');
                } else {
                    ctx.fillStyle = '#e2e8f0'; ctx.font = 'bold 11px ' + FUENTE;
                    ctx.fillText('·', cx, mid);
                }
            }
            var total = verde + rojo;
            var pct = total ? Math.round(verde / total * 100) : 0;
            ctx.font = 'bold ' + Math.min(13, Math.floor(rowH * 0.6)) + 'px ' + FUENTE;
            ctx.fillStyle = !total ? '#cbd5e1' : (pct >= 70 ? '#16a34a' : (pct >= 40 ? '#d97706' : '#dc2626'));
            ctx.fillText(total ? pct + '%' : '–', gx0 + gW + pctW / 2, mid);
            ctx.font = 'bold ' + Math.min(12, Math.floor(rowH * 0.55)) + 'px ' + FUENTE;
            var tOk = '✓' + verde, tKo = '✗' + rojo, sep = 6;
            var wOk = ctx.measureText(tOk).width, wKo = ctx.measureText(tKo).width;
            var dx = gx0 + gW + pctW + (detW - wOk - wKo - sep) / 2;
            ctx.textAlign = 'left';
            ctx.fillStyle = '#16a34a'; ctx.fillText(tOk, dx, mid);
            ctx.fillStyle = '#dc2626'; ctx.fillText(tKo, dx + wOk + sep, mid);
        });
    }

    // ── Girar 90° (texto de arriba a abajo) → 1920×1080 ──
    var out = document.createElement('canvas');
    out.width = H; out.height = W;
    var octx = out.getContext('2d');
    octx.translate(H, 0);
    octx.rotate(Math.PI / 2);
    octx.drawImage(cv, 0, 0);
    return out;
}
