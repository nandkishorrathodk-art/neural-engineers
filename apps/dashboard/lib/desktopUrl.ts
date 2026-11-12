// Employee desktops are served by the tokenized noVNC relay, which is only
// reachable on the host loopback. Remote operators reach the dashboard through a
// single externally exposed port, so rewrite the broker-provided absolute URL
// onto the same-origin ingress mount at /vnc/.
export function toIngressDesktopUrl(rawUrl: string | null | undefined): string | null {
  if (!rawUrl) return null;
  try {
    const parsed = new URL(rawUrl, 'http://localhost');
    const innerPath = parsed.searchParams.get('path') ?? 'websockify';
    const innerQuery = innerPath.includes('?') ? innerPath.slice(innerPath.indexOf('?') + 1) : '';
    const token = new URLSearchParams(innerQuery).get('token');

    const params = new URLSearchParams();
    params.set('autoconnect', 'true');
    params.set('resize', 'scale');
    params.set('path', token ? `vnc/websockify?token=${token}` : 'vnc/websockify');
    if (parsed.searchParams.get('view_only') === 'true') params.set('view_only', 'true');
    const password = parsed.searchParams.get('password');
    if (password) params.set('password', password);
    return `/vnc/vnc.html?${params.toString()}`;
  } catch {
    return rawUrl;
  }
}
