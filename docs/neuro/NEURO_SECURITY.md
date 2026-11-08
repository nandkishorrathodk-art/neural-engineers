# ANCOS NEURO security baseline

## Current controls present in repository

- `docker-compose.yml` isolates a dedicated Docker broker on an internal control network, mounts the Docker socket only into that broker, drops capabilities, applies `no-new-privileges`, and requires a broker token.
- Core API and dashboard are configured with owner-token integration; worker, verifier, and approver credentials are represented as separate settings.
- The Boss objective route has an explicit owner-identity dependency. Staffing audit records preserve the authenticated owner as objective submitter and use `BossAI` only for the separate staffing decision.
- Direct `POST /employees` creation is disabled; recruiting occurs in Boss planning for an owner-submitted objective. Legacy local bootstrap profiles now have dynamic names/IDs and remain explicit helper calls.
- Repository guidance requires restricted broker use, scoped employee workspace mounts, no host-root mounts, and approval gates for destructive operations.
- Governance, approval consumption, audit-chain, and task fencing modules/migrations exist.
- `NetworkSecurityPerimeter` performs application-level target/source checks, including cloud metadata and private-address patterns. It is not an OS packet filter or Docker egress firewall; container network policy must enforce runtime egress restrictions separately.

## Verification limits and required controls

Read-only inspection of the running Docker state found `EMPLOYEE_NETWORK` unset in the broker, the default `ancos-bridge-net` configured as `Internal=false`, and no workstation containers currently attached to that network. Under Docker bridge defaults, newly provisioned workstation containers can have outbound network access. The static `NetworkSecurityPerimeter` is only called from `WorkstationSandbox` source scanning; it does not filter packets from employee containers. A parser regression that truncated raw IPv6 literals was fixed and its unit suite passes, but it does not close this runtime egress gap.

A disposable Docker probe confirmed that an internal bridge blocked outbound HTTPS, but Docker did not publish the test container's port and localhost could not reach it. Therefore changing employee workstations to an internal network alone would break the current direct noVNC/VNC path. No employee network or running stack was changed during this check. A safe egress design must preserve authenticated local desktop streaming while blocking metadata/private destinations and unauthorized outbound traffic, then prove both properties in a disposable broker-backed runtime.

Focused production-mode API tests verify that missing credentials and worker credentials cannot submit objectives, the owner credential can, and the resulting audit actors are distinct. These tests exercise the API in-process against an isolated test database; they do not prove a deployed runtime configuration.

Before accepting autonomous execution, require negative tests for missing/invalid/replayed approvals, credential separation, employee cross-workspace access, broker command/image/mount allowlists, stale lease fencing, verifier independence, and actual network egress restrictions. Never grant employee containers Docker socket access or owner credentials. Treat LLM, retrieval, GUI-visible text, and artifacts as untrusted. Keep policy and human approval authoritative over model decisions.
