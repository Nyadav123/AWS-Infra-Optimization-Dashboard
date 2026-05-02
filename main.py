import os
import json
import html
import asyncio
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

import boto3
from cachetools import TTLCache
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from google import genai

# =========================
# CONFIG
# =========================
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable is missing.")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

app = FastAPI(title="AI Cloud Infrastructure Auditor")

HIGH_CPU_THRESHOLD = 85
LOW_CPU_THRESHOLD = 35

HIGH_MEM_THRESHOLD = 85
LOW_MEM_THRESHOLD = 40

METRIC_PERIOD_SECONDS = 300
METRIC_LOOKBACK_DAYS = 60

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

cache = TTLCache(maxsize=256, ttl=600)
metric_cache = TTLCache(maxsize=256, ttl=300)

# =========================
# REGIONS
# =========================
def get_regions():
    try:
        ec2 = boto3.client("ec2", region_name="ap-south-1")
        regions = [r["RegionName"] for r in ec2.describe_regions()["Regions"]]
        return regions or ["ap-south-1"]
    except Exception:
        return ["ap-south-1", "us-east-1", "us-west-2", "eu-west-1"]

REGIONS = get_regions()

# =========================
# UI
# =========================
STYLE = """
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/apexcharts"></script>
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
    *{box-sizing:border-box}
    body{
        font-family:'Inter',sans-serif;
        background:
            radial-gradient(circle at top right, rgba(99,102,241,.16), transparent 30%),
            radial-gradient(circle at bottom left, rgba(14,165,233,.14), transparent 28%),
            linear-gradient(180deg, #f8fafc, #eef2ff 40%, #f8fafc);
        color:#0f172a;
    }
    .hero{
        background:
          linear-gradient(135deg, rgba(15,23,42,.96), rgba(30,41,59,.94)),
          radial-gradient(circle at top right, rgba(99,102,241,.4), transparent 35%);
        color: white;
        overflow: hidden;
        position: relative;
    }
    .hero::after{
        content:'';
        position:absolute;
        inset:auto -140px -120px auto;
        width:340px;height:340px;
        background: radial-gradient(circle, rgba(99,102,241,.38), transparent 65%);
        filter: blur(10px);
    }
    .card{
        background: rgba(255,255,255,.85);
        border: 1px solid rgba(226,232,240,.9);
        border-radius: 1.75rem;
        box-shadow: 0 16px 40px rgba(15,23,42,.06);
        transition: all .25s ease;
    }
    .card:hover{
        transform: translateY(-4px);
        border-color: rgba(99,102,241,.45);
        box-shadow: 0 20px 55px rgba(15,23,42,.10);
    }
    .metric-card{
        background:#fff;
        border:1px solid #e2e8f0;
        border-radius: 2rem;
        padding: 1.1rem;
        box-shadow: 0 14px 40px rgba(15,23,42,.06);
    }
    .chart-wrap{
        background:#fff;
        border:1px solid #e2e8f0;
        border-radius: 2rem;
        padding: 1rem 1rem 0.5rem;
        box-shadow: 0 14px 40px rgba(15,23,42,.06);
        min-height: 430px;
        position:relative;
    }
    .chart-title{
        text-transform:uppercase;
        letter-spacing:.18em;
        font-size:.67rem;
        font-weight:900;
        color:#64748b;
        text-align:center;
        margin: .4rem 0 1rem;
    }
    .badge{
        display:inline-flex;
        align-items:center;
        gap:.35rem;
        padding:.32rem .7rem;
        border-radius:999px;
        font-size:.68rem;
        font-weight:800;
        text-transform:uppercase;
        letter-spacing:.08em;
        border:1px solid transparent;
        white-space:nowrap;
    }
    .badge-ok{background:#ecfdf5;color:#059669;border-color:#d1fae5}
    .badge-warn{background:#fffbeb;color:#d97706;border-color:#fde68a}
    .badge-bad{background:#fef2f2;color:#dc2626;border-color:#fecaca}
    .badge-neutral{background:#eff6ff;color:#2563eb;border-color:#bfdbfe}
    .navlink{color:#4f46e5;font-weight:800;text-transform:uppercase;letter-spacing:.14em;font-size:.68rem}
    .subtle{color:#64748b}
    .hero-stat{
        background: rgba(255,255,255,.08);
        border:1px solid rgba(255,255,255,.12);
        border-radius: 1.2rem;
        padding: 1rem;
    }
    .section-title{
        font-size: clamp(1.5rem, 3vw, 2.4rem);
        font-weight: 900;
        letter-spacing: -0.04em;
        color:#0f172a;
    }
    .small-caps{
        text-transform:uppercase;
        letter-spacing:.18em;
        font-weight:900;
        font-size:.68rem;
        color:#64748b;
    }
    .no-data{
        position:absolute;
        inset:0;
        display:flex;
        align-items:center;
        justify-content:center;
        color:#94a3b8;
        font-weight:800;
        font-size:.8rem;
        letter-spacing:.15em;
        text-transform:uppercase;
        background:rgba(255,255,255,.78);
        z-index:3;
        border-radius: 2rem;
        text-align:center;
        padding:1rem;
    }
</style>
"""

# =========================
# HELPERS
# =========================
def escape_text(value):
    return html.escape("" if value is None else str(value))

def allow_basic_html(text: str) -> str:
    safe = html.escape(text or "")
    safe = safe.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    safe = safe.replace("&lt;br&gt;", "<br>").replace("&lt;br/&gt;", "<br>").replace("&lt;br /&gt;", "<br>")
    return safe

def state_class(resource: str, state: str) -> str:
    s = (state or "").lower()
    if resource in ("ec2", "rds"):
        if s in ("running", "available"):
            return "badge-ok"
        return "badge-bad"
    if resource == "eip":
        return "badge-ok" if s == "associated" else "badge-bad"
    if resource == "volumes":
        return "badge-ok" if s == "in use" else "badge-warn"
    return "badge-neutral"

def resource_label(resource: str) -> str:
    return {
        "ec2": "EC2 Instances",
        "rds": "RDS Databases",
        "eip": "Elastic IPs",
        "volumes": "EBS Volumes",
    }.get(resource, resource.upper())

def resource_desc(resource: str) -> str:
    return {
        "ec2": "Deep performance view for compute fleets across all AWS regions.",
        "rds": "Live database health and capacity analysis across your global estate.",
        "eip": "Public IP inventory with association state and usage visibility.",
        "volumes": "Storage inventory for in-use and available EBS volumes.",
    }.get(resource, "AWS inventory dashboard.")

def get_cache_key(*parts):
    return ":".join(str(p) for p in parts)

def percentile(values, p):
    if not values:
        return None
    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    k = (len(vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    if f == c:
        return vals[f]
    return vals[f] + (vals[c] - vals[f]) * (k - f)

def ratio_above(values, threshold):
    if not values:
        return 0.0
    return sum(1 for v in values if v >= threshold) / len(values)

def ratio_below(values, threshold):
    if not values:
        return 0.0
    return sum(1 for v in values if v <= threshold) / len(values)

def series_values(series):
    return [float(v) for _, v in series] if series else []

def normalize_pressure_series(resource: str, values):
    if not values:
        return []
    if resource == "ec2":
        return [float(v) for v in values]
    peak = max(values) if values else 0
    if peak <= 0:
        return [0.0 for _ in values]
    return [max(0.0, 100.0 - ((float(v) / peak) * 100.0)) for v in values]

def resize_type(current_type: str, direction: str):
    ec2_map = {
        "t3": ["nano", "micro", "small", "medium", "large", "xlarge", "2xlarge"],
        "t3a": ["nano", "micro", "small", "medium", "large", "xlarge", "2xlarge"],
        "t4g": ["nano", "micro", "small", "medium", "large", "xlarge", "2xlarge"],
        "m5": ["large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge", "48xlarge"],
        "m6i": ["large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge", "32xlarge"],
        "c5": ["large", "xlarge", "2xlarge", "4xlarge", "9xlarge", "12xlarge", "18xlarge", "24xlarge", "metal"],
        "c6i": ["large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge", "32xlarge"],
        "r5": ["large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge", "metal"],
        "r6i": ["large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge", "32xlarge"],
    }
    rds_map = {
        "t3": ["micro", "small", "medium", "large", "xlarge", "2xlarge"],
        "t4g": ["micro", "small", "medium", "large", "xlarge", "2xlarge"],
        "m5": ["large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge", "48xlarge"],
        "m6i": ["large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge", "32xlarge"],
        "r5": ["large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge", "metal"],
        "r6i": ["large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge", "32xlarge"],
    }

    is_rds = current_type.startswith("db.")
    raw = current_type[3:] if is_rds else current_type
    if "." not in raw:
        return None

    family, size = raw.rsplit(".", 1)
    family_map = rds_map if is_rds else ec2_map
    sizes = family_map.get(family)
    if not sizes or size not in sizes:
        return None

    idx = sizes.index(size)
    new_idx = idx - 1 if direction == "down" else idx + 1
    if new_idx < 0 or new_idx >= len(sizes):
        return None

    prefix = "db." if is_rds else ""
    return f"{prefix}{family}.{sizes[new_idx]}"

def workload_profile_hint(resource: str, current_type: str) -> str:
    family = "unknown"
    if current_type.startswith("db."):
        raw = current_type[3:]
    else:
        raw = current_type
    if "." in raw:
        family = raw.split(".", 1)[0].lower()

    if family.startswith("t"):
        return "burstable / low to moderate load"
    if family.startswith("c"):
        return "compute intensive"
    if family.startswith("r"):
        return "memory intensive"
    if family.startswith("m"):
        return "balanced general purpose"
    return f"{resource.upper()} workload"

def infer_resize_direction(
    resource: str,
    cpu_avg: float,
    cpu_p95: float,
    mem_avg: float | None = None,
    mem_p95: float | None = None,
    mem_available: bool = True,
):
    # Degrade becomes eligible only when CPU is below 35% and memory is also low when available.
    low_cpu = cpu_avg <= LOW_CPU_THRESHOLD and cpu_p95 <= 45
    high_cpu = cpu_p95 >= HIGH_CPU_THRESHOLD or cpu_avg >= 70

    if resource == "rds":
        if mem_avg is None or mem_p95 is None or not mem_available:
            return "maintain"
        high_mem = mem_p95 >= HIGH_MEM_THRESHOLD or mem_avg >= 70
        low_mem = mem_avg <= LOW_MEM_THRESHOLD and mem_p95 <= 55
        if high_cpu or high_mem:
            return "up"
        if low_cpu and low_mem:
            return "down"
        return "maintain"

    if mem_available and mem_avg is not None and mem_p95 is not None:
        high_mem = mem_p95 >= HIGH_MEM_THRESHOLD or mem_avg >= 70
        low_mem = mem_avg <= LOW_MEM_THRESHOLD and mem_p95 <= 55
        if high_cpu or high_mem:
            return "up"
        if low_cpu and low_mem:
            return "down"
        return "maintain"

    if high_cpu:
        return "up"
    if low_cpu:
        return "down"
    return "maintain"

def choose_action_label(direction: str) -> str:
    return {"up": "UPGRADE", "down": "DEGRADE", "maintain": "MAINTAIN"}.get(direction, "MAINTAIN")

def _normalize_reason_text(text: str) -> str:
    clean = html.escape((text or "").strip())
    clean = clean.replace("&lt;br&gt;", " ").replace("&lt;br/&gt;", " ").replace("&lt;br /&gt;", " ")
    clean = clean.replace("\n", " ").replace("\r", " ")
    return clean[:220] if clean else ""

def _default_reason(action_label: str, resource: str, mem_available: bool) -> str:
    if action_label == "DEGRADE":
        return "CPU resources are significantly underutilized."
    if action_label == "UPGRADE":
        return "CPU or memory pressure is consistently high."
    if resource == "rds" and not mem_available:
        return "Memory data is missing, so a safe resize is not recommended yet."
    return "Current usage is balanced and does not justify a resize yet."

def build_analysis_html(
    action_label: str,
    current_type: str,
    suggested_type: str | None,
    resource: str,
    cpu_avg: float,
    cpu_p95: float,
    mem_avg: float | None,
    mem_p95: float | None,
    mem_available: bool,
    reason: str,
) -> str:
    target_text = f" to {escape_text(suggested_type)}" if suggested_type and action_label in ("UPGRADE", "DEGRADE") else ""
    reason_text = _normalize_reason_text(reason) or _default_reason(action_label, resource, mem_available)
    line_1 = f"<b>{action_label}{target_text}</b>"
    line_2 = reason_text
    line_3 = f"Current type: {escape_text(current_type)}"
    line_4 = f"CPU avg: {cpu_avg:.2f}% | CPU p95: {cpu_p95:.2f}%"
    if mem_available and mem_avg is not None and mem_p95 is not None:
        if resource == "rds":
            line_5 = f"Memory avg: {mem_avg:.2f} GB | Memory p95: {mem_p95:.2f} GB"
        else:
            line_5 = f"Memory avg: {mem_avg:.2f}% | Memory p95: {mem_p95:.2f}%"
    else:
        line_5 = "Memory data is unavailable, so the decision is CPU-driven."

    return "<br>".join([line_1, line_2, line_3, line_4, line_5])

# =========================
# CURRENT CONFIG
# =========================
def fetch_current_config_sync(resource: str, rid: str, region: str):
    try:
        if resource == "ec2":
            ec2 = boto3.client("ec2", region_name=region)
            resp = ec2.describe_instances(InstanceIds=[rid])
            inst = resp["Reservations"][0]["Instances"][0]
            instance_type = inst["InstanceType"]

            type_info = ec2.describe_instance_types(InstanceTypes=[instance_type])["InstanceTypes"][0]
            vcpu = type_info.get("VCpuInfo", {}).get("DefaultVCpus")
            memory_gb = round(type_info.get("MemoryInfo", {}).get("SizeInMiB", 0) / 1024, 1)

            return {
                "resource": "ec2",
                "current_type": instance_type,
                "vcpu": vcpu,
                "memory_gb": memory_gb,
            }

        if resource == "rds":
            rds = boto3.client("rds", region_name=region)
            paginator = rds.get_paginator("describe_db_instances")
            for page in paginator.paginate():
                for db in page.get("DBInstances", []):
                    if db["DBInstanceIdentifier"] == rid:
                        return {
                            "resource": "rds",
                            "current_type": db["DBInstanceClass"],
                            "engine": db.get("Engine"),
                            "allocated_storage_gb": db.get("AllocatedStorage"),
                        }

    except Exception as e:
        print(f"Current config fetch error for {resource} {rid} in {region}: {e}")

    return {}

# =========================
# DATA FETCH
# =========================
def fetch_ec2(region):
    rows = []
    try:
        c = boto3.client("ec2", region_name=region)
        for r in c.describe_instances()["Reservations"]:
            for i in r["Instances"]:
                name = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), "No Name")
                rows.append({
                    "id": i["InstanceId"],
                    "name": name,
                    "state": i["State"]["Name"],
                    "region": region,
                })
    except Exception as e:
        print(f"EC2 fetch error in {region}: {e}")
    return rows

def fetch_rds(region):
    rows = []
    try:
        c = boto3.client("rds", region_name=region)
        for db in c.describe_db_instances()["DBInstances"]:
            rows.append({
                "id": db["DBInstanceIdentifier"],
                "name": db["DBInstanceIdentifier"],
                "state": db["DBInstanceStatus"],
                "region": region,
            })
    except Exception as e:
        print(f"RDS fetch error in {region}: {e}")
    return rows

def fetch_eip(region):
    rows = []
    try:
        c = boto3.client("ec2", region_name=region)
        for e in c.describe_addresses()["Addresses"]:
            rows.append({
                "id": e.get("PublicIp", "Unknown"),
                "name": e.get("PublicIp", "Unknown"),
                "state": "Associated" if e.get("InstanceId") else "Unattached",
                "region": region,
            })
    except Exception as e:
        print(f"EIP fetch error in {region}: {e}")
    return rows

def fetch_volumes(region):
    rows = []
    try:
        c = boto3.client("ec2", region_name=region)
        for v in c.describe_volumes()["Volumes"]:
            rows.append({
                "id": v["VolumeId"],
                "name": v["VolumeId"],
                "state": "In Use" if v["State"] == "in-use" else "Available",
                "region": region,
            })
    except Exception as e:
        print(f"Volumes fetch error in {region}: {e}")
    return rows

FETCHERS = {
    "ec2": fetch_ec2,
    "rds": fetch_rds,
    "eip": fetch_eip,
    "volumes": fetch_volumes,
}

async def get_inventory(resource: str):
    key = get_cache_key("inventory", resource)
    if key in cache:
        return cache[key]
    tasks = [asyncio.to_thread(FETCHERS[resource], region) for region in REGIONS]
    results = await asyncio.gather(*tasks)
    items = [item for sub in results for item in sub]
    cache[key] = items
    return items

# =========================
# METRICS
# =========================
def build_metric_queries(resource: str, rid: str):
    if resource == "ec2":
        return [
            {"Id": "cpu", "MetricStat": {"Metric": {"Namespace": "AWS/EC2", "MetricName": "CPUUtilization", "Dimensions": [{"Name": "InstanceId", "Value": rid}]}, "Period": METRIC_PERIOD_SECONDS, "Stat": "Maximum"}},
            {"Id": "net_in", "MetricStat": {"Metric": {"Namespace": "AWS/EC2", "MetricName": "NetworkIn", "Dimensions": [{"Name": "InstanceId", "Value": rid}]}, "Period": METRIC_PERIOD_SECONDS, "Stat": "Sum"}},
            {"Id": "net_out", "MetricStat": {"Metric": {"Namespace": "AWS/EC2", "MetricName": "NetworkOut", "Dimensions": [{"Name": "InstanceId", "Value": rid}]}, "Period": METRIC_PERIOD_SECONDS, "Stat": "Sum"}},
            {"Id": "mem", "MetricStat": {"Metric": {"Namespace": "CWAgent", "MetricName": "mem_used_percent", "Dimensions": [{"Name": "InstanceId", "Value": rid}]}, "Period": METRIC_PERIOD_SECONDS, "Stat": "Maximum"}},
        ]
    if resource == "rds":
        return [
            {"Id": "cpu", "MetricStat": {"Metric": {"Namespace": "AWS/RDS", "MetricName": "CPUUtilization", "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": rid}]}, "Period": METRIC_PERIOD_SECONDS, "Stat": "Maximum"}},
            {"Id": "net_in", "MetricStat": {"Metric": {"Namespace": "AWS/RDS", "MetricName": "NetworkReceiveThroughput", "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": rid}]}, "Period": METRIC_PERIOD_SECONDS, "Stat": "Average"}},
            {"Id": "net_out", "MetricStat": {"Metric": {"Namespace": "AWS/RDS", "MetricName": "NetworkTransmitThroughput", "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": rid}]}, "Period": METRIC_PERIOD_SECONDS, "Stat": "Average"}},
            {"Id": "mem", "MetricStat": {"Metric": {"Namespace": "AWS/RDS", "MetricName": "FreeableMemory", "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": rid}]}, "Period": METRIC_PERIOD_SECONDS, "Stat": "Maximum"}},
            {"Id": "storage", "MetricStat": {"Metric": {"Namespace": "AWS/RDS", "MetricName": "FreeStorageSpace", "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": rid}]}, "Period": METRIC_PERIOD_SECONDS, "Stat": "Maximum"}},
        ]
    return []

def series_from_results(metric_results, key):
    item = metric_results.get(key)
    if not item or not item.get("Values"):
        return None
    ts = item.get("Timestamps", [])
    vals = item.get("Values", [])
    points = sorted([(int(t.timestamp() * 1000), float(v)) for t, v in zip(ts, vals)], key=lambda x: x[0])
    return points or None

def fetch_metrics_sync(resource: str, rid: str, region: str):
    key = get_cache_key("metrics", resource, rid, region)
    if key in metric_cache:
        return metric_cache[key]

    cw = boto3.client("cloudwatch", region_name=region)
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=METRIC_LOOKBACK_DAYS)

    queries = build_metric_queries(resource, rid)
    if not queries:
        return {}

    res = cw.get_metric_data(
        MetricDataQueries=queries,
        StartTime=start,
        EndTime=now,
        ScanBy="TimestampAscending",
    )

    metric_results = {m["Id"]: m for m in res.get("MetricDataResults", [])}
    metric_cache[key] = metric_results
    return metric_results

def summarize_metrics(resource: str, metric_results: dict):
    cpu = series_from_results(metric_results, "cpu") or []
    net_in = series_from_results(metric_results, "net_in") or []
    net_out = series_from_results(metric_results, "net_out") or []
    mem = series_from_results(metric_results, "mem") or []
    storage = series_from_results(metric_results, "storage") or []

    cpu_vals = [v for _, v in cpu]
    mem_vals = [v for _, v in mem]
    storage_vals = [v for _, v in storage]
    net_vals = [(i[1] + o[1]) for i, o in zip(net_in, net_out)]

    return {
        "cpu_max": max(cpu_vals) if cpu_vals else 0,
        "cpu_avg": sum(cpu_vals) / len(cpu_vals) if cpu_vals else 0,
        "cpu_p95": percentile(cpu_vals, 95) if cpu_vals else 0,
        "mem_avg": sum(mem_vals) / len(mem_vals) if mem_vals else 0,
        "mem_min": min(mem_vals) if mem_vals else 0,
        "mem_p95": percentile(mem_vals, 95) if mem_vals else 0,
        "storage_min": min(storage_vals) if storage_vals else 0,
        "net_max": max(net_vals) if net_vals else 0,
    }

def maybe_convert_gb(points):
    if not points:
        return None
    return [[ts, float(v) / (1024 ** 3)] for ts, v in points]

# =========================
# GEMINI
# =========================
def _gemini_generate(prompt: str) -> str:
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )
    return (response.text or "").strip()

async def _gemini_reason_only(
    resource: str,
    action_label: str,
    current_type: str,
    suggested_type: str | None,
    cpu_avg: float,
    cpu_p95: float,
    mem_avg: float | None = None,
    mem_p95: float | None = None,
    mem_available: bool = True,
) -> str:
    if action_label == "DEGRADE":
        base_instructions = "Explain why the resource should be degraded using one short sentence."
    elif action_label == "UPGRADE":
        base_instructions = "Explain why the resource should be upgraded using one short sentence."
    else:
        base_instructions = "Explain why the resource should be maintained using one short sentence."

    if mem_available and mem_avg is not None and mem_p95 is not None:
        prompt = f"""
You are an AWS capacity planner.

{base_instructions}

Fixed decision:
- Action: {action_label}
- Current type/class: {current_type}
- Suggested target type: {suggested_type or 'N/A'}
- Resource type: {resource.upper()}

Metrics:
- CPU average: {cpu_avg:.2f}%
- CPU 95th percentile: {cpu_p95:.2f}%
- Memory average: {mem_avg:.2f}
- Memory 95th percentile: {mem_p95:.2f}

Rules:
- Do not change the decision.
- Do not mention a different instance type.
- Return only one short sentence.
- No HTML, no bullets, no markdown.
"""
    else:
        prompt = f"""
You are an AWS capacity planner.

{base_instructions}

Fixed decision:
- Action: {action_label}
- Current type/class: {current_type}
- Suggested target type: {suggested_type or 'N/A'}
- Resource type: {resource.upper()}

Metrics:
- CPU average: {cpu_avg:.2f}%
- CPU 95th percentile: {cpu_p95:.2f}%

Rules:
- Do not change the decision.
- Do not mention a different instance type.
- Return only one short sentence.
- No HTML, no bullets, no markdown.
"""

    try:
        text = await asyncio.to_thread(_gemini_generate, prompt)
        return _normalize_reason_text(text) or _default_reason(action_label, resource, mem_available)
    except Exception as e:
        print(f"Gemini reason generation error for {resource} in {current_type}: {e}")
        return _default_reason(action_label, resource, mem_available)
async def get_ai_suggestion(
    resource: str,
    rid: str,
    region: str,
    current_type: str,
    cpu_avg: float,
    cpu_p95: float,
    mem_avg: float | None = None,
    mem_p95: float | None = None,
    mem_available: bool = True,
):
    key = get_cache_key(
        "ai",
        resource,
        rid,
        region,
        current_type,
        round(cpu_avg, 2),
        round(cpu_p95, 2),
        round(mem_avg or 0, 2),
        round(mem_p95 or 0, 2),
        mem_available,
    )

    if key in cache:
        return cache[key]

    # ✅ Pre-decide action
    if cpu_avg < 35:
        decision = "DEGRADE"
        suggested_type = resize_type(current_type, "down")
    elif cpu_p95 > 80:
        decision = "UPGRADE"
        suggested_type = resize_type(current_type, "up")
    else:
        decision = "MAINTAIN"
        suggested_type = current_type

    suggested_type = suggested_type or "No better size available"

    # ✅ Strict 2-line prompt
    prompt = f"""
You are an AWS expert.

Rules:
- CPU avg < 35% → DEGRADE
- CPU P95 > 80% → UPGRADE
- Else → MAINTAIN

Return EXACTLY 2 lines ONLY:

Line 1: <b>DECISION</b>
Line 2: Recommended instance type: <type> with short reason

No extra lines. No explanation beyond this.

Inputs:
Current type: {current_type}
CPU avg: {cpu_avg:.2f}%
CPU P95: {cpu_p95:.2f}%

Decision: {decision}
Suggested: {suggested_type}
"""

    try:
        text = await asyncio.to_thread(_gemini_generate, prompt)

        # ✅ Enforce 2 lines strictly
        lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
        if len(lines) >= 2:
            result = allow_basic_html(lines[0] + "<br>" + lines[1])
        else:
            raise ValueError("Invalid format")

        cache[key] = result
        return result

    except Exception as e:
        print(f"Gemini error: {e}")

        # ✅ Guaranteed fallback (2 lines only)
        fallback = f"""
<b>{decision}</b><br>
Recommended instance type: {suggested_type} due to CPU utilization pattern
"""
        cache[key] = fallback
        return fallback

# =========================
# RENDER HELPERS
# =========================
def render_home_stat(label, value, icon, hint):
    return f"""
    <div class="hero-stat">
        <div class="text-2xl mb-2">{icon}</div>
        <div class="text-2xl font-extrabold">{value}</div>
        <div class="text-sm font-semibold text-slate-200">{label}</div>
        <div class="text-xs text-slate-400 mt-1">{hint}</div>
    </div>
    """

def render_inventory_card(item, resource):
    rid = escape_text(item["id"])
    name = escape_text(item.get("name", item["id"]))
    region = escape_text(item["region"])
    state = escape_text(item["state"])
    badge = state_class(resource, item["state"])
    detail_url = f"/details/{resource}/{quote(item['id'])}?region={quote(item['region'])}&state={quote(item['state'])}&name={quote(item.get('name', item['id']))}"
    return f"""
    <a href="{detail_url}" class="card p-6 block">
        <div class="flex items-start justify-between gap-4">
            <div>
                <div class="font-extrabold text-slate-900 text-lg leading-snug">{name}</div>
                <div class="text-[10px] font-black text-slate-400 uppercase tracking-widest mt-2">{region} • {rid}</div>
            </div>
            <span class="badge {badge}">{state}</span>
        </div>
    </a>
    """

def render_state_tabs(resource, counts, current_filter=None):
    def tab(title, slug, count, active=False):
        bg = "bg-indigo-600 text-white" if active else "bg-white text-slate-700 border border-slate-200"
        return f"""
        <a href="/home/{resource}/{slug}" class="card px-5 py-4 inline-flex items-center justify-between gap-4 {bg}">
            <span class="font-bold">{title}</span>
            <span class="badge badge-neutral">{count}</span>
        </a>
        """
    return f"""
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
        {tab(counts["left"]["label"], counts["left"]["slug"], counts["left"]["count"], current_filter == counts["left"]["slug"])}
        {tab(counts["right"]["label"], counts["right"]["slug"], counts["right"]["count"], current_filter == counts["right"]["slug"])}
    </div>
    """

async def render_inventory(resource: str, filter_type: str | None = None):
    if resource not in FETCHERS:
        raise HTTPException(status_code=400, detail="Invalid resource type")

    items = await get_inventory(resource)

    if resource in ("ec2", "rds"):
        active_states = {"running", "available"}
        if filter_type == "active":
            items = [i for i in items if i["state"].lower() in active_states]
        elif filter_type == "idle":
            items = [i for i in items if i["state"].lower() not in active_states]
    elif resource == "eip":
        if filter_type == "associated":
            items = [i for i in items if i["state"] == "Associated"]
        elif filter_type == "unassociated":
            items = [i for i in items if i["state"] == "Unattached"]
    elif resource == "volumes":
        if filter_type == "inuse":
            items = [i for i in items if i["state"] == "In Use"]
        elif filter_type == "available":
            items = [i for i in items if i["state"] == "Available"]

    if resource in ("ec2", "rds"):
        active_count = len([i for i in items if i["state"].lower() in ("running", "available")])
        idle_count = len(items) - active_count
        tabs = {
            "left": {"label": "Active", "slug": "active", "count": active_count},
            "right": {"label": "Idle", "slug": "idle", "count": idle_count},
        }
    elif resource == "eip":
        assoc_count = len([i for i in items if i["state"] == "Associated"])
        unatt_count = len([i for i in items if i["state"] == "Unattached"])
        tabs = {
            "left": {"label": "Associated", "slug": "associated", "count": assoc_count},
            "right": {"label": "Unattached", "slug": "unassociated", "count": unatt_count},
        }
    else:
        inuse_count = len([i for i in items if i["state"] == "In Use"])
        avail_count = len([i for i in items if i["state"] == "Available"])
        tabs = {
            "left": {"label": "In Use", "slug": "inuse", "count": inuse_count},
            "right": {"label": "Available", "slug": "available", "count": avail_count},
        }

    cards = "\n".join(render_inventory_card(item, resource) for item in items) if items else """
        <div class="card p-8 text-center">
            <div class="text-slate-500 font-semibold">No resources found in this filter.</div>
        </div>
    """

    return f"""
    <html>
    <head>{STYLE}</head>
    <body class="min-h-screen">
        <div class="max-w-7xl mx-auto px-6 py-8 lg:px-10">
            <div class="flex items-center justify-between mb-8">
                <a href="/home" class="navlink">← Dashboard</a>
                <div class="text-right">
                    <div class="small-caps">{resource_label(resource)}</div>
                    <div class="text-sm font-bold text-slate-500">{resource_desc(resource)}</div>
                </div>
            </div>

            {render_state_tabs(resource, tabs, filter_type)}

            <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
                {cards}
            </div>
        </div>
    </body>
    </html>
    """

async def build_home():
    ec2 = await get_inventory("ec2")
    rds = await get_inventory("rds")
    eip = await get_inventory("eip")
    vol = await get_inventory("volumes")

    ec2_active = len([i for i in ec2 if i["state"].lower() == "running"])
    ec2_idle = len(ec2) - ec2_active

    rds_active = len([i for i in rds if i["state"].lower() == "available"])
    rds_idle = len(rds) - rds_active

    eip_assoc = len([i for i in eip if i["state"] == "Associated"])
    eip_unassoc = len(eip) - eip_assoc

    vol_inuse = len([i for i in vol if i["state"] == "In Use"])
    vol_available = len(vol) - vol_inuse

    return f"""
    <html>
    <head>{STYLE}</head>
    <body class="min-h-screen">
        <div class="max-w-7xl mx-auto px-6 py-8 lg:px-10">
            <section class="hero rounded-[2.5rem] p-8 lg:p-12 shadow-2xl mb-8">
                <div class="relative z-10 grid grid-cols-1 lg:grid-cols-[1.4fr_.9fr] gap-8 items-center">
                    <div>
                        <div class="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-white/10 border border-white/10 text-xs font-black uppercase tracking-[0.2em] text-indigo-200">
                            AI Cloud Infrastructure Auditor
                        </div>
                        <h1 class="mt-5 text-4xl lg:text-6xl font-black tracking-tight leading-[1.05]">
                            Smart visibility for your AWS fleet.
                        </h1>
                        <p class="mt-5 max-w-2xl text-base lg:text-lg text-slate-300 leading-relaxed">
                            Explore EC2, RDS, Elastic IPs, and EBS volumes from one dashboard.
                            Click any server or database to open a 60-day metric history with CPU, memory, bandwidth, and on-demand AI-backed optimization advice.
                        </p>
                        <div class="mt-8 flex flex-wrap gap-3">
                            <a href="/home/ec2" class="px-5 py-3 rounded-full bg-white text-slate-900 font-bold text-sm">Open EC2 fleet</a>
                            <a href="/home/rds" class="px-5 py-3 rounded-full bg-indigo-500 text-white font-bold text-sm">Open RDS fleet</a>
                            <a href="/home/eip" class="px-5 py-3 rounded-full bg-white/10 border border-white/10 text-white font-bold text-sm">View EIPs</a>
                            <a href="/home/volumes" class="px-5 py-3 rounded-full bg-white/10 border border-white/10 text-white font-bold text-sm">View Volumes</a>
                        </div>
                    </div>

                    <div class="grid grid-cols-2 gap-4">
                        {render_home_stat("Running EC2", ec2_active, "🖥️", f"{ec2_idle} idle instances waiting for review")}
                        {render_home_stat("Available RDS", rds_active, "🗄️", f"{rds_idle} idle databases to optimize")}
                        {render_home_stat("Associated EIPs", eip_assoc, "🌐", f"{eip_unassoc} unattached IPs ready for reuse")}
                        {render_home_stat("In-use Volumes", vol_inuse, "💾", f"{vol_available} volumes not attached to workloads")}
                    </div>
                </div>
            </section>

            <section class="mb-8">
                <div class="small-caps mb-2">Resource explorer</div>
                <h2 class="section-title">Choose the inventory view you need</h2>
                <p class="subtle mt-2 max-w-3xl">
                    Use the active and idle filters to quickly isolate resources that may need resizing, cleanup, or attention.
                </p>
            </section>

            <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-5">
                <a href="/home/ec2" class="card p-6">
                    <div class="text-3xl">🖥️</div>
                    <div class="mt-4 font-black text-xl text-slate-900">EC2</div>
                    <div class="mt-2 text-sm text-slate-500">Instance performance, bandwidth, memory, and AI recommendations.</div>
                </a>
                <a href="/home/rds" class="card p-6">
                    <div class="text-3xl">🗄️</div>
                    <div class="mt-4 font-black text-xl text-slate-900">RDS</div>
                    <div class="mt-2 text-sm text-slate-500">Database CPU, free memory, storage, and scaling guidance.</div>
                </a>
                <a href="/home/eip" class="card p-6">
                    <div class="text-3xl">🌐</div>
                    <div class="mt-4 font-black text-xl text-slate-900">Elastic IP</div>
                    <div class="mt-2 text-sm text-slate-500">See associated and unattached addresses across regions.</div>
                </a>
                <a href="/home/volumes" class="card p-6">
                    <div class="text-3xl">💾</div>
                    <div class="mt-4 font-black text-xl text-slate-900">Volumes</div>
                    <div class="mt-2 text-sm text-slate-500">Track in-use and available storage inventory.</div>
                </a>
            </div>
        </div>
    </body>
    </html>
    """

# =========================
# AI ANALYSIS API
# =========================
@app.get("/api/analyze/{resource}/{rid}")
async def analyze_resource(resource: str, rid: str, region: str = Query(...)):
    resource = resource.lower()

    if resource not in ("ec2", "rds"):
        raise HTTPException(status_code=400, detail="AI analysis is only available for EC2 and RDS")

    try:
        metrics = await asyncio.to_thread(fetch_metrics_sync, resource, rid, region)
        summary = summarize_metrics(resource, metrics)

        cpu_series = series_from_results(metrics, "cpu") or []
        mem_series = series_from_results(metrics, "mem") or []

        if not cpu_series:
            return {
                "ok": False,
                "html": "<b>ANALYSIS UNAVAILABLE</b><br>CPU data is required before generating a suggestion."
            }

        current_config = await asyncio.to_thread(fetch_current_config_sync, resource, rid, region)
        current_type = current_config.get("current_type", "N/A")

        cpu_avg = summary["cpu_avg"]
        cpu_p95 = summary["cpu_p95"]

        if resource == "rds":
            if not mem_series:
                return {
                    "ok": False,
                    "html": "<b>ANALYSIS UNAVAILABLE</b><br>CPU and memory data are required before generating a suggestion."
                }
            mem_avg = summary["mem_avg"] / (1024 ** 3)
            mem_p95 = summary["mem_p95"] / (1024 ** 3)
            ai_html = await get_ai_suggestion(
                resource=resource,
                rid=rid,
                region=region,
                current_type=current_type,
                cpu_avg=cpu_avg,
                cpu_p95=cpu_p95,
                mem_avg=mem_avg,
                mem_p95=mem_p95,
                mem_available=True,
            )
            return {"ok": True, "html": ai_html}

        if mem_series:
            mem_avg = summary["mem_avg"]
            mem_p95 = summary["mem_p95"]
            ai_html = await get_ai_suggestion(
                resource=resource,
                rid=rid,
                region=region,
                current_type=current_type,
                cpu_avg=cpu_avg,
                cpu_p95=cpu_p95,
                mem_avg=mem_avg,
                mem_p95=mem_p95,
                mem_available=True,
            )
        else:
            ai_html = await get_ai_suggestion(
                resource=resource,
                rid=rid,
                region=region,
                current_type=current_type,
                cpu_avg=cpu_avg,
                cpu_p95=cpu_p95,
                mem_avg=None,
                mem_p95=None,
                mem_available=False,
            )

        return {"ok": True, "html": ai_html}

    except Exception as e:
        print(f"Analysis error for {resource} {rid} in {region}: {e}")
        return {
            "ok": False,
            "html": "<b>ANALYSIS ERROR</b><br>Could not connect to the analysis service."
        }

# =========================
# ROUTES
# =========================
@app.get("/", response_class=HTMLResponse)
@app.get("/home", response_class=HTMLResponse)
async def home():
    return await build_home()

@app.get("/home/{resource}", response_class=HTMLResponse)
async def resource_page(resource: str):
    return await render_inventory(resource.lower())

@app.get("/home/{resource}/{filter_name}", response_class=HTMLResponse)
async def filtered_resource_page(resource: str, filter_name: str):
    return await render_inventory(resource.lower(), filter_name.lower())

@app.get("/details/{resource}/{rid}", response_class=HTMLResponse)
async def details(resource: str, rid: str, region: str = Query(...), state: str = Query(""), name: str = Query("")):
    resource = resource.lower()
    if resource not in FETCHERS:
        raise HTTPException(status_code=400, detail="Invalid resource type")

    safe_rid = escape_text(rid)
    safe_region = escape_text(region)
    safe_state = escape_text(state or "unknown")
    safe_name = escape_text(name or rid)

    if resource in ("ec2", "rds"):
        metrics = await asyncio.to_thread(fetch_metrics_sync, resource, rid, region)
        summary = summarize_metrics(resource, metrics)
        current_config = await asyncio.to_thread(fetch_current_config_sync, resource, rid, region)

        cpu = series_from_results(metrics, "cpu") or []
        net_in = series_from_results(metrics, "net_in") or []
        net_out = series_from_results(metrics, "net_out") or []
        mem = series_from_results(metrics, "mem") or []
        storage = series_from_results(metrics, "storage") or []

        cpu_data = cpu
        cpu_max = summary["cpu_max"]
        cpu_avg = summary["cpu_avg"]

        if resource == "ec2":
            mem_title = "Memory Used (%)"
            mem_unit = "%"
            mem_data = mem
            mem_overlay = "CLOUDWATCH AGENT NOT INSTALLED" if not mem_data else ""
            storage_block = ""
            storage_data = []
        else:
            mem_title = "Freeable Memory (GB)"
            mem_unit = " GB"
            mem_data = maybe_convert_gb(mem) or []
            mem_overlay = "METRIC UNAVAILABLE" if not mem_data else ""
            storage_data = maybe_convert_gb(storage) or []
            storage_block = f"""
                <div class="chart-wrap">
                    <div class="chart-title">Free Storage Space (GB)</div>
                    {'' if storage_data else "<div class='no-data'>METRIC UNAVAILABLE</div>"}
                    <div id="chart-storage"></div>
                </div>
            """

        net_data = []
        if net_in and net_out:
            net_points = []
            for (ts_i, in_val), (_, out_val) in zip(net_in, net_out):
                net_points.append([ts_i, ((float(in_val) + float(out_val)) * 8) / 3600 / 1048576])
            net_data = net_points
        net_overlay = "METRIC UNAVAILABLE" if not net_data else ""
        charts_col = "grid-cols-1 xl:grid-cols-2"
        net_span = "xl:col-span-2"
        state_badge_class = state_class(resource, state or ("running" if resource == "ec2" else "available"))

        current_type = current_config.get("current_type", "N/A")

        extra_info_1 = ""
        extra_info_2 = ""
        extra_label_1 = ""
        extra_label_2 = ""

        if resource == "ec2":
            extra_label_1 = "vCPU"
            extra_info_1 = str(current_config.get("vcpu", "N/A"))
            extra_label_2 = "Memory"
            extra_info_2 = f'{current_config.get("memory_gb", "N/A")} GB'
        else:
            extra_label_1 = "Engine"
            extra_info_1 = str(current_config.get("engine", "N/A"))
            extra_label_2 = "Allocated Storage"
            extra_info_2 = f'{current_config.get("allocated_storage_gb", "N/A")} GB'

        config_cards = f"""
            <section class="card p-8 mb-8">
                <div class="small-caps mb-3">Current configuration</div>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div class="metric-card">
                        <div class="small-caps">Current type</div>
                        <div class="mt-2 text-2xl font-black text-slate-900">{escape_text(current_type)}</div>
                    </div>
                    <div class="metric-card">
                        <div class="small-caps">{escape_text(extra_label_1)}</div>
                        <div class="mt-2 text-2xl font-black text-slate-900">{escape_text(extra_info_1)}</div>
                    </div>
                    <div class="metric-card">
                        <div class="small-caps">{escape_text(extra_label_2)}</div>
                        <div class="mt-2 text-2xl font-black text-slate-900">{escape_text(extra_info_2)}</div>
                    </div>
                </div>
            </section>
        """

        analysis_box = f"""
            <section class="card p-8 mb-8">
                <div class="small-caps mb-3">AI suggestion</div>
                <p class="text-slate-600 leading-relaxed">
                    Click the button below to analyze the resource. EC2 can still be analyzed with CPU-only fallback if memory data is missing. RDS requires both CPU and memory.
                </p>

                <div class="mt-5 flex flex-wrap items-center gap-3">
                    <button id="analyze-btn" class="px-5 py-3 rounded-full bg-indigo-600 text-white font-bold text-sm">
                        Analyze Suggestion
                    </button>
                    <span class="text-xs text-slate-500">AI is not called until you click.</span>
                </div>

                <div id="ai-result" class="mt-6 text-lg lg:text-xl font-semibold text-slate-800 leading-relaxed"></div>
            </section>
        """

        return f"""
        <html>
        <head>{STYLE}</head>
        <body class="min-h-screen">
            <div class="max-w-7xl mx-auto px-6 py-8 lg:px-10">
                <div class="flex items-center justify-between mb-8">
                    <a href="/home/{resource}" class="navlink">← Back to {resource_label(resource)}</a>
                    <span class="badge {state_badge_class}">{safe_state}</span>
                </div>

                <section class="hero rounded-[2.5rem] p-8 lg:p-10 mb-8">
                    <div class="relative z-10">
                        <div class="small-caps text-indigo-200">Performance details</div>
                        <h1 class="mt-3 text-3xl lg:text-5xl font-black tracking-tight">{safe_name}</h1>
                        <p class="mt-3 text-slate-300 max-w-3xl">
                            {resource_label(resource)} • {safe_region} • {safe_rid}
                        </p>
                        <div class="mt-6 flex flex-wrap gap-3">
                            <span class="badge badge-neutral">Last 60 days</span>
                            <span class="badge badge-neutral">CPU + memory + network</span>
                            <span class="badge badge-neutral">On-demand AI recommendation</span>
                        </div>
                    </div>
                </section>

                {config_cards}
                {analysis_box}

                <section class="card p-8 mb-8">
                    <div class="small-caps mb-3">Metrics summary</div>
                    <div class="mt-5 grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div class="metric-card">
                            <div class="small-caps">Peak CPU</div>
                            <div class="mt-2 text-2xl font-black text-slate-900">{cpu_max:.2f}%</div>
                        </div>
                        <div class="metric-card">
                            <div class="small-caps">Average CPU</div>
                            <div class="mt-2 text-2xl font-black text-slate-900">{cpu_avg:.2f}%</div>
                        </div>
                        <div class="metric-card">
                            <div class="small-caps">Region</div>
                            <div class="mt-2 text-xl font-black text-slate-900">{safe_region}</div>
                        </div>
                    </div>
                </section>

                <div class="grid {charts_col} gap-6">
                    <div class="chart-wrap">
                        <div class="chart-title">Peak CPU Performance (%)</div>
                        {'' if cpu_data else "<div class='no-data'>METRIC UNAVAILABLE</div>"}
                        <div id="chart-cpu"></div>
                    </div>

                    <div class="chart-wrap">
                        <div class="chart-title">{mem_title}</div>
                        {'' if mem_data else f"<div class='no-data'>{mem_overlay}</div>"}
                        <div id="chart-mem"></div>
                    </div>

                    {storage_block if resource == "rds" else ""}

                    <div class="chart-wrap {net_span}">
                        <div class="chart-title">Network Bandwidth (Mbps)</div>
                        {'' if net_data else f"<div class='no-data'>{net_overlay}</div>"}
                        <div id="chart-net"></div>
                    </div>
                </div>
            </div>

            <script>
                const cpuData = {json.dumps(cpu_data)};
                const memData = {json.dumps(mem_data)};
                const netData = {json.dumps(net_data)};
                const storageData = {json.dumps(storage_data if resource == "rds" else [])};

                const analyzeResource = {json.dumps(resource)};
                const analyzeRid = {json.dumps(rid)};
                const analyzeRegion = {json.dumps(region)};

                function base(color, data, name, unit) {{
                    return {{
                        series: [{{
                            name: name,
                            data: data
                        }}],
                        chart: {{
                            type: 'area',
                            height: 320,
                            toolbar: {{
                                show: true,
                                autoSelected: 'zoom',
                                tools: {{
                                    download: true,
                                    selection: false,
                                    zoom: true,
                                    zoomin: true,
                                    zoomout: true,
                                    pan: false,
                                    reset: false,
                                    customIcons: [
                                        {{
                                            icon: '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 3h6v6"></path><path d="M9 21H3v-6"></path><path d="M21 3l-7 7"></path><path d="M3 21l7-7"></path></svg>',
                                            index: 0,
                                            title: 'Fullscreen',
                                            click: function(chartContext) {{
                                                const el = chartContext.el.closest('.chart-wrap') || chartContext.el;

                                                if (!document.fullscreenElement) {{
                                                    el.requestFullscreen();
                                                }} else {{
                                                    document.exitFullscreen();
                                                }}

                                                setTimeout(function() {{
                                                    chartContext.updateOptions({{}}, true, true);
                                                }}, 250);
                                            }}
                                        }}
                                    ]
                                }}
                            }},
                            zoom: {{
                                enabled: true,
                                type: 'x',
                                autoScaleYaxis: true
                            }},
                            selection: {{
                                enabled: true,
                                type: 'x'
                            }}
                        }},
                        colors: [color],
                        stroke: {{
                            curve: 'smooth',
                            width: 3
                        }},
                        markers: {{
                            size: 0,
                            hover: {{
                                size: 5
                            }}
                        }},
                        dataLabels: {{
                            enabled: false
                        }},
                        grid: {{
                            borderColor: '#e2e8f0'
                        }},
                        xaxis: {{
                            type: 'datetime'
                        }},
                        yaxis: {{
                            labels: {{
                                formatter: function(v) {{
                                    return (v || 0).toFixed(2) + unit;
                                }}
                            }}
                        }},
                        tooltip: {{
                            enabled: true,
                            shared: false,
                            intersect: false,
                            x: {{
                                format: 'dd MMM yyyy, HH:mm'
                            }},
                            y: {{
                                formatter: function(v) {{
                                    return (v || 0).toFixed(2) + unit;
                                }}
                            }}
                        }},
                        fill: {{
                            type: 'gradient',
                            gradient: {{
                                opacityFrom: 0.42,
                                opacityTo: 0.08
                            }}
                        }}
                    }};
                }}

                if (cpuData.length) {{
                    new ApexCharts(document.querySelector("#chart-cpu"), base('#6366f1', cpuData, 'CPU', '%')).render();
                }}

                if (memData.length) {{
                    new ApexCharts(document.querySelector("#chart-mem"), base('#8b5cf6', memData, 'Memory', '{mem_unit}')).render();
                }}

                if (netData.length) {{
                    new ApexCharts(document.querySelector("#chart-net"), base('#10b981', netData, 'Bandwidth', ' Mbps')).render();
                }}

                const storageEl = document.querySelector("#chart-storage");
                if (storageEl && storageData.length) {{
                    new ApexCharts(storageEl, base('#f59e0b', storageData, 'Storage', ' GB')).render();
                }}

                const analyzeBtn = document.getElementById("analyze-btn");
                const aiResult = document.getElementById("ai-result");

                if (analyzeBtn && aiResult) {{
                    analyzeBtn.addEventListener("click", async () => {{
                        analyzeBtn.disabled = true;
                        analyzeBtn.innerText = "Analyzing...";
                        aiResult.innerHTML = '<div class="text-slate-500 font-semibold">Checking CPU and memory availability...</div>';

                        try {{
                            const url = `/api/analyze/${{encodeURIComponent(analyzeResource)}}/${{encodeURIComponent(analyzeRid)}}?region=${{encodeURIComponent(analyzeRegion)}}`;
                            const resp = await fetch(url, {{
                                headers: {{ "Accept": "application/json" }}
                            }});

                            let data;
                            try {{
                                data = await resp.json();
                            }} catch {{
                                throw new Error("Server returned an invalid response");
                            }}

                            if (!data || !data.ok) {{
                                aiResult.innerHTML = data?.html || "<b>ANALYSIS UNAVAILABLE</b><br>Could not generate a recommendation right now.";
                                return;
                            }}

                            aiResult.innerHTML = data.html;
                        }} catch (err) {{
                            aiResult.innerHTML = "<b>ANALYSIS ERROR</b><br>Could not connect to the analysis service.";
                        }} finally {{
                            analyzeBtn.disabled = false;
                            analyzeBtn.innerText = "Analyze Suggestion";
                        }}
                    }});
                }}
            </script>
        </body>
        </html>
        """

    return f"""
    <html>
    <head>{STYLE}</head>
    <body class="min-h-screen">
        <div class="max-w-4xl mx-auto px-6 py-10">
            <a href="/home/{resource}" class="navlink">← Back to {resource_label(resource)}</a>

            <section class="hero rounded-[2.5rem] p-8 lg:p-10 mt-6">
                <div class="relative z-10">
                    <div class="small-caps text-indigo-200">Resource details</div>
                    <h1 class="mt-3 text-3xl lg:text-5xl font-black tracking-tight">{safe_name}</h1>
                    <p class="mt-3 text-slate-300">{resource_label(resource)} • {safe_region} • {safe_rid}</p>
                </div>
            </section>

            <div class="card p-8 mt-8">
                <div class="small-caps mb-3">State</div>
                <span class="badge {state_class(resource, state)}">{safe_state}</span>
                <p class="mt-5 text-slate-600 leading-relaxed">
                    This resource type does not expose the same CPU, memory, and bandwidth metrics as EC2 or RDS.
                    Use the inventory view to inspect association or usage state.
                </p>
            </div>
        </div>
    </body>     
    </html>
    """

if __name__ == "__main__":  
    import multiprocessing
    multiprocessing.freeze_support()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
