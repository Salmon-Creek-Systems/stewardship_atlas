// Run the leg/smoothing helpers outside a browser with a stub map.
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const stubEl = () => ({addEventListener(){}, value: '', hidden: false, textContent: ''});
global.document = {getElementById: stubEl, querySelector: () => null};
global.window = {location: {search: ''}};
const map = {getMaxZoom: () => 18};
const body = src.replace(/^async function wrapper\(map, maplibregl\) \{/, '').replace(/\}\s*$/, '');
const f = new Function('map', 'maplibregl', body + '; return {buildRouteLegs, sampleAlong, smoothedAlong, easeBearing};');
const R = f(map, {});
// L-shaped line, 1 km each way, with a lift array
const fc = {features: [{geometry: {type: 'LineString', coordinates: [[-123.8, 40.1], [-123.788, 40.1], [-123.788, 40.109]]},
                        properties: {camera_lift_m: [0, 100, 0]}}]};
const [leg] = R.buildRouteLegs(fc);
console.log('legs len m', Math.round(leg.len), 'lifts', leg.lifts);
const corner = leg.cum[1];
const raw = R.sampleAlong(leg, corner), sm = R.smoothedAlong(leg, corner);
console.log('at corner raw', raw.pt.map(v => v.toFixed(5)), 'lift', raw.lift, '| smoothed', sm.pt.map(v => v.toFixed(5)), 'lift', sm.lift.toFixed(1));
console.log('no lift array ->', R.buildRouteLegs({features: [{geometry: fc.features[0].geometry, properties: {}}]})[0].lifts);
console.log('bad-length lift ->', R.buildRouteLegs({features: [{geometry: fc.features[0].geometry, properties: {camera_lift_m: [1, 2]}}]})[0].lifts);
console.log('ease 350->10 over 0.6s', R.easeBearing(350, 10, 0.6).toFixed(1), '| null ->', R.easeBearing(null, 42, 0.1), '| dt 0 ->', R.easeBearing(90, 180, 0));
