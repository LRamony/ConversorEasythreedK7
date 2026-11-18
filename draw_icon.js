// Ícone do Conversor K7 desenhado em pixel art dentro do LibreSprite.
// Executado assim:
//   libresprite -b _blank.png --script draw_icon.js --save-as icon.png

const img = app.activeSprite.layer(0).cel(0).image;
const col = app.pixelColor;
const W = img.width;
const H = img.height;

function C(r, g, b, a) {
    return col.rgba(r, g, b, a === undefined ? 255 : a);
}

function px(x, y, c) {
    x = Math.round(x);
    y = Math.round(y);
    if (x >= 0 && y >= 0 && x < W && y < H) img.putPixel(x, y, c);
}

function rect(x0, y0, x1, y1, c) {
    for (let y = y0; y <= y1; y++) {
        for (let x = x0; x <= x1; x++) px(x, y, c);
    }
}

function roundRect(x0, y0, x1, y1, r, c) {
    for (let y = y0; y <= y1; y++) {
        for (let x = x0; x <= x1; x++) {
            const dx = x < x0 + r ? x0 + r - x : (x > x1 - r ? x - (x1 - r) : 0);
            const dy = y < y0 + r ? y0 + r - y : (y > y1 - r ? y - (y1 - r) : 0);
            if (dx * dx + dy * dy <= r * r) px(x, y, c);
        }
    }
}

function inside(x, y, points) {
    let hit = false;
    for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
        const xi = points[i][0], yi = points[i][1];
        const xj = points[j][0], yj = points[j][1];
        if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) {
            hit = !hit;
        }
    }
    return hit;
}

function poly(points, c) {
    let minX = W, minY = H, maxX = 0, maxY = 0;
    for (const p of points) {
        minX = Math.min(minX, p[0]); maxX = Math.max(maxX, p[0]);
        minY = Math.min(minY, p[1]); maxY = Math.max(maxY, p[1]);
    }
    for (let y = minY; y <= maxY; y++) {
        for (let x = minX; x <= maxX; x++) {
            if (inside(x, y, points)) px(x, y, c);
        }
    }
}

// --- paleta ---
const BORDA   = C(38, 72, 108);
const FUNDO   = C(13, 27, 42);
const CONTORNO= C(6, 18, 30);
const TOPO    = C(92, 205, 245);
const ESQ     = C(38, 133, 194);
const DIR     = C(20, 88, 145);
const MESA    = C(38, 168, 145);
const MESA_LAT= C(18, 105, 94);
const BICO    = C(245, 176, 66);
const VERDE   = C(60, 214, 125);

// fundo arredondado com borda
roundRect(8, 8, 247, 247, 30, BORDA);
roundRect(12, 12, 243, 243, 27, FUNDO);

// base da impressora / mesa
poly([[44, 190], [212, 190], [236, 212], [20, 212]], CONTORNO);
poly([[48, 190], [208, 190], [230, 208], [26, 208]], MESA);
poly([[20, 212], [236, 212], [236, 224], [20, 224]], MESA_LAT);
rect(20, 224, 236, 228, CONTORNO);

// cubo (contorno + três faces)
poly([[128, 46], [198, 86], [128, 126], [58, 86]], CONTORNO);       // topo
poly([[58, 86], [128, 126], [128, 184], [58, 144]], CONTORNO);      // esquerda
poly([[198, 86], [128, 126], [128, 184], [198, 144]], CONTORNO);    // direita

poly([[128, 54], [184, 86], [128, 118], [72, 86]], TOPO);
poly([[66, 90], [124, 122], [124, 176], [66, 141]], ESQ);
poly([[190, 90], [132, 122], [132, 176], [190, 141]], DIR);

// brilho no topo
poly([[128, 60], [168, 82], [128, 104], [88, 82]], C(140, 226, 252));

// bico extrusor
rect(124, 12, 132, 30, BICO);
poly([[114, 30], [142, 30], [128, 54]], CONTORNO);
poly([[118, 32], [138, 32], [128, 48]], BICO);

// marca de conclusão (check) no canto
poly([[196, 168], [206, 178], [224, 154], [232, 162], [206, 196], [188, 178]], CONTORNO);
poly([[198, 170], [206, 178], [222, 158], [227, 163], [206, 191], [193, 178]], VERDE);

console.log('icone desenhado em ' + W + 'x' + H);
