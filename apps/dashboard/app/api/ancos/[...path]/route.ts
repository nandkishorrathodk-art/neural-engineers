import { timingSafeEqual } from 'node:crypto';
import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const SESSION_COOKIE = 'ancos_owner_session';
const HOP_BY_HOP_HEADERS = [
  'connection',
  'content-encoding',
  'content-length',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
  'set-cookie',
  'accept-encoding',
];

function safeTokenMatch(supplied: string | undefined, expected: string | undefined): boolean {
  if (!supplied || !expected) return false;
  const left = Buffer.from(supplied);
  const right = Buffer.from(expected);
  return left.length === right.length && timingSafeEqual(left, right);
}

async function proxyToApi(
  request: NextRequest,
  context: { params: { path: string[] } | Promise<{ path: string[] }> },
) {
  const configuredToken = process.env.OWNER_API_TOKEN;
  const sessionToken = request.cookies.get(SESSION_COOKIE)?.value;
  if (!safeTokenMatch(sessionToken, configuredToken)) {
    return NextResponse.json({ detail: 'Owner sign-in required.' }, { status: 401 });
  }

  const routeParams = await context.params;
  const segments = routeParams.path || [];
  if (segments.some((segment) => segment === '.' || segment === '..' || !/^[A-Za-z0-9._~-]+$/.test(segment))) {
    return NextResponse.json({ detail: 'Invalid API path.' }, { status: 400 });
  }

  let upstreamBase: URL;
  try {
    upstreamBase = new URL(process.env.ANCOS_API_URL || 'http://localhost:12000');
    if (!['http:', 'https:'].includes(upstreamBase.protocol)) throw new Error('Unsupported protocol');
  } catch {
    return NextResponse.json({ detail: 'ANCOS_API_URL is not configured correctly.' }, { status: 503 });
  }

  const apiUrl = new URL(
    segments.map((segment) => encodeURIComponent(segment)).join('/') + request.nextUrl.search,
    upstreamBase.href.endsWith('/') ? upstreamBase : `${upstreamBase.href}/`,
  );
  const headers = new Headers(request.headers);
  for (const header of [
    ...HOP_BY_HOP_HEADERS,
    'authorization',
    'cookie',
    'host',
    'origin',
    'x-forwarded-for',
    'x-real-ip',
  ]) headers.delete(header);
  headers.set('Authorization', `Bearer ${configuredToken}`);

  const method = request.method.toUpperCase();
  const body = method === 'GET' || method === 'HEAD' ? undefined : await request.arrayBuffer();
  try {
    const upstream = await fetch(apiUrl, {
      method,
      headers,
      body,
      cache: 'no-store',
      redirect: 'manual',
    });
    const responseHeaders = new Headers(upstream.headers);
    for (const header of HOP_BY_HOP_HEADERS) responseHeaders.delete(header);
    responseHeaders.set('Cache-Control', 'no-store');
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch {
    return NextResponse.json({ detail: 'ANCOS API is unavailable.' }, { status: 503 });
  }
}

export const GET = proxyToApi;
export const HEAD = proxyToApi;
export const POST = proxyToApi;
export const PUT = proxyToApi;
export const PATCH = proxyToApi;
export const DELETE = proxyToApi;
