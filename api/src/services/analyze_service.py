# (C) Copyright 2026 Telefónica Innovación Digital (alexandremiquel.frauamar.practicas@telefonica.com, antonio.pastorperales@telefonica.com)
# Modified for the PQ-REACT Katana/5G testbed integration (IPsec + iperf mode, MCP live push).

import time
import requests
import logging
import json

from datetime import datetime, timedelta
from flask import jsonify, current_app
import src.services.test_suites_service as test_suites_service
import src.utils.metrics_collection_manager as metrics_collection_manager
from src.models.test_run import TestRun
from src.models.test_run_metric import TestRunMetric
from src.enums.status import Status
from src.enums.metric import Metric

WAIT_MS = 15

# ── Phase A integration: live push to MCP MariaDB after each test_runs row ──
_MCP_ALG_ALIASES = {
    'classical': 'classical', 'ecdsa-p256': 'classical', 'none': 'classical',
    'kyber512': 'mlkem512', 'mlkem512': 'mlkem512',
    'kyber768': 'mlkem768', 'mlkem768': 'mlkem768',
    'kyber1024': 'mlkem1024', 'mlkem1024': 'mlkem1024',
}
_MCP_SEC_LEVEL = {'classical': 0, 'mlkem512': 1, 'mlkem768': 3, 'mlkem1024': 5}


def _mcp_canon(name):
    return _MCP_ALG_ALIASES.get((name or '').strip().lower(), (name or '').strip().lower())


def _push_to_mcp(*, alg, message_size, kem_total_ms, source_id):
    """Best-effort UPSERT into the MCP MariaDB performance_test table.
    Failures are logged but never block the main test_runs insert."""
    cfg = current_app.configurations
    host = getattr(cfg, 'mcp_db_host', None)
    if not host:
        return  # MCP integration disabled (env vars not set)
    try:
        import pymysql
        canon = _mcp_canon(alg)
        conn = pymysql.connect(
            host=host,
            port=int(getattr(cfg, 'mcp_db_port', 3306)),
            user=cfg.mcp_db_user,
            password=cfg.mcp_db_password,
            db=cfg.mcp_db_name,
            connect_timeout=3,
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO performance_test
                  (algorithm_name, message_size, duration, security_level,
                   source, size_kind)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  duration       = VALUES(duration),
                  security_level = VALUES(security_level)
                """,
                (canon, int(message_size or 0), float(kem_total_ms or 0),
                 _MCP_SEC_LEVEL.get(canon),
                 'campaign-b-qujata-live', 'payload'),
            )
        conn.commit()
        conn.close()
        logging.info('MCP live push: %s msg=%s kem=%sms (test_run=%s)',
                     canon, message_size, kem_total_ms, source_id)
    except Exception as e:
        logging.warning('MCP live push failed (non-fatal): %s', e)


def analyze(data):

    # TLS payload -> iperf adapter for the trimmed deployment.
    # The portal "Run experiment" button POSTs {algorithms, iterationsCount,
    # messageSizes} to /analyze, which routes through the legacy TLS path
    # that needs CURL_URL configured. CURL_URL is empty in this deployment,
    # so every TLS-mode run lands as FAILED with 'CURL_URL not configured'.
    # Translate the TLS-shape payload into an iperf-shape payload AND drop
    # the TLS-shape keys so the dispatch below falls into the iperf branch
    # (which talks to IPERF_URL on the slicing testbed).
    #
    # iterationsCount → connections mapping: TLS "iterations" mean N crypto
    # operations; iperf has no direct analog, but the iperf path stores
    # `connections` count as `test_run.iterations`, so mapping the user's
    # iterationsCount into the connections field makes the portal's
    # Iterations column reflect their input. Clamped to [1, 8] (the safety
    # cap from launch_qujata_experiment).
    if ('algorithms' in data and 'iterationsCount' in data and 'messageSizes' in data
            and not getattr(current_app.configurations, 'curl_url', None)):
        _algs = data.get('algorithms', []) or ['mlkem768']
        _msgs = data.get('messageSizes', [1200]) or [1200]
        _iters_raw = data.get('iterationsCount', [100]) or [100]
        # Map each user-iteration count to a connections count. 100 → 1,
        # 200 → 2, ..., 800 → 8, ≥800 → 8 (capped).
        _conns = [max(1, min(8, (int(i) // 100) or 1)) for i in _iters_raw]
        _slice = data.get('sliceId', 'mgmt')
        data = {k: v for k, v in data.items()
                if k not in ('algorithms', 'iterationsCount', 'messageSizes')}
        data['ipsecAlgorithms']  = _algs
        data['time']             = [10]
        data['connections']      = _conns
        data['messageSizeIperf'] = _msgs
        data['bandwidth']        = [50]
        data['intervals']        = [1]
        data['sliceId']          = _slice
    if 'algorithms' in data and 'iterationsCount' in data and 'messageSizes' in data:
        # TLS mode (unchanged - unused now, CURL_URL is empty)
        test_suite = test_suites_service.create_test_suite(data)
        start_time = int(datetime.timestamp(datetime.now() - timedelta(seconds=60)) * 1000)
        for algorithm in data.get('algorithms', []):
            for iterations in data.get('iterationsCount', []):
                for message_size in data.get('messageSizes', []) or [0]:
                    __create_test_run(algorithm, iterations, message_size, test_suite.id)
                    time.sleep(WAIT_MS)
    else:
        # IPsec + iperf mode (Katana integration)
        test_suite = test_suites_service.create_test_suite(data)
        start_time = int(datetime.timestamp(datetime.now() - timedelta(seconds=60)) * 1000)
        slice_id = data.get('sliceId', 'mgmt')
        for ipsec_algorithm in data.get('ipsecAlgorithms', []):
            for time_iperf in data.get('time', []):
                for connection in data.get('connections', []):
                    for msg in data.get('messageSizeIperf', []):
                        for bw in data.get('bandwidth', []):
                            for interval in data.get('intervals', []):
                                __run_and_record_iperf(ipsec_algorithm, time_iperf, connection,
                                                       msg, bw*1000, interval, slice_id, test_suite.id)

    end_time = int(datetime.timestamp(datetime.now() + timedelta(seconds=90)) * 1000)
    test_suite.start_time = start_time
    test_suite.end_time = end_time
    test_suites_service.update_test_suite(test_suite)
    return jsonify({'test_suite_id': test_suite.id})


def __create_test_run(algorithm, iterations, message_size, test_suite_id):
    start_time = datetime.now()
    metrics_collection_manager.start_collecting()
    status, status_message, requests_size = __run(algorithm, iterations, message_size)
    metrics_collection_manager.stop_collecting()
    end_time = datetime.now()
    test_suites_service.create_test_run(start_time, end_time, algorithm, iterations, message_size,
                                        test_suite_id, status, status_message, requests_size,
                                        *metrics_collection_manager.get_metrics())


def __run(algorithm, iterations, message_size):
    payload = {'algorithm': algorithm, 'iterationsCount': iterations, 'messageSize': message_size}
    headers = {'Content-Type': 'application/json'}
    curl_url = current_app.configurations.curl_url
    if not curl_url:
        return Status.FAILED, 'CURL_URL not configured', 0
    response = requests.post(curl_url + "/curl", headers=headers, json=payload,
                             timeout=int(current_app.configurations.request_timeout))
    return __validate_response(response)


def __run_and_record_iperf(ipsec_algorithm, time_iperf, connection, msg, bandwidth, interval,
                            slice_id, test_suite_id):
    cfg = current_app.configurations
    headers = {'Content-Type': 'application/json'}
    timeout = int(cfg.request_timeout)

    # Optional: switch IKE proposal via secgw adapters
    switch_payload = {'ipsecAlgorithm': ipsec_algorithm}
    # path-b-real-metrics-rollup: time the secgw /script POST so kem_total_ms below
    # is the REAL IKE rekey latency, not jitter*10. Take the max
    # across the two gateways (the slowest one bounds end-to-end).
    _secgw_kem_ms = 0.0
    for label, url in (('secgw1', cfg.secgw1_url), ('secgw2', cfg.secgw2_url)):
        if not url:
            continue
        try:
            import time as _t_ike
            _t0 = _t_ike.perf_counter()
            r = requests.post(url.rstrip('/') + '/script', headers=headers, json=switch_payload, timeout=30)
            _elapsed_ms = (_t_ike.perf_counter() - _t0) * 1000.0
            # secgw_service.py /script returns { elapsed_ms, ok, alg }.
            # Prefer that (script-internal time excludes HTTP RTT) but
            # fall back to our wall-clock if the response isn't JSON.
            try:
                _body = r.json()
                _elapsed_ms = float(_body.get('elapsed_ms') or _elapsed_ms)
            except Exception:
                pass
            if _elapsed_ms > _secgw_kem_ms:
                _secgw_kem_ms = _elapsed_ms
            logging.info('%s: %s (%.1fms)', label, r.status_code, _elapsed_ms)
        except Exception as e:
            logging.warning('%s switch failed: %s', label, e)
    if cfg.secgw1_url or cfg.secgw2_url:
        time.sleep(0.5)

    # Build and run iperf payload
    iperf_payload = {
        'ipsecAlgorithm': ipsec_algorithm,
        'time': time_iperf,
        'connections': connection,
        'messageSizeIperf': msg,
        'bandwidth': bandwidth,
        'intervals': interval,
        'sliceId': slice_id,
    }
    start_time = datetime.now()
    status = Status.SUCCESS
    status_msg_obj = {'ipsec_algorithm': ipsec_algorithm, 'slice_id': slice_id}
    summary = {}
    kem = {}
    if not cfg.iperf_url:
        status = Status.FAILED
        status_msg_obj['error'] = 'IPERF_URL not configured'
    else:
        try:
            logging.info('iPerf call: %s', json.dumps(iperf_payload))
            r = requests.post(cfg.iperf_url.rstrip('/') + '/script',
                              headers=headers, json=iperf_payload, timeout=timeout)
            if r.status_code >= 400:
                status = Status.FAILED
                status_msg_obj['error'] = f'adapter HTTP {r.status_code}'
                status_msg_obj['response'] = r.text[:400]
            else:
                resp = r.json()
                summary = resp.get('summary') or {}
                kem = resp.get('kem') or {}
                status_msg_obj.update({
                    'kem_total_ms': round(_secgw_kem_ms, 3) if _secgw_kem_ms > 0 else resp.get('kem_total_ms'),
                    'mbps': summary.get('mbps'),
                    'jitter_ms': summary.get('jitter_ms'),
                    'lost_percent': summary.get('lost_percent'),
                    'lost_packets': summary.get('lost_packets'),
                    'packets': summary.get('packets'),
                })
        except Exception as e:
            status = Status.FAILED
            status_msg_obj['error'] = str(e)
    end_time = datetime.now()

    # Insert test_run row
    test_run = TestRun(
        start_time=start_time,
        end_time=end_time,
        algorithm=ipsec_algorithm,
        iterations=int(connection),
        message_size=int(msg),
        status=status,
        status_message=json.dumps(status_msg_obj)[:1900],
        test_suite_id=test_suite_id,
    )
    current_app.database_manager.create(test_run)

    # Phase A: live push to MCP MariaDB (best-effort, non-blocking)
    _push_to_mcp(alg=ipsec_algorithm,
                 message_size=msg,
                 kem_total_ms=status_msg_obj.get('kem_total_ms'),
                 source_id=test_run.id)

    # Record metrics so Grafana panels have something to query
    if status == Status.SUCCESS and summary:
        def _metric(name, value):
            if value is None:
                return
            current_app.database_manager.create(
                TestRunMetric(test_run_id=test_run.id, metric_name=name, value=float(value))
            )
        _metric(Metric.BYTES_THROUGHPUT_PER_SECOND, (summary.get('bits_per_second') or 0) / 8.0)
        _metric(Metric.MESSAGES_THROUGHPUT_PER_SECOND, (summary.get('packets') or 0) / max(time_iperf, 1))
        _metric(Metric.ERROR_RATE, summary.get('lost_percent'))
        _metric(Metric.AVERAGE_TLS_HANDSHAKE_TIME, (kem or {}).get('total_ms', 0.0))

        # path-b-real-metrics-rollup-stats: persist real CPU + memory metrics for both
        # sides of the IPsec tunnel:
        #
        #   client side — taken from iperf-runner's `local_stats` field
        #     (sampled DURING the run by a background thread on SGW1, so
        #     the value reflects iperf3+charon load while traffic is
        #     actually flowing — not the post-run 0% we'd get otherwise).
        #
        #   server side — fetched from SGW2's /stats endpoint AFTER iperf
        #     completes. iperf3 -s daemon stays running so we still see
        #     its decryption cost; charon's IKE rekey is also attributed
        #     here from earlier in the experiment window.
        try:
            ls = (resp.get('local_stats') or {})
            if ls:
                _metric(Metric.CLIENT_AVERAGE_CPU,    round(float(ls.get('cpu_percent_max')  or 0), 2))
                _metric(Metric.CLIENT_AVERAGE_MEMORY, round(float(ls.get('memory_mb_max')    or 0), 2))
            try:
                if cfg.secgw2_url:
                    r3 = requests.get(cfg.secgw2_url.rstrip('/') + '/stats', timeout=4)
                    if r3.status_code == 200:
                        body = r3.json()
                        _metric(Metric.SERVER_AVERAGE_CPU,    round(float(body.get('cpu_percent') or 0), 2))
                        _metric(Metric.SERVER_AVERAGE_MEMORY, round(float(body.get('memory_mb')   or 0), 2))
            except Exception as _e_srv:
                logging.warning('secgw2 stats failed: %s', _e_srv)
        except Exception as _e_outer:
            logging.warning('secgw stats outer error: %s', _e_outer)


def __validate_response(response):
    data = response.json()
    if response.status_code < 200 or response.status_code > 299:
        return Status.FAILED, json.dumps(data), 0
    return Status.SUCCESS, "", data.get('totalRequestSize')
