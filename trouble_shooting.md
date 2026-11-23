# Troubleshooting

[Overview](README.md) · [Installation & usage](install.md) · [Troubleshooting](trouble_shooting.md) · [Development](development.md)

## CPU shows “Unavailable” despite low utilization

CPU utilization is telemetry, not a loading threshold. **Free** means the slot
has no assigned model; **Unavailable** means a runtime error, not a busy CPU.
Read the error shown on the CPU hardware card and in the runtime detail panel.
An already loaded model instead occupies the slot until it is unloaded or expires.

If the error says **Legacy or differently assigned container found**, migrate it:

1. Click **Stop Ollama** to stop all registered runtimes.
2. Expand **Configuration → Model storage**. Keep the current location unchanged
   (including an empty path if using the Docker volume).
3. Tick the confirmation box and click **Save storage location**. Wait for success.
4. Click **Start Ollama**, then select **CPU** when loading a downloaded model.

This explicitly recreates managed containers with hardware-assignment labels;
it does not move or delete model files. Restarting the dashboard alone does not
migrate containers. Other errors, such as an inaccessible Docker daemon or an
unreachable Ollama API, must be resolved before the slot is usable. Do not remove
ownership checks or delete `runtime-slots.json` to bypass an error.

## GPU cannot be selected or a load fails

Only safely identifiable NVIDIA UUIDs and AMD render devices can be selected.
Check host drivers, Docker GPU support, device permissions, available system RAM,
and the selected GPU's free VRAM. Other applications can consume these resources
after a fit check. A load with no reported GPU VRAM use is unloaded and reported
as a CPU fallback rather than shown as successful GPU execution.

### Rootless Docker cannot resolve an NVIDIA CDI device

Run these checks as the account that runs Llameros and rootless Docker:

```sh
nvidia-smi -L
nvidia-ctk cdi list
docker version --format '{{.Server.Version}}'
docker info --format '{{json .CDISpecDirs}}'
journalctl --user -u docker -n 100 --no-pager
```

If `nvidia-smi` sees GPUs but Docker reports `BPF_CGROUP_DEVICE` and the NVIDIA
legacy hook, check both the Engine version and whether a CDI spec exists. Docker
Engine **29.2.0** added CDI resolution for NVIDIA `--gpus` requests such as the
ones Llameros sends. Older engines can still reach the legacy hook even when
explicit `--device nvidia.com/gpu=…` requests work. Upgrade during a maintenance
window if you want to use CDI with Llameros on an older engine.

If the Toolkit lists zero CDI devices, ask the host administrator to check
`systemctl status nvidia-cdi-refresh.service`. If the service stopped at its
`ExecCondition`, compare that condition with the installed module name in
`/lib/modules/$(uname -r)/modules.dep`; a check for `nvidia.ko:` does not match
`nvidia.ko.zst:`. An administrator can generate a spec manually, for example:

```sh
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
nvidia-ctk cdi list
```

Use `/etc/cdi` only if it is in the daemon's search list and no other NVIDIA
spec is already managed there or in `/run/cdi`. A manually generated spec must
be refreshed after driver or device changes, or replaced by working automatic
refresh. If the daemon still cannot see a valid spec, coordinate a user-daemon
restart: this can interrupt all of its running containers.

If the host sees the GPU and Docker logs `unresolvable CDI devices` followed by
an NVIDIA legacy hook `BPF_CGROUP_DEVICE` permission error, check whether the
Toolkit's generated `/run/cdi/nvidia.yaml` is accessible to the **daemon**, not
just to the shell. RootlessKit's private `/run` can miss a CDI directory created
after Docker starts. If Docker lists a user CDI directory, an administrator can
mirror the host-generated spec there atomically, before the user daemon starts
and whenever the host spec changes. Keep the mirror in sync after driver or
Toolkit updates; do not edit it by hand. Retry the existing container and check
`nvidia-smi -L` inside it. No container recreation is needed if the existing
GPU request resolves successfully. See the
[installation guide](install.md#verify-nvidia-gpus-with-rootless-docker) for
the container startup check and opt-in installer. A passing check does not
diagnose why a previously running container exited.

## Docker or Ollama is unavailable

Check the error shown in the runtime panel. The Docker CLI must reach a local
daemon accessible to the user running Llameros. Remote Docker endpoints are not
supported. Prefer rootless Docker; the example configuration refuses rootful
Docker unless you explicitly accept its risks. Do not run Llameros with sudo.

Start Ollama before downloading or loading models. Initial image download and
API readiness can take time; inspect the job's stage and error before retrying.
If readiness fails, inspect the managed container's logs. See
[runtime configuration](install.md#application-settings) before changing ports or images.

## Sign-in or certificate problems

Use your Linux login username and password. The account starting Llameros defaults to admin; other login accounts default to users. Blocked accounts cannot log in. Cross-account PAM authentication may be restricted by host policy; see the multi-user section of the installation guide.
There is no default password. Verify/trust the locally generated certificate
before submitting credentials. PAM must support ordinary username/password
authentication; locked, passwordless, expired, or MFA-only accounts may not work.
Do not weaken PAM or run as root as a workaround. Login is limited to five
attempts per minute; wait before retrying. Sessions disappear on server restart.

## Model missing, download disabled, or memory check fails

Check the selected [model storage](install.md#model-storage-in-the-gui). Changing
storage does not move existing models. A stopped Docker volume shows last-known
inventory; start Ollama to refresh it. Custom model names may be listed even when
they are not supported by official-library lifecycle controls.

Clear **CPU-only** to inspect unknown or oversized catalogue variants, then use
**Search / recheck** to refresh estimates. CPU-only filters discovery, not runtime
placement. Discovery and downloads require internet access; cloud-only manifests
without downloadable layers are unsupported. Fit estimates reserve system RAM
and check selected GPU VRAM separately; low CPU utilization does not imply a
memory fit. Other workloads and large contexts can still exhaust memory.

## Playground or client example cannot reach a model

Load a model first and select its hardware slot. After playground generation,
the five-minute keep-alive can expire; reload if it is no longer active.
Use the selected slot's API URL, not the dashboard HTTPS URL. Examples run on the
Llameros host; loopback is not directly reachable from a remote browser's machine
or an ordinary bridged container. Never send Linux credentials to Ollama.
Direct Ollama requests do not appear in dashboard token/timing totals.

## Changes did not take effect

Restart Llameros after application/configuration changes and reload the browser.
Container configuration is not automatically reapplied: stop all runtimes and
confirm/reapply storage to recreate containers for image changes. Persisted ports
need a deliberate stopped-container/registry migration. Do not delete the registry
while containers exist. Models can remain running when the dashboard exits;
**Stop Ollama** explicitly when you want all managed runtimes stopped.
