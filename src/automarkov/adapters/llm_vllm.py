"""Production vLLM runtime adapter via SSH tunnel.

SSH tunnel format:
    ssh -i <key_path> -o StrictHostKeyChecking=accept-new
        -L <local_port>:127.0.0.1:<remote_port> <user>@<host> -N

Required env vars:
    AUTOMARKOV_SSH_HOST  — SSH jump host hostname or IP
    AUTOMARKOV_SSH_PORT  — SSH port (default 22)
    AUTOMARKOV_SSH_KEY   — path to SSH private key file
    AUTOMARKOV_VLLM_USER — SSH user (optional, inferred from key if absent)

vLLM endpoint format:
    http://127.0.0.1:<local_port>/v1
    OpenAI-compatible /v1/chat/completions
    Model: Qwen3.6-27B via Qwen/Qwen3.6-35B-A3B checkpoint
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from hashlib import sha256
from typing import Any

from automarkov.contracts.llm import (
    LlmCompletionRequest,
    LlmCompletionResponseArtifact,
    LlmCompletionResult,
    LlmCompletionTrace,
    LlmProbeResult,
    LlmResponsePayload,
    LlmStartRequest,
    LlmUsage,
    LocalLlmRuntimeManifest,
    RuntimeArtifactReference,
)
from automarkov.domain.canonical import canonical_json_bytes
from automarkov.domain.models import ArtifactId, Sha256Digest
from automarkov.public import CloseResult

_HTTP_TIMEOUT = 30
_TUNNEL_READY_RETRIES = 20
_TUNNEL_READY_DELAY = 1.0


def _aid(payload_hash: str) -> ArtifactId:
    return ArtifactId(root=f"artifact_{payload_hash.removeprefix('sha256:')}")


def _sd(data: bytes) -> str:
    return "sha256:" + sha256(data).hexdigest()


def _http_get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=_HTTP_TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _http_post(url: str, body: bytes) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class ProductionVllmRuntime:
    """通过 SSH 隧道连接远程 vLLM 服务的生产级 LLM 运行时适配器。"""

    def __init__(
        self,
        *,
        ssh_host: str | None = None,
        ssh_port: int | None = None,
        ssh_key_path: str | None = None,
        ssh_user: str | None = None,
        local_port: int = 8000,
        remote_port: int = 8000,
    ) -> None:
        self._ssh_host = ssh_host or os.environ.get("AUTOMARKOV_SSH_HOST", "")
        self._ssh_port = ssh_port if ssh_port is not None else int(os.environ.get("AUTOMARKOV_SSH_PORT", "22"))
        self._ssh_key_path = ssh_key_path or os.environ.get("AUTOMARKOV_SSH_KEY", "")
        self._ssh_user = ssh_user or os.environ.get("AUTOMARKOV_VLLM_USER", "")
        self._local_port = local_port
        self._remote_port = remote_port
        self._tunnel: subprocess.Popen[bytes] | None = None
        self._manifest: LocalLlmRuntimeManifest | None = None
        self._manifest_hash: str | None = None
        self._ready = False
        self._closed = False

    # -- public API -------------------------------------------------------

    def start(self, request: LlmStartRequest) -> LlmProbeResult:
        if self._tunnel is not None:
            return self._failed("transport_failed")
        self._manifest = request.runtime_manifest
        self._manifest_hash = request.runtime_manifest_payload_hash.root
        try:
            self._start_tunnel()
        except (OSError, subprocess.SubprocessError, ValueError, TimeoutError):
            self._manifest = None
            self._manifest_hash = None
            return self._failed("transport_failed")
        return self._run_probe()

    def probe(self) -> LlmProbeResult:
        if self._manifest is None:
            return self._failed("transport_failed")
        if self._closed:
            return self._failed("transport_failed")
        if not self._tunnel_alive():
            return self._failed("transport_failed")
        return self._run_probe()

    def complete(self, request: LlmCompletionRequest) -> LlmCompletionResult:
        m = self._require_ready()
        messages = [msg.model_dump(mode="json", warnings="error") for msg in request.prompt.messages]
        body = canonical_json_bytes({
            "model": m.served_model_name, "messages": messages,
            "temperature": request.sampling.temperature_value,
            "top_p": request.sampling.top_p_value, "seed": request.sampling.seed,
            "max_tokens": request.sampling.max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        })
        status, raw = _http_post(f"{m.base_url}/chat/completions", body)
        if status != 200:
            raise RuntimeError(f"vLLM completion HTTP {status}")
        parsed = json.loads(raw)
        choice = parsed["choices"][0]
        fr: str = choice.get("finish_reason", "stop")
        if fr not in ("stop", "length", "tool_calls"):
            fr = "stop"
        resp = LlmResponsePayload(
            schema_version="automarkov.llm-response.v1",
            content=choice["message"].get("content", "") or "",
            tool_calls=(), finish_reason=fr,  # type: ignore[arg-type]
        )
        u = parsed.get("usage", {})
        usage = LlmUsage(
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            total_tokens=u.get("total_tokens", 0),
        )
        return self._build_result(request, m, resp, usage)

    def close(self) -> CloseResult:
        self._ready = False
        self._manifest = None
        self._manifest_hash = None
        self._closed = True
        self._stop_tunnel()
        return CloseResult(schema_version="automarkov.close-result.v1", closed=True)

    # -- internals --------------------------------------------------------

    def _start_tunnel(self) -> None:
        if not self._ssh_host:
            raise ValueError("AUTOMARKOV_SSH_HOST not set")
        if not self._ssh_key_path:
            raise ValueError("AUTOMARKOV_SSH_KEY not set")
        host = f"{self._ssh_user}@{self._ssh_host}" if self._ssh_user else self._ssh_host
        self._tunnel = subprocess.Popen(
            ["ssh", "-i", self._ssh_key_path, "-o", "StrictHostKeyChecking=accept-new",
             "-o", "ServerAliveInterval=30", "-o", "ExitOnForwardFailure=yes",
             "-L", f"{self._local_port}:127.0.0.1:{self._remote_port}",
             "-p", str(self._ssh_port), host, "-N"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        for _ in range(_TUNNEL_READY_RETRIES):
            time.sleep(_TUNNEL_READY_DELAY)
            if not self._tunnel_alive():
                self._stop_tunnel()
                raise OSError("SSH tunnel died during startup")
            s, _ = _http_get(f"http://127.0.0.1:{self._local_port}/health")
            if s < 500:
                return
        self._stop_tunnel()
        raise TimeoutError("vLLM not ready after tunnel established")

    def _stop_tunnel(self) -> None:
        t = self._tunnel
        if t is None:
            return
        self._tunnel = None
        try:
            t.terminate(); t.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            t.kill()
            try:
                t.wait(timeout=3)
            except (subprocess.TimeoutExpired, OSError):
                pass

    def _tunnel_alive(self) -> bool:
        return self._tunnel is not None and self._tunnel.poll() is None

    def _run_probe(self) -> LlmProbeResult:
        m = self._require_manifest()
        hp, mp, sn, fc = False, False, None, None
        s, _ = _http_get(f"{m.base_url}/health")
        if s == 200:
            hp = True
        else:
            fc = "health_failed"
        if hp:
            s, raw = _http_get(f"{m.base_url}/models")
            if s == 200:
                try:
                    sn = _extract_name(json.loads(raw), m)
                    if sn != m.served_model_name:
                        fc = "identity_mismatch"
                    else:
                        mp = True
                except (json.JSONDecodeError, KeyError, ValueError):
                    fc = "models_failed"
            else:
                fc = "models_failed"
        ready = hp and mp
        self._ready = ready
        return LlmProbeResult(
            schema_version="automarkov.llm-probe-result.v3",
            runtime_id=m.runtime_id,
            readiness_state="READY" if ready else "WAITING_RUNTIME",
            ready=ready,
            runtime_manifest_payload_hash=self._require_manifest_hash(),
            health_passed=hp, authenticated_models_passed=mp,
            authentication_enforced_passed=False, authenticated_completion_passed=False,
            served_model_name=sn,
            health_response_hash=None, missing_auth_response_hash=None,
            invalid_auth_response_hash=None, models_response_hash=None,
            canary_request_hash=None, canary_response_hash=None,
            failure_code=fc,  # type: ignore[arg-type]
            probe_evidence_artifact_id=None, probe_evidence_payload_hash=None,
        )

    def _build_result(
        self, request: LlmCompletionRequest, m: LocalLlmRuntimeManifest,
        resp: LlmResponsePayload, usage: LlmUsage,
    ) -> LlmCompletionResult:
        mh = self._require_manifest_hash()
        mr = RuntimeArtifactReference(artifact_id=_aid(mh), payload_hash=mh)
        pr = RuntimeArtifactReference(artifact_id=request.prompt_artifact_id, payload_hash=request.prompt_payload_hash.root)
        pbytes = canonical_json_bytes({"probe": m.runtime_id})
        ph = _sd(pbytes)
        por = RuntimeArtifactReference(artifact_id=_aid(ph), payload_hash=ph)
        ra = LlmCompletionResponseArtifact(
            schema_version="automarkov.llm-completion-response-artifact.v1",
            request_id=request.request_id, runtime_manifest_ref=mr,
            runtime_probe_evidence_ref=por, prompt_ref=pr, response=resp,
        )
        rbytes = canonical_json_bytes(ra.model_dump(mode="json", round_trip=True, warnings="error"))
        rrh = _sd(rbytes)
        ra_id = _aid(rrh)
        trace = LlmCompletionTrace(
            schema_version="automarkov.llm-completion-trace.v2",
            request_id=request.request_id, model_id=m.model_id,
            model_revision=m.model_revision, vllm_version=m.vllm_version,
            tokenizer_hash=m.tokenizer_hash, chat_template_hash=m.chat_template_hash,
            runtime_manifest_ref=mr, runtime_probe_evidence_ref=por, prompt_ref=pr,
            response_ref=RuntimeArtifactReference(artifact_id=ra_id, payload_hash=rrh),
            endpoint_identity_hash=m.listener_identity_hash, connection_evidence_hash=ph,
            sampling=request.sampling, usage=usage, latency_ms=0,
            finish_reason=resp.finish_reason,
        )
        tbytes = canonical_json_bytes(trace.model_dump(mode="json", round_trip=True, warnings="error"))
        return LlmCompletionResult(
            schema_version="automarkov.llm-completion-result.v3",
            response=resp, trace=trace,
            response_payload_hash=Sha256Digest(root=resp.payload_hash),
            trace_payload_hash=Sha256Digest(root=trace.payload_hash),
            response_artifact_id=ra_id, trace_artifact_id=_aid(_sd(tbytes)),
        )

    def _failed(self, code: str) -> LlmProbeResult:
        m = self._manifest
        rid = m.runtime_id if m else "runtime_unknown"
        mh = self._manifest_hash or "sha256:" + "0" * 64
        self._ready = False
        return LlmProbeResult(
            schema_version="automarkov.llm-probe-result.v3",
            runtime_id=rid,  # type: ignore[arg-type]
            readiness_state="WAITING_RUNTIME", ready=False,
            runtime_manifest_payload_hash=mh,
            health_passed=False, authenticated_models_passed=False,
            authentication_enforced_passed=False, authenticated_completion_passed=False,
            served_model_name=None,
            health_response_hash=None, missing_auth_response_hash=None,
            invalid_auth_response_hash=None, models_response_hash=None,
            canary_request_hash=None, canary_response_hash=None,
            failure_code=code,  # type: ignore[arg-type]
            probe_evidence_artifact_id=None, probe_evidence_payload_hash=None,
        )

    def _require_manifest(self) -> LocalLlmRuntimeManifest:
        if self._manifest is None:
            raise RuntimeError("not started")
        return self._manifest

    def _require_manifest_hash(self) -> str:
        if self._manifest_hash is None:
            raise RuntimeError("manifest hash unavailable")
        return self._manifest_hash

    def _require_ready(self) -> LocalLlmRuntimeManifest:
        m = self._require_manifest()
        if self._closed or not self._ready:
            raise RuntimeError("not READY")
        if not self._tunnel_alive():
            self._ready = False
            raise RuntimeError("SSH tunnel down")
        return m


def _extract_name(data: dict[str, Any], m: LocalLlmRuntimeManifest) -> str:
    for entry in data.get("data", []):
        if entry.get("id") == m.served_model_name:
            return m.served_model_name
    if data.get("data"):
        return data["data"][0].get("id", "")
    raise ValueError("no models in /v1/models")
