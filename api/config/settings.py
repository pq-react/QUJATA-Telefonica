# (C) Copyright 2026 Telefónica Innovación Digital (alexandremiquel.frauamar.practicas@telefonica.com, antonio.pastorperales@telefonica.com)
# Modified for the PQ-REACT Katana/5G testbed integration (IPsec + iperf mode, MCP live push).

import logging
import os
from dotenv import load_dotenv
from src.enums.environment import Environment


def load_config(app):
    load_dotenv()
    app.configurations = Configuration({
        'environment': os.environ.get('ENVIRONMENT'),
        'log_level': os.environ.get('LOG_LEVEL', 'INFO'),
        'allowed_algorithms': os.environ.get('DEFAULT_GROUPS',"mlkem512:frodo640aes").split(":"),
        'curl_url': os.environ.get('CURL_URL'),
        'cadvisor_url': os.environ.get('CADVISOR_URL'),
        'request_timeout': os.environ.get('REQUEST_TIMEOUT', 3600),
        'code_release': os.environ.get('CODE_RELEASE'),
        'protocol': os.environ.get('PROTOCOL'),
        'iterations_options': list(map(int, os.environ.get('ITERATIONS_OPTIONS', "100:500:1000:2000:5000:10000:50000").split(':'))),
        'message_sizes_options': list(map(int, os.environ.get('MESSAGE_SIZES_OPTIONS', "0:1:2:100:1024:102400:204800:1048576:2097152:10485760").split(':'))),
        # --- Katana/5G integration additions ---
        'secgw1_url': os.environ.get('SECGW1_URL', ''),
        'secgw2_url': os.environ.get('SECGW2_URL', ''),
        'iperf_url':  os.environ.get('IPERF_URL',  ''),
        'iperf_metrics_url': os.environ.get('IPERF_METRICS_URL', ''),
        'katana_nbi_url':    os.environ.get('KATANA_NBI_URL', ''),
        'katana_kpi_url':    os.environ.get('KATANA_KPI_URL', ''),
        # --- Phase A: live ETL into MCP MariaDB ---
        'mcp_db_host':     os.environ.get('MCP_DB_HOST', ''),
        'mcp_db_port':     int(os.environ.get('MCP_DB_PORT', '3306')),
        'mcp_db_name':     os.environ.get('MCP_DB_NAME', 'PQREACT'),
        'mcp_db_user':     os.environ.get('MCP_DB_USER', 'pqreact'),
        'mcp_db_password': os.environ.get('MCP_DB_PASSWORD', ''),
    })


class Configuration:
    def __init__(self, config_dict):
        self.__validate_environment(config_dict.get('environment'))
        self.__configure_logging(config_dict.get('log_level', 'INFO'))
        self.environment = config_dict.get('environment')
        self.allowed_algorithms = config_dict.get('allowed_algorithms')
        self.curl_url = config_dict.get('curl_url')
        self.cadvisor_url = config_dict.get('cadvisor_url')
        self.request_timeout = config_dict.get('request_timeout')
        self.code_release = config_dict.get('code_release')
        self.protocol = config_dict.get('protocol')
        self.iterations_options = config_dict.get('iterations_options')
        self.message_sizes_options = config_dict.get('message_sizes_options')
        # --- Katana/5G integration additions ---
        self.secgw1_url = config_dict.get('secgw1_url')
        self.secgw2_url = config_dict.get('secgw2_url')
        self.iperf_url  = config_dict.get('iperf_url')
        self.iperf_metrics_url = config_dict.get('iperf_metrics_url')
        self.katana_nbi_url    = config_dict.get('katana_nbi_url')
        self.katana_kpi_url    = config_dict.get('katana_kpi_url')
        # --- Phase A: live ETL into MCP MariaDB ---
        self.mcp_db_host     = config_dict.get('mcp_db_host')
        self.mcp_db_port     = config_dict.get('mcp_db_port')
        self.mcp_db_name     = config_dict.get('mcp_db_name')
        self.mcp_db_user     = config_dict.get('mcp_db_user')
        self.mcp_db_password = config_dict.get('mcp_db_password')

    def __validate_environment(self, environment):
        valid_environments = [e.value for e in Environment]
        if environment not in valid_environments:
            raise ValueError(f"Invalid environment: {environment}. Must be one of {valid_environments}")

    def __configure_logging(self, log_level):
        logging.basicConfig(level=getattr(logging, log_level.upper(), None))
