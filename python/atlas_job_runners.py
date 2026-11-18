"""What each job kind does (atlas_jobs.KINDS), each in its own session.

The same runners serve both consumers; ``target`` changes only how much is
hydrated and how long to wait for a busy atlas:

* ``lambda`` hydrates the job's declared inputs (``asset_inputs``) into an
  empty ``/tmp``, and does not wait: a busy atlas raises ``AtlasLocked`` and
  the message comes back shortly.
* ``box`` hydrates everything. Its workspace persists, so only changed files
  download, and its heavy jobs (QGIS, GDAL, Chromium) are exactly the ones
  the differential check does not cover. It waits a while for the lease,
  because every redelivery counts toward the dead-letter limit.

Heavy imports are lazy so this module loads where QGIS or Dagster are absent.
"""
import asset_inputs
import atlas_session

BOX_LEASE_WAIT = 600.0     # seconds


def _session(job, inputs=None, wanted=None):
    on_box = job.get('target') == 'box'
    label = job['params'].get('asset') or job['params'].get('layer') or ''
    return atlas_session.open_session(
        job['atlas'], purpose=f"job {job['kind']} {label} ({job['id']})".strip(),
        wait=BOX_LEASE_WAIT if on_box else 0.0,
        inputs=None if on_box else inputs, wanted=None if on_box else wanted)


def run_materialize(job):
    import atlas
    asset = job['params']['asset']
    with _session(job, inputs=lambda c: asset_inputs.asset_inputs(c, asset)) as session:
        if asset not in (session.config.get('assets') or {}):
            raise ValueError(f"no asset '{asset}' in atlas '{job['atlas']}'")
        return atlas.materialize(session.config, asset)


def run_refresh_layer(job):
    import atlas_dagster
    p = job['params']
    layer, mode, cascade = p['layer'], p.get('mode', 'update'), bool(p.get('cascade', True))
    with _session(job, inputs=lambda c: asset_inputs.refresh_layer_job_inputs(
            c, layer, mode, cascade)) as session:
        result = atlas_dagster.refresh_layer(session.config, layer, mode=mode, cascade=cascade)
        if not result.success:
            raise RuntimeError(f"refresh_layer {layer} ({mode}) did not succeed")
        return f"refreshed {layer} ({mode}, cascade={cascade})"


def run_publish(job):
    import versioning
    # Publish promotes the staging catalog server-side; only the config is read.
    with _session(job, wanted=asset_inputs.wanted(asset_inputs.publish_inputs())) as session:
        return versioning.publish_new_version(session.config)


def run_slides(job):
    import outlets_slides
    p = job['params']
    with _session(job) as session:
        config = session.config
        config['assets']['slides'] = {'type': 'outlet', 'config': {'fetch_type': 'outlet_slides'},
                                      'tour': p['tour'], 'gslides': bool(p.get('gslides'))}
        return outlets_slides.outlet_slides(config, 'slides')


RUNNERS = {
    'materialize': run_materialize,
    'refresh_layer': run_refresh_layer,
    'publish': run_publish,
    'slides': run_slides,
}
