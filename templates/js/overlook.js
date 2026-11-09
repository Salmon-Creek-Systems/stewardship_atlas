/* Overlook — public progress page. Static: reads only the inlined summary and
 * the outlet's own data/*.geojson (plus public context layers by relative URL). */

const PALETTE = ['#2563eb', '#d97706', '#059669', '#db2777', '#7c3aed',
                 '#0891b2', '#dc2626', '#65a30d', '#c026d3', '#4b5563'];

const BASEMAP_STYLE = {
  version: 8,
  sources: {
    carto: {
      type: 'raster',
      tiles: ['https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors © CARTO',
    },
  },
  layers: [{ id: 'basemap', type: 'raster', source: 'carto', paint: { 'raster-opacity': 0.85 } }],
};

function el(tag, attrs, text) {
  const e = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => { if (v !== undefined && v !== null) e.setAttribute(k, v); });
  if (text !== undefined) e.textContent = text;
  return e;
}

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso.length <= 10 ? iso + 'T12:00:00' : iso);
  if (isNaN(d)) return iso;
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function dateRange(a, b) {
  if (!a) return '';
  return a === b || !b ? fmtDate(a) : `${fmtDate(a)} – ${fmtDate(b)}`;
}

function colorFor(layers) {
  const map = {};
  layers.forEach((l, i) => { map[l] = PALETTE[i % PALETTE.length]; });
  return map;
}

function fitTo(map, bbox) {
  if (!bbox) return;
  const [w, s, e, n] = bbox;
  if (w === e && s === n) { map.jumpTo({ center: [w, s], zoom: 15 }); return; }
  map.fitBounds([[w, s], [e, n]], { padding: 24, duration: 0, maxZoom: 16 });
}

function newMap(container, bbox) {
  const map = new maplibregl.Map({ container, style: BASEMAP_STYLE, attributionControl: { compact: true } });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  fitTo(map, bbox);
  return map;
}

function addContext(map, ctx) {
  (ctx || []).forEach((c) => {
    const id = 'ctx-' + c.layer;
    map.addSource(id, { type: 'geojson', data: c.url });
    map.addLayer({ id, type: 'line', source: id, paint: { 'line-color': '#555', 'line-width': 1.2, 'line-dasharray': [3, 2] } });
  });
}

function popupHtml(p) {
  const wrap = el('div');
  wrap.appendChild(el('strong', {}, p.layer));
  wrap.appendChild(el('div', {}, `${p.action || ''}${p.date ? ' · ' + fmtDate(p.date) : ''}`));
  if (p.name) wrap.appendChild(el('div', {}, p.name));
  if (p.source) wrap.appendChild(el('div', { class: 'muted' }, p.source));
  return wrap;
}

function renderMasthead(s) {
  document.title = `${s.title} — Overlook`;
  document.getElementById('title').textContent = s.title;
  if (s.tagline) { const t = document.getElementById('tagline'); t.textContent = s.tagline; t.hidden = false; }
  if (s.logo_url) { const l = document.getElementById('logo'); l.src = s.logo_url; l.hidden = false; }
  if (s.about) {
    document.getElementById('about').textContent = s.about;
    document.getElementById('about-section').hidden = false;
  }
  document.getElementById('asof').textContent = `Data as of ${fmtDate(s.built_at)}`;
  document.getElementById('built-at').textContent = fmtDate(s.built_at);
  // Built in staging and copied on publish: the same file is a preview in one
  // place and the release in the other, so the page decides by where it is.
  if (/\/staging\//.test(window.location.pathname)) document.getElementById('preview-banner').hidden = false;
}

function renderTiles(s) {
  const box = document.getElementById('headlines');
  (s.headlines || []).forEach((t) => {
    const tile = el('div', { class: 'tile' });
    tile.appendChild(el('div', { class: 'value' }, Number(t.value).toLocaleString()));
    tile.appendChild(el('div', { class: 'label' }, t.label));
    box.appendChild(tile);
  });
  if (!box.children.length) box.hidden = true;
}

function renderChanges(s, colors) {
  const since = document.getElementById('changes-since');
  since.textContent = s.since_date ? `Since the release of ${fmtDate(s.since_date)}.` : 'Since the atlas began.';
  const list = document.getElementById('changes');
  if (!s.changes.length) {
    list.appendChild(el('li', { class: 'muted' }, 'No changes yet since the last release.'));
  }
  s.changes.forEach((g) => {
    const li = el('li');
    li.appendChild(el('span', { class: 'swatch', style: `background:${colors[g.layer]}` }));
    li.appendChild(el('strong', {}, g.layer.replace(/_/g, ' ')));
    li.appendChild(document.createTextNode(': ' + g.parts.join(', ') + ' '));
    li.appendChild(el('span', { class: 'dates' }, '· ' + dateRange(g.first, g.last)));
    const srcs = Object.keys(g.sources || {});
    if (srcs.length) li.appendChild(el('span', { class: 'src' }, 'via ' + srcs.join(', ')));
    list.appendChild(li);
  });
  document.getElementById('change-truncated').hidden = !s.change_map_truncated;
}

function renderChangeMap(s, colors) {
  const map = newMap('change-map', s.change_bbox);
  const legend = document.getElementById('change-legend');
  s.changes.filter((g) => Object.keys(g.actions || {}).length).forEach((g) => {
    const li = el('li');
    li.appendChild(el('span', { class: 'swatch', style: `background:${colors[g.layer]}` }));
    li.appendChild(document.createTextNode(g.layer.replace(/_/g, ' ')));
    legend.appendChild(li);
  });
  const colorExpr = ['match', ['get', 'layer']];
  Object.entries(colors).forEach(([l, c]) => colorExpr.push(l, c));
  colorExpr.push('#4b5563');
  const isPoly = ['in', ['geometry-type'], ['literal', ['Polygon', 'MultiPolygon']]];
  const isLine = ['in', ['geometry-type'], ['literal', ['LineString', 'MultiLineString']]];
  const isPoint = ['in', ['geometry-type'], ['literal', ['Point', 'MultiPoint']]];
  const footprint = ['==', ['get', 'kind'], 'footprint'];

  map.on('load', () => {
    addContext(map, s.context_layers);
    map.addSource('changes', { type: 'geojson', data: 'data/changes.geojson' });
    // Bottom to top: footprints → polygons → lines → points.
    map.addLayer({ id: 'fp-annotate', type: 'line', source: 'changes',
      filter: ['all', footprint, ['==', ['get', 'action'], 'annotate']],
      paint: { 'line-color': colorExpr, 'line-width': 1.5, 'line-dasharray': [4, 2] } });
    map.addLayer({ id: 'fp-delete', type: 'line', source: 'changes',
      filter: ['all', footprint, ['==', ['get', 'action'], 'delete']],
      paint: { 'line-color': colorExpr, 'line-width': 1.5, 'line-dasharray': [1, 2] } });
    map.addLayer({ id: 'poly-fill', type: 'fill', source: 'changes',
      filter: ['all', ['!', footprint], isPoly], paint: { 'fill-color': colorExpr, 'fill-opacity': 0.35 } });
    map.addLayer({ id: 'poly-line', type: 'line', source: 'changes',
      filter: ['all', ['!', footprint], isPoly], paint: { 'line-color': colorExpr, 'line-width': 1.2 } });
    map.addLayer({ id: 'line-casing', type: 'line', source: 'changes',
      filter: ['all', ['!', footprint], isLine], paint: { 'line-color': '#fff', 'line-width': 5 } });
    map.addLayer({ id: 'line', type: 'line', source: 'changes',
      filter: ['all', ['!', footprint], isLine], paint: { 'line-color': colorExpr, 'line-width': 3 } });
    map.addLayer({ id: 'point', type: 'circle', source: 'changes',
      filter: ['all', ['!', footprint], isPoint],
      paint: { 'circle-color': colorExpr, 'circle-radius': 5, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5 } });
    ['poly-fill', 'line', 'point', 'fp-annotate', 'fp-delete'].forEach((id) => {
      map.on('click', id, (e) => {
        new maplibregl.Popup().setLngLat(e.lngLat).setDOMContent(popupHtml(e.features[0].properties)).addTo(map);
      });
      map.on('mouseenter', id, () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', id, () => { map.getCanvas().style.cursor = ''; });
    });
  });
}

function photoContent(p) {
  const wrap = el('div');
  const img = el('img', { src: p.image_url, alt: p.name || 'Field photo', class: 'popup-photo', loading: 'lazy' });
  wrap.appendChild(img);
  wrap.appendChild(el('strong', {}, p.name || 'Field photo'));
  wrap.appendChild(el('div', { class: 'muted' }, `${p.layer.replace(/_/g, ' ')} · ${fmtDate(p.timestamp)}`));
  const a = el('a', { href: p.map_url }, 'View on the map');
  wrap.appendChild(a);
  return wrap;
}

function openPhoto(p) {
  const d = document.getElementById('photo-dialog');
  document.getElementById('photo-dialog-img').src = p.image_url;
  document.getElementById('photo-dialog-img').alt = p.name || 'Field photo';
  document.getElementById('photo-dialog-caption').textContent =
    `${p.name || 'Field photo'} — ${p.layer.replace(/_/g, ' ')}, ${fmtDate(p.timestamp)}`;
  document.getElementById('photo-dialog-link').href = p.map_url;
  if (d.showModal) d.showModal(); else window.open(p.image_url);
}

function renderPhotos(s) {
  if (!s.photo_count) {
    document.getElementById('photos-count').textContent = 'No field photos yet — send the first one (see below).';
    document.getElementById('photo-map').hidden = true;
    return;
  }
  document.getElementById('photos-count').textContent =
    `${s.photo_count.toLocaleString()} photo${s.photo_count === 1 ? '' : 's'} from the field.`;
  renderPhotoStrip(s);
  if (typeof maplibregl === 'undefined') { document.getElementById('photo-map').hidden = true; return; }
  const map = newMap('photo-map', s.atlas_bbox);
  map.on('load', () => {
    addContext(map, s.context_layers);
    map.addSource('photos', { type: 'geojson', data: 'data/photos.geojson' });
    map.addLayer({ id: 'photos', type: 'circle', source: 'photos',
      paint: { 'circle-color': '#db2777', 'circle-radius': 5, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5 } });
    map.on('click', 'photos', (e) => {
      new maplibregl.Popup({ maxWidth: '240px' }).setLngLat(e.lngLat)
        .setDOMContent(photoContent(e.features[0].properties)).addTo(map);
    });
    map.on('mouseenter', 'photos', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'photos', () => { map.getCanvas().style.cursor = ''; });
  });
}

function renderPhotoStrip(s) {
  const strip = document.getElementById('photo-strip');
  (s.photos || []).forEach((p) => {
    const b = el('button', { type: 'button', title: p.name || 'Field photo' });
    b.appendChild(el('img', { src: p.image_url, alt: p.name || 'Field photo', loading: 'lazy' }));
    b.addEventListener('click', () => openPhoto(p));
    strip.appendChild(b);
  });
}

function renderProducts(s) {
  const body = document.getElementById('products');
  (s.products || []).forEach((p) => {
    const tr = el('tr');
    const name = el('td');
    if (p.href) name.appendChild(el('a', { href: p.href }, p.title)); else name.textContent = p.title;
    tr.appendChild(name);
    tr.appendChild(el('td', {}, p.access));
    tr.appendChild(el('td', {}, p.built ? fmtDate(p.built) : '—'));
    tr.appendChild(el('td', { class: 'status-' + p.status.replace(/ /g, '-') }, p.status));
    body.appendChild(tr);
  });
}

function renderInvolved(s) {
  const pe = document.getElementById('photo-email');
  pe.textContent = s.photo_email;
  pe.href = 'mailto:' + s.photo_email;
  if (s.contact_email) {
    const ce = document.getElementById('contact-email');
    ce.textContent = s.contact_email;
    ce.href = 'mailto:' + s.contact_email;
    document.getElementById('contact-help').hidden = false;
  }
}

function initializeOverlook(s) {
  const colors = colorFor(s.changes.map((g) => g.layer));
  renderMasthead(s);
  renderTiles(s);
  renderChanges(s, colors);
  renderProducts(s);
  renderInvolved(s);
  renderPhotos(s);
  if (typeof maplibregl === 'undefined') {  // offline / CDN blocked: text still renders
    document.getElementById('change-map').hidden = true;
    return;
  }
  renderChangeMap(s, colors);
}
