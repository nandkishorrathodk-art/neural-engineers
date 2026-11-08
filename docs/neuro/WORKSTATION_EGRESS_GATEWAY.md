# Workstation Network Boundary

## Source implementation

The compose source now defines a local workstation gateway with two functions:

- A read-only noVNC/WebSocket relay on `127.0.0.1:12003`. The restricted broker creates a random per-session websockify token, maps it to the employee container's internal VNC endpoint, and removes the mapping during approved termination. The existing per-workstation VNC password remains required.
- A Squid HTTP/HTTPS proxy on port `3128`, reachable only over each employee's private Docker network. It denies private, loopback, link-local, metadata, and non-allowlisted destinations. The initial domain list in `docker/squid-ancos.conf` covers the configured model providers and common code/package registries; operators must add domains deliberately when tasks require them.

Each workstation gets a distinct Docker bridge with `Internal=true`; only that workstation and the gateway join it. This blocks direct internet routing and employee-to-employee traffic. The broker verifies network labels and the internal flag before attaching the gateway or provisioning a workstation. It refuses to provision when the gateway is unavailable. Container ports are not published on the host. The gateway has no Docker socket and runs with dropped capabilities and no-new-privileges.

## Acceptance still required

This is source-only until deployed and exercised against a disposable Docker environment. Compose parsing and source-level checks do not establish a network security boundary. Before treating G-04/NEURO-12 as accepted, capture live receipts for all of the following:

1. An employee container cannot connect directly to public IPv4/IPv6 destinations, RFC1918/ULA, host loopback, link-local, cloud metadata, or another employee container.
2. Requests through the proxy succeed for an allowlisted domain and fail for a non-allowlisted domain; DNS answers that rebind to private or metadata addresses remain blocked.
3. A noVNC session opens only through the local gateway URL, requires the employee's VNC password, and cannot route to another employee's VNC service.
4. Approved workstation termination removes the token and its employee network while preserving its named workspace volume.
5. Gateway failure blocks provisioning and does not leave an employee desktop connected to an egress-capable network.

The running user stack was not changed as part of this source implementation. No live acceptance result is claimed here.
