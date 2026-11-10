# QUJATA on the PQ-REACT Katana/5G testbed

How the QUJATA instance on the PQ-REACT testbed (VM role: `pqreact-QUJATA-TID`)
was deployed. This snapshot was taken on 2026-11-10, before the VM was deleted.
Hosts are named by role here. The real addresses are in the testbed inventory,
not in this repository.

## Roles

| Role | What runs there | Used by QUJATA via |
|---|---|---|
| QUJATA host (this VM) | portal, api, mysql, prometheus, pushgateway, grafana, cadvisor (Docker) | n/a |
| SecGW1 node | secgw adapter `:30666` (`/script` switches the IKE proposal), iperf-runner `:30667`, iperf metrics `:30100` | `SECGW1_URL`, `IPERF_URL`, `IPERF_METRICS_URL` |
| SecGW2 node | secgw adapter `:30666` (`/script`, `/stats`) | `SECGW2_URL` |
| Katana slice manager | NBI `:8000` | `KATANA_NBI_URL` |
| Katana KPI exporter | `:8080` | `KATANA_KPI_URL` |
| MCP server stack (same VM, separate compose project) | `pqreact-mcp-mariadb` on docker network `server_mcp` | `MCP_DB_*` |

The secgw1/secgw2/iperf/curl/nginx containers from the upstream
`run/docker/docker-compose.yml` were **not** used. The IPsec tunnel and iperf
run on the slicing testbed nodes and are driven over HTTP.

## What ran

- The checkout was `pq-react/QUJATA-Telefonica` `main` @ `4eb183d`, with local (uncommitted)
  changes. Those changes are now committed on this branch:
  - `api/` adds the Katana/5G IPsec+iperf mode and the MCP live push (see `api/src/services/analyze_service.py`).
  - `run/docker/docker-compose.pqreact.yml` holds the trimmed compose file (on the VM it was `docker-compose.yml`).
  - `run/docker/grafana/dashboard.pqreact.json` is the MySQL-backed "Qujata Analysis" dashboard (on the VM it was `grafana/dashboard.json`).
  - `run/docker/grafana/pm_metrics.json` is the "PM_metrics" dashboard, exported from the Grafana DB.
  - `run/docker/grafana/datasource.pqreact.yaml` provisions the `qujata-mysql` and `cam-influxdb` datasources, which had been created in the UI.
- Compose project name: `docker` (the directory name). The network was therefore `docker_myservices-net3`.
- The code inside the running `qujata-api` container was identical to the checkout.
- Image versions that ran: grafana 13.0.1, prometheus 3.11.2, pushgateway 1.11.2,
  cAdvisor v0.49.1, and MySQL 9.6.0 (from `mysql/Dockerfile`). The api image is python 3.9.
- Published ports: portal `2000`, api `3020`, grafana `3000`, prometheus `9090`,
  pushgateway `9091`, mysql `3306`, cadvisor `8089`.

### Portal flow

The portal's "Run experiment" button POSTs the TLS-shaped payload
`{algorithms, iterationsCount, messageSizes}` to `/qujata-api/analyze`. `CURL_URL` is
empty, so the api converts that payload into an iperf run, with
`iterationsCount/100` becoming the connection count (clamped to 1..8), 10 s,
50 Mbit/s and slice `mgmt`. For each algorithm the api then does the following:

1. It POSTs `/script` to SecGW1 and SecGW2 to switch the IKE proposal. The slowest switch time is recorded as `kem_total_ms`.
2. It POSTs `/script` to the iperf-runner.
3. It writes a `test_runs` row with mbps, jitter, loss and packets in `status_message` (JSON), plus `test_run_metrics` rows. Client CPU and memory come from iperf-runner `local_stats`. Server CPU and memory come from SecGW2 `/stats`.
4. When `MCP_DB_HOST` is set, it makes a best-effort UPSERT into MCP `performance_test` with `source='campaign-b-qujata-live'`.

The Grafana "Qujata Analysis" dashboard reads those rows directly from `qujata-mysql`.

## Redeploy

```bash
cd run/docker
cp .env.example .env        # fill in endpoints and passwords
# MCP stack first (it owns the server_mcp network), or just:
docker network create server_mcp
docker compose -p docker -f docker-compose.pqreact.yml up -d --build
```

Set `MCP_DB_HOST=` (empty) in `.env` to disable the MCP push.

### cAdvisor

On the VM, cAdvisor was **not** started by compose. It ran standalone, and
`docker inspect qujata-cadvisor` shows it was started as follows:

```bash
docker run -d --name qujata-cadvisor --restart unless-stopped --privileged \
  --network docker_myservices-net3 -p 8089:8080 \
  -v /:/rootfs:ro -v /var/run/docker.sock:/var/run/docker.sock \
  -v /run/containerd/containerd.sock:/run/containerd/containerd.sock \
  -v /sys:/sys:ro -v /var/lib/docker/:/var/lib/docker:ro \
  gcr.io/cadvisor/cadvisor:v0.49.1 \
  --containerd=/run/containerd/containerd.sock --docker_only=true
```

The `cadvisor` service in `docker-compose.pqreact.yml` reproduces these
arguments. Use one or the other, because both set the same `container_name`.
Prometheus scrapes it as `qujata-cadvisor:8080`
(`run/docker/prometheus/prometheus.yml`, unchanged from upstream).

## Caveats

- The compose file declared `grafana_data` but did not mount it, so the
  Grafana DB (users, the UI-created datasources and the PM_metrics dashboard)
  lived only in the container layer. Dashboards and datasources are now
  provisioned from files. Uncomment the volume to persist anything else.
- The `cam-influxdb` datasource (CAM project, NUC power metrics) points at a
  container that was not running at snapshot time, so PM_metrics stays empty
  until a CAM InfluxDB is reachable and `CAM_INFLUXDB_TOKEN` is set.
- Experiment results (the MySQL `qujata` DB) and the Prometheus TSDB were
  in Docker volumes on the VM. They are not part of this repository.
