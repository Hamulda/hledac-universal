"""
Sample CVE Data for Testing CveCorrelationMatrix

ISSUE [ULTIMATE]-004: Provides sample data for initial testing.

NOTE: For production use, run:
    python -m hledac.universal.knowledge.cve_data_loader --update
"""

# Sample CVE records for testing
SAMPLE_CVE_DATA = [
    # nginx CVEs
    {
        "cve_id": "CVE-2021-23017",
        "technology": "nginx",
        "version_pattern": r"1\.18\..*",
        "cvss_score": 9.1,
        "cwe_id": "CWE-78",
        "description_snippet": "nginx resolver vulnerability allows DNS rebinding attacks",
        "published_date": "2021-06-02",
    },
    {
        "cve_id": "CVE-2022-41741",
        "technology": "nginx",
        "version_pattern": r"1\.18\..*",
        "cvss_score": 7.5,
        "cwe_id": "CWE-400",
        "description_snippet": "nginx mp4 module vulnerability via specially crafted media file",
        "published_date": "2022-10-18",
    },
    {
        "cve_id": "CVE-2023-44487",
        "technology": "nginx",
        "version_pattern": r"1\.1[89]\..*",
        "cvss_score": 7.5,
        "cwe_id": "CWE-400",
        "description_snippet": "HTTP/2 Rapid Reset Attack (affects nginx if used as reverse proxy)",
        "published_date": "2023-10-10",
    },
    # OpenSSH CVEs
    {
        "cve_id": "CVE-2020-15778",
        "technology": "openssh",
        "version_pattern": r"8\.[23]p1",
        "cvss_score": 6.8,
        "cwe_id": "CWE-269",
        "description_snippet": "scp command allows arbitrary file overwrite",
        "published_date": "2020-07-27",
    },
    {
        "cve_id": "CVE-2021-28041",
        "technology": "openssh",
        "version_pattern": r"8\.[45]p1",
        "cvss_score": 9.8,
        "cwe_id": "CWE-122",
        "description_snippet": "ssh-agent allows arbitrary code execution via PKCS#11",
        "published_date": "2021-03-10",
    },
    {
        "cve_id": "CVE-2024-6387",
        "technology": "openssh",
        "version_pattern": r"8\.[5-9]p1",
        "cvss_score": 9.8,
        "cwe_id": "CWE-362",
        "description_snippet": "regreSSHion: RCE vulnerability in sshd",
        "published_date": "2024-07-01",
    },
    # Apache CVEs
    {
        "cve_id": "CVE-2021-41773",
        "technology": "apache",
        "version_pattern": r"2\.4\.4[0-9]",
        "cvss_score": 7.5,
        "cwe_id": "CWE-22",
        "description_snippet": "Path traversal vulnerability in mod_proxy",
        "published_date": "2021-10-05",
    },
    {
        "cve_id": "CVE-2021-42013",
        "technology": "apache",
        "version_pattern": r"2\.4\.4[0-9]",
        "cvss_score": 9.8,
        "cwe_id": "CWE-22",
        "description_snippet": "Path traversal in mod_proxy with double encoding",
        "published_date": "2021-10-07",
    },
    {
        "cve_id": "CVE-2023-27522",
        "technology": "apache",
        "version_pattern": r"2\.4\.[0-9]+",
        "cvss_score": 8.6,
        "cwe_id": "CWE-78",
        "description_snippet": "HTTP request smuggling in Apache Traffic Server",
        "published_date": "2023-03-07",
    },
    # WordPress CVEs
    {
        "cve_id": "CVE-2021-29447",
        "technology": "wordpress",
        "version_pattern": r"5\.7\..*",
        "cvss_score": 8.1,
        "cwe_id": "CWE-78",
        "description_snippet": "WordPress stored XSS via comment navigation",
        "published_date": "2021-04-15",
    },
    {
        "cve_id": "CVE-2021-29450",
        "technology": "wordpress",
        "version_pattern": r"5\.7\..*",
        "cvss_score": 9.1,
        "cwe_id": "CWE-94",
        "description_snippet": "Remote code execution via XML entity expansion",
        "published_date": "2021-04-15",
    },
    # PostgreSQL CVEs
    {
        "cve_id": "CVE-2024-1597",
        "technology": "postgresql",
        "version_pattern": r"1[3-16]\..*",
        "cvss_score": 10.0,
        "cwe_id": "CWE-89",
        "description_snippet": "SQL injection via pg_dump extension",
        "published_date": "2024-02-13",
    },
    # Redis CVEs
    {
        "cve_id": "CVE-2023-22458",
        "technology": "redis",
        "version_pattern": r"[5-7]\.[0-9]+",
        "cvss_score": 9.1,
        "cwe_id": "CWE-94",
        "description_snippet": "Integer overflow in Lua scripts allows code execution",
        "published_date": "2023-01-17",
    },
    {
        "cve_id": "CVE-2022-31144",
        "technology": "redis",
        "version_pattern": r"7\.0\.[0-8]",
        "cvss_score": 8.8,
        "cwe_id": "CWE-94",
        "description_snippet": "Lua script argument injection in Redis Stack",
        "published_date": "2022-07-15",
    },
    # Kubernetes CVEs
    {
        "cve_id": "CVE-2023-47108",
        "technology": "kubernetes",
        "version_pattern": r"1\.(2[5-8]|29)\..*",
        "cvss_score": 8.8,
        "cwe_id": "CWE-306",
        "description_snippet": "Missing RBAC permission check in kube-apiserver",
        "published_date": "2023-11-08",
    },
    {
        "cve_id": "CVE-2024-24786",
        "technology": "kubernetes",
        "version_pattern": r"1\.(2[5-9]|30)\..*",
        "cvss_score": 9.8,
        "cwe_id": "CWE-835",
        "description_snippet": "Infinite loop vulnerability in golang protobuf parsing",
        "published_date": "2024-04-09",
    },
    # Docker CVEs
    {
        "cve_id": "CVE-2024-21626",
        "technology": "docker",
        "version_pattern": r"(25|26)\..*",
        "cvss_score": 8.6,
        "cwe_id": "CWE-78",
        "description_snippet": "Container breakout via leaked file descriptors",
        "published_date": "2024-01-31",
    },
    # MySQL CVEs
    {
        "cve_id": "CVE-2024-20953",
        "technology": "mysql",
        "version_pattern": r"8\.(0|1)\..*",
        "cvss_score": 6.5,
        "cwe_id": "CWE-89",
        "description_snippet": "SQL injection in MySQL Protocol component",
        "published_date": "2024-01-16",
    },
]


def load_sample_data(matrix):
    """Load sample data into CveCorrelationMatrix."""
    return matrix.load_cve_data(SAMPLE_CVE_DATA)
