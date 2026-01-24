# Scalable Image Processing Pipeline Architecture

## Overview

A distributed, fault-tolerant image processing system designed to handle **1000+ images/second** with support for resize, compress, watermark, and OCR operations.

---

## Architecture Diagram

```
                                    ┌─────────────────────────────────────────────────────────────┐
                                    │                     MONITORING LAYER                        │
                                    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
                                    │  │ Prometheus  │  │   Grafana   │  │    PagerDuty/       │  │
                                    │  │  (Metrics)  │  │ (Dashboards)│  │    AlertManager     │  │
                                    │  └─────────────┘  └─────────────┘  └─────────────────────┘  │
                                    └─────────────────────────────────────────────────────────────┘
                                                              ▲
                                                              │ metrics/alerts
┌──────────────┐                                              │
│   Clients    │                                              │
│  (API/SDK)   │                                              │
└──────┬───────┘                                              │
       │                                                      │
       ▼                                                      │
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                        INGESTION LAYER                                           │
│  ┌────────────────────┐    ┌────────────────────┐    ┌────────────────────────────────────────┐  │
│  │   API Gateway      │───▶│  Rate Limiter      │───▶│         Load Balancer (ALB)           │  │
│  │   (Kong/AWS)       │    │  (Redis-based)     │    │         (Health checks)               │  │
│  └────────────────────┘    └────────────────────┘    └───────────────────┬────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┼────────────────────────┘
                                                                           │
                                                                           ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                      ORCHESTRATION LAYER                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                            Ingestion Service (Kubernetes Deployment)                        │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │ │
│  │  │   Pod 1     │  │   Pod 2     │  │   Pod 3     │  │   Pod N     │  │  HPA/KEDA   │       │ │
│  │  │  (FastAPI)  │  │  (FastAPI)  │  │  (FastAPI)  │  │  (FastAPI)  │  │ (Autoscaler)│       │ │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘       │ │
│  └─────────────────────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┬────────────────────────┘
                                                                           │
                          ┌────────────────────────────────────────────────┼────────────────────────┐
                          │                                                │                        │
                          ▼                                                ▼                        ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                        STORAGE LAYER                                             │
│                                                                                                  │
│  ┌────────────────────┐    ┌────────────────────┐    ┌────────────────────────────────────────┐  │
│  │     S3 Bucket      │    │     S3 Bucket      │    │           Redis Cluster               │  │
│  │   (Input Images)   │    │  (Output Images)   │    │    (Job State, Deduplication)        │  │
│  │                    │    │                    │    │                                        │  │
│  │  - Lifecycle rules │    │  - CDN integration │    │  - TTL-based expiration               │  │
│  │  - Versioning      │    │  - Replication     │    │  - Pub/Sub for events                 │  │
│  └────────────────────┘    └────────────────────┘    └────────────────────────────────────────┘  │
│                                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                              PostgreSQL (Job Metadata)                                     │  │
│  │   - job_id, status, timestamps, input_path, output_path, operations, retry_count          │  │
│  └────────────────────────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
                                                                           │
                                                                           │ job dispatch
                                                                           ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                      MESSAGE QUEUE LAYER                                         │
│                                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                              Amazon SQS / RabbitMQ Cluster                                  │ │
│  │                                                                                             │ │
│  │   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │ │
│  │   │   resize    │  │  compress   │  │  watermark  │  │     ocr     │  │  Dead Letter    │  │ │
│  │   │   queue     │  │   queue     │  │   queue     │  │   queue     │  │    Queue        │  │ │
│  │   │             │  │             │  │             │  │             │  │                 │  │ │
│  │   │ Priority: 1 │  │ Priority: 2 │  │ Priority: 2 │  │ Priority: 3 │  │ (Failed jobs)   │  │ │
│  │   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └─────────────────┘  │ │
│  │          │                │                │                │                              │ │
│  └──────────┼────────────────┼────────────────┼────────────────┼──────────────────────────────┘ │
└─────────────┼────────────────┼────────────────┼────────────────┼────────────────────────────────┘
              │                │                │                │
              ▼                ▼                ▼                ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                      WORKER POOL LAYER                                           │
│                                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                     Kubernetes Worker Deployments (Spot + On-Demand Mix)                   │ │
│  │                                                                                             │ │
│  │  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────────────────┐  │ │
│  │  │  Resize Workers      │  │  Compress Workers    │  │  Watermark Workers               │  │ │
│  │  │  ────────────────    │  │  ────────────────    │  │  ────────────────────            │  │ │
│  │  │  Replicas: 20-100    │  │  Replicas: 15-80     │  │  Replicas: 10-50                 │  │ │
│  │  │  CPU: 2 cores        │  │  CPU: 2 cores        │  │  CPU: 1 core                     │  │ │
│  │  │  Memory: 4GB         │  │  Memory: 4GB         │  │  Memory: 2GB                     │  │ │
│  │  │  90% Spot instances  │  │  90% Spot instances  │  │  90% Spot instances              │  │ │
│  │  └──────────────────────┘  └──────────────────────┘  └──────────────────────────────────┘  │ │
│  │                                                                                             │ │
│  │  ┌──────────────────────────────────────────────────────────────────────────────────────┐  │ │
│  │  │  OCR Workers (GPU-enabled)                                                           │  │ │
│  │  │  ────────────────────────────                                                        │  │ │
│  │  │  Replicas: 5-30  │  GPU: T4/A10  │  Memory: 16GB  │  70% Spot (GPU spot available)   │  │ │
│  │  └──────────────────────────────────────────────────────────────────────────────────────┘  │ │
│  │                                                                                             │ │
│  │  ┌─────────────────────────────────────────────────────────────────────────────┐           │ │
│  │  │  KEDA Autoscaler: Scale based on queue depth (target: <100 messages/worker) │           │ │
│  │  └─────────────────────────────────────────────────────────────────────────────┘           │ │
│  └─────────────────────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Details

### 1. Ingestion Layer

**API Gateway & Rate Limiting**
```python
# Rate limiting configuration (Redis-based)
RATE_LIMITS = {
    "free_tier": "100/minute",
    "standard": "1000/minute",
    "enterprise": "10000/minute"
}
```

**Ingestion Service (FastAPI)**
```python
from fastapi import FastAPI, UploadFile, BackgroundTasks
from pydantic import BaseModel
import boto3
import uuid

class ImageJob(BaseModel):
    job_id: str
    operations: list[str]  # ["resize", "compress", "watermark", "ocr"]
    params: dict
    priority: int = 1
    callback_url: str | None = None

@app.post("/v1/process")
async def submit_job(
    file: UploadFile,
    operations: list[str],
    params: dict,
    background_tasks: BackgroundTasks
):
    job_id = str(uuid.uuid4())

    # 1. Upload to S3 (async, presigned URL for large files)
    s3_key = f"input/{job_id}/{file.filename}"
    await upload_to_s3(file, s3_key)

    # 2. Create job record in PostgreSQL
    job = await create_job_record(job_id, operations, params, s3_key)

    # 3. Dispatch to appropriate queues
    background_tasks.add_task(dispatch_to_queues, job)

    return {"job_id": job_id, "status": "queued"}
```

### 2. Message Queue Design

**Queue Structure (SQS with FIFO for ordering)**

| Queue Name | Purpose | Visibility Timeout | Max Retries | DLQ |
|------------|---------|-------------------|-------------|-----|
| `img-resize-queue` | Resize operations | 60s | 3 | Yes |
| `img-compress-queue` | Compression | 60s | 3 | Yes |
| `img-watermark-queue` | Watermarking | 30s | 3 | Yes |
| `img-ocr-queue` | OCR (longer) | 300s | 2 | Yes |
| `img-dlq` | Failed jobs | N/A | N/A | No |

**Message Schema**
```json
{
    "job_id": "uuid",
    "operation": "resize",
    "input_path": "s3://bucket/input/uuid/image.jpg",
    "output_path": "s3://bucket/output/uuid/image_resized.jpg",
    "params": {
        "width": 800,
        "height": 600,
        "quality": 85
    },
    "retry_count": 0,
    "created_at": "2025-01-15T10:00:00Z",
    "trace_id": "span-uuid"
}
```

### 3. Worker Pool Architecture

**Base Worker Implementation**
```python
import asyncio
from abc import ABC, abstractmethod
from prometheus_client import Counter, Histogram
import structlog

logger = structlog.get_logger()

# Metrics
JOBS_PROCESSED = Counter('jobs_processed_total', 'Total jobs processed', ['operation', 'status'])
PROCESSING_TIME = Histogram('processing_duration_seconds', 'Job processing time', ['operation'])

class BaseWorker(ABC):
    def __init__(self, queue_url: str, max_concurrent: int = 10):
        self.queue_url = queue_url
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.running = True

    async def run(self):
        """Main worker loop with graceful shutdown support"""
        while self.running:
            messages = await self.poll_messages(max_messages=10)
            tasks = [self.process_with_semaphore(msg) for msg in messages]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def process_with_semaphore(self, message):
        async with self.semaphore:
            return await self.safe_process(message)

    async def safe_process(self, message):
        """Process with retry logic and metrics"""
        job = parse_message(message)

        with PROCESSING_TIME.labels(operation=self.operation_name).time():
            try:
                result = await self.process(job)
                await self.ack_message(message)
                JOBS_PROCESSED.labels(operation=self.operation_name, status='success').inc()

                # Trigger next operation in pipeline if needed
                await self.dispatch_next(job, result)

            except RetryableError as e:
                logger.warning("retryable_error", job_id=job.job_id, error=str(e))
                if job.retry_count < self.max_retries:
                    await self.requeue_with_backoff(job)
                else:
                    await self.send_to_dlq(job, e)

            except Exception as e:
                logger.error("fatal_error", job_id=job.job_id, error=str(e))
                await self.send_to_dlq(job, e)
                JOBS_PROCESSED.labels(operation=self.operation_name, status='failed').inc()

    @abstractmethod
    async def process(self, job) -> dict:
        """Override in subclasses"""
        pass


class ResizeWorker(BaseWorker):
    operation_name = "resize"

    async def process(self, job):
        # Download from S3
        image_data = await download_from_s3(job.input_path)

        # Process with Pillow/libvips (libvips is faster for high throughput)
        import pyvips
        image = pyvips.Image.new_from_buffer(image_data, "")
        resized = image.thumbnail_image(
            job.params['width'],
            height=job.params.get('height')
        )

        # Upload result
        output_buffer = resized.write_to_buffer('.jpg[Q=85]')
        await upload_to_s3(output_buffer, job.output_path)

        return {"output_path": job.output_path, "size": len(output_buffer)}


class OCRWorker(BaseWorker):
    """GPU-accelerated OCR using Tesseract or EasyOCR"""
    operation_name = "ocr"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        import easyocr
        self.reader = easyocr.Reader(['en'], gpu=True)

    async def process(self, job):
        image_data = await download_from_s3(job.input_path)

        # Run OCR (CPU-bound, run in thread pool)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self.reader.readtext(image_data)
        )

        text = " ".join([item[1] for item in result])
        confidence = sum([item[2] for item in result]) / len(result) if result else 0

        return {"text": text, "confidence": confidence}
```

### 4. Failure Handling Strategy

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          FAILURE HANDLING FLOW                              │
│                                                                             │
│   Job Arrives                                                               │
│       │                                                                     │
│       ▼                                                                     │
│   ┌───────────┐     Success    ┌──────────────────┐                        │
│   │  Process  │───────────────▶│  Mark Complete   │                        │
│   │   Job     │                │  Trigger Callback│                        │
│   └─────┬─────┘                └──────────────────┘                        │
│         │                                                                   │
│         │ Failure                                                           │
│         ▼                                                                   │
│   ┌─────────────────┐                                                       │
│   │ Retryable Error?│                                                       │
│   └────────┬────────┘                                                       │
│            │                                                                │
│     ┌──────┴──────┐                                                         │
│     │Yes          │No                                                       │
│     ▼             ▼                                                         │
│ ┌─────────┐  ┌─────────────┐                                               │
│ │ Retry < │  │ Send to DLQ │                                               │
│ │ Max?    │  │             │                                               │
│ └────┬────┘  └──────┬──────┘                                               │
│      │              │                                                       │
│  ┌───┴───┐          ▼                                                       │
│  │Yes  No│    ┌─────────────┐     ┌─────────────────────────────────────┐  │
│  ▼       ▼    │ DLQ Monitor │────▶│ Alert + Manual Review Dashboard    │  │
│ Requeue  DLQ  │ (Lambda)    │     │ - View failed jobs                 │  │
│ (exp.        └─────────────┘     │ - Retry individual/batch           │  │
│  backoff)                         │ - Analyze failure patterns         │  │
│                                   └─────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘

Retry Backoff Strategy:
  Attempt 1: Immediate
  Attempt 2: 30 seconds delay
  Attempt 3: 2 minutes delay
  After 3 failures → Dead Letter Queue
```

**Retryable vs Non-Retryable Errors**

| Retryable | Non-Retryable |
|-----------|---------------|
| S3 timeout | Invalid image format |
| Network errors | Unsupported operation |
| Rate limiting | Authentication failure |
| Temporary GPU unavailable | Malformed parameters |

### 5. Scaling Strategy

**KEDA Autoscaler Configuration**
```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: resize-worker-scaler
spec:
  scaleTargetRef:
    name: resize-worker
  minReplicaCount: 5
  maxReplicaCount: 100
  triggers:
    - type: aws-sqs-queue
      metadata:
        queueURL: https://sqs.us-east-1.amazonaws.com/xxx/img-resize-queue
        queueLength: "50"  # Scale up when >50 messages per worker
        awsRegion: us-east-1
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleDown:
          stabilizationWindowSeconds: 300  # Wait 5 min before scaling down
        scaleUp:
          stabilizationWindowSeconds: 30   # Scale up quickly
```

**Spot Instance Strategy**
```yaml
# Node pool configuration for EKS/GKE
apiVersion: karpenter.sh/v1alpha5
kind: Provisioner
metadata:
  name: spot-workers
spec:
  requirements:
    - key: karpenter.sh/capacity-type
      operator: In
      values: ["spot", "on-demand"]
    - key: node.kubernetes.io/instance-type
      operator: In
      values: ["c6i.xlarge", "c6a.xlarge", "c5.xlarge"]  # Diversify for spot availability
  limits:
    resources:
      cpu: 1000
  ttlSecondsAfterEmpty: 60

  # Spot configuration
  providerRef:
    name: default
---
# Spot interruption handling
apiVersion: v1
kind: Pod
spec:
  terminationGracePeriodSeconds: 120  # Allow job completion
  containers:
    - name: worker
      lifecycle:
        preStop:
          exec:
            command: ["/bin/sh", "-c", "sleep 90"]  # Drain gracefully
```

**Cost Optimization Summary**

| Component | Instance Type | Spot % | Est. Monthly Cost |
|-----------|---------------|--------|-------------------|
| Resize workers | c6i.xlarge | 90% | $800 |
| Compress workers | c6i.xlarge | 90% | $600 |
| Watermark workers | c6i.large | 90% | $300 |
| OCR workers (GPU) | g4dn.xlarge | 70% | $1,200 |
| Control plane | t3.medium | 0% | $150 |
| **Total** | | | **~$3,050** |

### 6. Monitoring & Observability

**Key Metrics Dashboard**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     IMAGE PROCESSING PIPELINE DASHBOARD                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  THROUGHPUT                          LATENCY (p50/p95/p99)                 │
│  ┌─────────────────────────┐         ┌─────────────────────────┐           │
│  │ Current: 1,247 img/sec  │         │ Resize:    45ms/120ms   │           │
│  │ Peak:    2,103 img/sec  │         │ Compress:  80ms/200ms   │           │
│  │ ▂▃▅▆█▇▅▃▂▄▆█▇▆▅▄▃▅▆▇   │         │ Watermark: 30ms/80ms    │           │
│  └─────────────────────────┘         │ OCR:       1.2s/3.5s    │           │
│                                      └─────────────────────────┘           │
│  SUCCESS RATE                        QUEUE DEPTH                           │
│  ┌─────────────────────────┐         ┌─────────────────────────┐           │
│  │ Overall:    99.7%       │         │ Resize:    234 msgs     │           │
│  │ Resize:     99.9%       │         │ Compress:  156 msgs     │           │
│  │ Compress:   99.8%       │         │ Watermark: 42 msgs      │           │
│  │ Watermark:  99.9%       │         │ OCR:       892 msgs     │           │
│  │ OCR:        98.5%       │         │ DLQ:       12 msgs ⚠️   │           │
│  └─────────────────────────┘         └─────────────────────────┘           │
│                                                                             │
│  WORKER STATUS                       COST TRACKING                         │
│  ┌─────────────────────────┐         ┌─────────────────────────┐           │
│  │ Resize:    45/100 pods  │         │ Today:     $98.50       │           │
│  │ Compress:  32/80 pods   │         │ This Week: $612.30      │           │
│  │ Watermark: 12/50 pods   │         │ Projected: $2,890/mo    │           │
│  │ OCR:       8/30 pods    │         │ Spot Savings: 68%       │           │
│  └─────────────────────────┘         └─────────────────────────┘           │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Prometheus Metrics**
```python
# Key metrics to track
from prometheus_client import Counter, Histogram, Gauge

# Throughput
jobs_submitted = Counter('pipeline_jobs_submitted_total', 'Jobs submitted', ['operation'])
jobs_completed = Counter('pipeline_jobs_completed_total', 'Jobs completed', ['operation', 'status'])

# Latency
processing_duration = Histogram(
    'pipeline_processing_seconds',
    'Processing time',
    ['operation'],
    buckets=[.01, .05, .1, .25, .5, 1, 2.5, 5, 10]
)
queue_wait_time = Histogram('pipeline_queue_wait_seconds', 'Time in queue', ['queue'])

# Queue depth
queue_depth = Gauge('pipeline_queue_depth', 'Messages in queue', ['queue'])

# Worker status
active_workers = Gauge('pipeline_workers_active', 'Active worker pods', ['operation'])
spot_workers = Gauge('pipeline_workers_spot', 'Spot instance workers', ['operation'])
```

**Alert Rules**
```yaml
groups:
  - name: image-pipeline
    rules:
      - alert: HighQueueDepth
        expr: pipeline_queue_depth > 1000
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Queue depth exceeds threshold"

      - alert: HighFailureRate
        expr: rate(pipeline_jobs_completed_total{status="failed"}[5m]) / rate(pipeline_jobs_completed_total[5m]) > 0.05
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Failure rate above 5%"

      - alert: HighLatency
        expr: histogram_quantile(0.95, pipeline_processing_seconds_bucket) > 5
        for: 5m
        labels:
          severity: warning

      - alert: DLQAccumulating
        expr: pipeline_queue_depth{queue="dlq"} > 100
        for: 10m
        labels:
          severity: warning
```

---

## Data Flow Summary

```
1. CLIENT SUBMISSION
   Client → API Gateway → Rate Limiter → Ingestion Service

2. JOB CREATION
   Ingestion Service:
     → Upload image to S3 (input bucket)
     → Create job record in PostgreSQL
     → Dispatch messages to operation queues

3. PROCESSING
   Workers poll their respective queues:
     → Download image from S3
     → Process (resize/compress/watermark/ocr)
     → Upload result to S3 (output bucket)
     → Update job status in PostgreSQL
     → Dispatch to next queue if pipeline continues

4. COMPLETION
   Final worker:
     → Mark job complete
     → Trigger webhook callback (if configured)
     → Client can poll status or receive push notification

5. FAILURE PATH
   On failure:
     → Increment retry count
     → Requeue with exponential backoff (up to 3 times)
     → After max retries → Dead Letter Queue
     → DLQ monitor alerts operations team
```

---

## Capacity Planning

**To achieve 1000 images/second:**

| Operation | Avg Time | Workers Needed | Buffer (1.5x) |
|-----------|----------|----------------|---------------|
| Resize | 50ms | 50 | 75 |
| Compress | 100ms | 100 | 150 |
| Watermark | 30ms | 30 | 45 |
| OCR | 2000ms | 2000 | N/A (batch) |

**OCR Note:** OCR is significantly slower. For 1000/sec OCR throughput, consider:
- Batch processing (not real-time)
- GPU parallelization (multiple images per GPU)
- Async callback model (process in background, notify when done)

---

## Security Considerations

1. **Input Validation**: Verify file types, size limits, scan for malware
2. **S3 Security**: Private buckets, presigned URLs with expiration
3. **Network**: VPC isolation, no public worker endpoints
4. **Secrets**: AWS Secrets Manager for API keys, DB credentials
5. **IAM**: Least-privilege roles per component

---

## Deployment Checklist

- [ ] Set up S3 buckets with lifecycle policies
- [ ] Deploy PostgreSQL (RDS with read replicas)
- [ ] Deploy Redis cluster (ElastiCache)
- [ ] Configure SQS queues with DLQ
- [ ] Deploy Kubernetes cluster with Karpenter/KEDA
- [ ] Deploy worker pods with HPA
- [ ] Set up Prometheus + Grafana
- [ ] Configure AlertManager integrations
- [ ] Load test with realistic traffic patterns
- [ ] Implement chaos engineering tests (spot interruption, queue failures)
