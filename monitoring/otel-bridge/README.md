# Open Telemetry Protocol to Prometheus bridge for metrics

Alloy deployment that receives OTLP metrics from Open WebUI,
and allows them to be scraped through a Prometheus endpoint.

This enables Open WebUI metrics to be scraped by the K1H monitoring stack,
at  `http://openwebui-otel-bridge.openllm:9464`.

This enpoint is advertised by a `ServiceMonitor`.

To inspect the manifests:
```
mkdir manifests
kustomize build --enable-helm -o manifests
```

To deploy, until managed by Argo CD:
```
CLUSTER=`scilifelab-2-dev`
kustomize build --enable-helm | kubectl --context "${CLUSTER:?}" -n openllm apply -f-
```
